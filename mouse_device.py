"""
HID communication layer for Acer PopGo wireless mouse.

VID:PID 32C2:0066 · vendor page 0xFFB5 · report ID 0xB5

CMD 0x01 status (observed):
  [0xB5, 0x01, state, percent, ...]
  state  often 0x01 while on battery over 2.4G
  percent 0–100

Charging is inferred from:
  1) status state byte (enum)
  2) sticky battery trend (any 1% rise/fall between polls)
  3) optional user override from the UI
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Literal, Optional, Tuple

try:
    import hid
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency 'hidapi'. Install with: pip install hidapi"
    ) from exc


VENDOR_ID = 0x32C2
PRODUCT_ID = 0x0066
USAGE_PAGE_VENDOR = 0xFFB5
REPORT_ID = 0xB5

DPI_LEVELS: tuple[int, ...] = (800, 1200, 1600, 2400, 3200, 4000, 5000, 6400)
BATTERY_CAPACITY_MAH = 500
LOW_BATTERY_PERCENT = 10

PowerMode = Literal["auto", "charging", "battery", "full"]
PowerSource = Literal["charging", "battery", "full", "unknown"]


@dataclass
class MouseStatus:
    connected: bool = False
    product_name: str = "Acer PopGo"
    battery_percent: Optional[int] = None
    is_charging: Optional[bool] = None
    is_full: bool = False
    power_source: PowerSource = "unknown"
    charge_label: str = "—"
    charge_detail: str = ""  # how we decided (trend / status / override)
    dpi_index: Optional[int] = None
    dpi: Optional[int] = None
    firmware: Optional[str] = None
    status_flags: Optional[int] = None
    raw_status: Optional[list[int]] = None
    raw_state: Optional[list[int]] = None
    raw_info: Optional[list[int]] = None
    last_error: Optional[str] = None
    last_update: float = field(default_factory=time.time)
    override_mode: PowerMode = "auto"

    @property
    def battery_label(self) -> str:
        if self.battery_percent is None:
            return "—"
        return f"{self.battery_percent}%"

    @property
    def dpi_label(self) -> str:
        if self.dpi is not None:
            return f"{self.dpi} DPI"
        if self.dpi_index is not None and 0 <= self.dpi_index < len(DPI_LEVELS):
            return f"{DPI_LEVELS[self.dpi_index]} DPI"
        return "Unknown (use DPI button)"

    @property
    def battery_level_name(self) -> str:
        p = self.battery_percent
        if p is None:
            return "unknown"
        if self.is_full or p >= 100:
            return "full"
        if p <= LOW_BATTERY_PERCENT:
            return "critical"
        if p <= 20:
            return "low"
        if p <= 50:
            return "medium"
        if p <= 80:
            return "good"
        return "high"


class PopGoMouse:
    """Thread-safe reader for the Acer PopGo vendor HID interface."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._dev: Optional[hid.device] = None
        self._path: Optional[bytes] = None
        self.status = MouseStatus()
        self._tracked_dpi_index: Optional[int] = None
        self._battery_history: Deque[Tuple[float, int]] = deque(maxlen=60)
        self._last_percent: Optional[int] = None
        # Sticky inferred mode from trend (survives flat periods)
        self._sticky_charging: Optional[bool] = None
        self._override: PowerMode = "auto"

    # ------------------------------------------------------------------ open
    def find_device_info(self) -> list[dict]:
        all_devs = list(hid.enumerate(VENDOR_ID, PRODUCT_ID))
        vendor = [d for d in all_devs if d.get("usage_page") == USAGE_PAGE_VENDOR]
        if vendor:
            return vendor
        fallback = [
            d
            for d in all_devs
            if d.get("usage_page", 0) >= 0xFF00
            or (d.get("interface_number") == 1 and d.get("usage") not in (0x02, 0x06))
        ]
        return fallback or all_devs

    def is_present(self) -> bool:
        return bool(self.find_device_info())

    def open(self) -> bool:
        with self._lock:
            if self._dev is not None:
                return True
            matches = self.find_device_info()
            if not matches:
                self.status.connected = False
                self.status.last_error = "Mouse dongle not found (VID 32C2 / PID 0066)"
                return False
            info = matches[0]
            dev = hid.device()
            try:
                dev.open_path(info["path"])
                dev.set_nonblocking(True)
            except Exception as exc:
                self.status.connected = False
                self.status.last_error = f"Open failed: {exc}"
                return False
            self._dev = dev
            self._path = info["path"]
            name = info.get("product_string") or "2.4G Wireless"
            self.status.product_name = f"Acer PopGo ({name})"
            self.status.connected = True
            self.status.last_error = None
            return True

    def close(self) -> None:
        with self._lock:
            if self._dev is not None:
                try:
                    self._dev.close()
                except Exception:
                    pass
            self._dev = None
            self._path = None
            self.status.connected = False

    # ---------------------------------------------------------------- protocol
    def _drain(self, timeout: float = 0.05) -> list[list[int]]:
        assert self._dev is not None
        pkts: list[list[int]] = []
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                data = self._dev.read(64)
            except Exception:
                break
            if data:
                pkts.append(list(data))
                t0 = time.time()
            else:
                time.sleep(0.002)
        return pkts

    def _query(self, payload: list[int], listen: float = 0.12) -> list[list[int]]:
        assert self._dev is not None
        self._drain(0.04)
        packet = bytes([REPORT_ID] + (payload + [0] * 7)[:7])
        try:
            self._dev.write(packet)
        except Exception as exc:
            raise RuntimeError(f"HID write failed: {exc}") from exc
        time.sleep(0.015)
        return self._drain(listen)

    def _first_matching(self, pkts: list[list[int]], cmd: int) -> Optional[list[int]]:
        for p in pkts:
            if len(p) >= 4 and p[0] == REPORT_ID and p[1] == cmd:
                return p
        for p in pkts:
            if len(p) >= 4 and p[0] == REPORT_ID:
                return p
        return None

    # ------------------------------------------------------- charge helpers
    def set_power_override(self, mode: PowerMode) -> None:
        """UI override: auto | charging | battery | full."""
        with self._lock:
            self._override = mode
            self.status.override_mode = mode
            if mode != "auto" and self.status.battery_percent is not None:
                self._apply_power_result(
                    self.status.battery_percent,
                    state_hint=None,
                    source="manual override",
                )

    def get_power_override(self) -> PowerMode:
        return self._override

    def _state_byte_hint(self, state: int, cmd04_byte3: Optional[int]) -> Optional[str]:
        """
        Map firmware state bytes to a power hint.

        Observed on battery: CMD01 byte2 == 1.
        Many OEM firmwares use:
          0 = charging, 1 = discharging, 2 = charging, 3 = full
        CMD04 byte3 has been 0 while discharging — treat 1 as charging if seen.
        """
        if state in (2, 0x12, 0x22):
            return "charging"
        if state in (3, 0x13, 0x23):
            return "full"
        if state == 0:
            # ambiguous: some firmwares use 0 for charging, others unknown
            # Prefer CMD04 if available
            if cmd04_byte3 == 1:
                return "charging"
            return None
        if state == 1:
            if cmd04_byte3 == 1:
                return "charging"
            return "battery"
        # High bit often means charging
        if state & 0x80:
            return "charging"
        if state & 0x02:
            return "charging"
        if state & 0x04:
            return "full"
        return None

    def _update_sticky_from_percent(self, pct: int) -> Optional[bool]:
        """Any 1% rise → charging sticky; any 1% fall → battery sticky."""
        if self._last_percent is None:
            self._last_percent = pct
            return self._sticky_charging
        delta = pct - self._last_percent
        self._last_percent = pct
        if delta >= 1:
            self._sticky_charging = True
        elif delta <= -1:
            self._sticky_charging = False
        return self._sticky_charging

    def _apply_power_result(
        self, pct: int, state_hint: Optional[str], source: str
    ) -> None:
        override = self._override
        if override == "charging":
            self.status.is_charging = True
            self.status.is_full = pct >= 100
            self.status.power_source = "charging"
            self.status.charge_label = "Charging"
            self.status.charge_detail = source if source.startswith("manual") else "manual override"
            return
        if override == "battery":
            self.status.is_charging = False
            self.status.is_full = False
            self.status.power_source = "battery"
            self.status.charge_label = "On battery · in use"
            self.status.charge_detail = "manual override"
            return
        if override == "full":
            self.status.is_charging = False
            self.status.is_full = True
            self.status.power_source = "full"
            self.status.charge_label = "Fully charged"
            self.status.charge_detail = "manual override"
            return

        # Auto mode
        sticky = self._update_sticky_from_percent(pct)

        if pct >= 100 or state_hint == "full":
            # Full but may still be on charger
            if sticky is True or state_hint == "charging":
                self.status.is_charging = True
                self.status.is_full = True
                self.status.power_source = "charging"
                self.status.charge_label = "Charging · full"
                self.status.charge_detail = "status/trend · full"
            else:
                self.status.is_charging = False
                self.status.is_full = True
                self.status.power_source = "full"
                self.status.charge_label = "Fully charged"
                self.status.charge_detail = "100% / status full"
            return

        if sticky is True or state_hint == "charging":
            self.status.is_charging = True
            self.status.is_full = False
            self.status.power_source = "charging"
            self.status.charge_label = "Charging"
            why = []
            if sticky is True:
                why.append("level rising")
            if state_hint == "charging":
                why.append("device status")
            self.status.charge_detail = " · ".join(why) or source
            return

        if sticky is False or state_hint == "battery":
            self.status.is_charging = False
            self.status.is_full = False
            self.status.power_source = "battery"
            self.status.charge_label = "On battery · in use"
            why = []
            if sticky is False:
                why.append("level falling/stable")
            if state_hint == "battery":
                why.append("device status")
            self.status.charge_detail = " · ".join(why) or "wireless use"
            return

        # No signal yet — honest unknown rather than guessing wrong
        self.status.is_charging = None
        self.status.is_full = False
        self.status.power_source = "unknown"
        self.status.charge_label = "Detecting…"
        self.status.charge_detail = "watching battery trend — plug USB-C to test charge"

    # ----------------------------------------------------------------- reads
    def read_battery(self) -> Optional[int]:
        with self._lock:
            if not self.open():
                return None
            try:
                pkts = self._query([0x01], listen=0.12)
            except RuntimeError as exc:
                self.status.last_error = str(exc)
                self.close()
                return None
            pkt = self._first_matching(pkts, 0x01)
            self.status.raw_status = pkt
            if not pkt or len(pkt) < 4:
                return None

            state = int(pkt[2])
            self.status.status_flags = state
            pct = int(pkt[3])
            if pct > 100:
                if pct & 0x80:
                    pct = pct & 0x7F
                    forced_charge = True
                else:
                    pct = min(pct, 100)
                    forced_charge = False
            else:
                forced_charge = False

            # Supplementary CMD 0x04
            cmd04_b3: Optional[int] = None
            try:
                st = self._query([0x04], listen=0.10)
                sp = self._first_matching(st, 0x04)
                self.status.raw_state = sp
                if sp and len(sp) >= 4:
                    cmd04_b3 = int(sp[3])
            except RuntimeError:
                pass

            hint = self._state_byte_hint(state, cmd04_b3)
            if forced_charge:
                hint = "charging"

            self.status.battery_percent = pct
            self._battery_history.append((time.time(), pct))
            self._apply_power_result(pct, hint, source="hid")
            self.status.last_update = time.time()
            return pct

    def read_state(self) -> Optional[list[int]]:
        with self._lock:
            if not self.open():
                return None
            try:
                pkts = self._query([0x04], listen=0.12)
            except RuntimeError as exc:
                self.status.last_error = str(exc)
                self.close()
                return None
            pkt = self._first_matching(pkts, 0x04)
            self.status.raw_state = pkt
            return pkt

    def read_info(self) -> Optional[list[int]]:
        with self._lock:
            if not self.open():
                return None
            try:
                pkts = self._query([0x20], listen=0.12)
            except RuntimeError as exc:
                self.status.last_error = str(exc)
                self.close()
                return None
            pkt = self._first_matching(pkts, 0x20)
            self.status.raw_info = pkt
            if pkt and len(pkt) >= 4:
                self.status.firmware = f"{pkt[2]}.{pkt[3]}"
            return pkt

    def refresh(self) -> MouseStatus:
        if not self.is_present():
            self.close()
            self.status.connected = False
            self.status.battery_percent = None
            self.status.is_charging = None
            self.status.is_full = False
            self.status.power_source = "unknown"
            self.status.charge_label = "—"
            self.status.charge_detail = ""
            self.status.last_error = "Receiver not plugged in or mouse off"
            self.status.last_update = time.time()
            return self.status

        if not self.open():
            return self.status

        self.read_battery()
        if self.status.firmware is None:
            self.read_info()

        if self._tracked_dpi_index is not None:
            self.status.dpi_index = self._tracked_dpi_index
            self.status.dpi = DPI_LEVELS[self._tracked_dpi_index]
        else:
            self.status.dpi_index = None
            self.status.dpi = None

        self.status.connected = True
        self.status.override_mode = self._override
        self.status.last_update = time.time()
        return self.status

    # ----------------------------------------------------------- DPI tracking
    def set_tracked_dpi_index(self, index: int) -> None:
        if not 0 <= index < len(DPI_LEVELS):
            raise ValueError("DPI index out of range")
        self._tracked_dpi_index = index
        self.status.dpi_index = index
        self.status.dpi = DPI_LEVELS[index]

    def cycle_tracked_dpi(self) -> int:
        cur = self._tracked_dpi_index
        if cur is None:
            cur = self.status.dpi_index if self.status.dpi_index is not None else 0
        nxt = (cur + 1) % len(DPI_LEVELS)
        self.set_tracked_dpi_index(nxt)
        return nxt

    def get_tracked_dpi_index(self) -> Optional[int]:
        return self._tracked_dpi_index


class StatusPoller:
    def __init__(
        self,
        mouse: PopGoMouse,
        on_update: Callable[[MouseStatus], None],
        interval: float = 1.5,
    ) -> None:
        self.mouse = mouse
        self.on_update = on_update
        self.interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="PopGoPoller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                status = self.mouse.refresh()
                self.on_update(status)
            except Exception as exc:
                self.mouse.status.last_error = str(exc)
                self.on_update(self.mouse.status)
            self._stop.wait(self.interval)
