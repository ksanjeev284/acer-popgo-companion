"""
HID communication layer for Acer PopGo wireless mouse.

VID:PID 32C2:0066 · vendor page 0xFFB5 · report ID 0xB5

CMD 0x01 (clean, isolated):
  [0xB5, 0x01, 0x01, percent, 0, 0, 0, 0]
  - byte[2] is always 0x01 on this firmware (NOT a charge flag)
  - byte[3] is battery percent 0–100
  - bytes[4..7] are often stale buffer garbage after other commands

Charging is NOT reported as a dedicated HID flag on the 2.4G link
(verified: status packet is identical with USB-C plugged or unplugged).

Power state:
  - Default: On battery
  - User toggle / buttons: Charging or Full
  - Helpers: if user marked Charging and % falls → back to On battery
            if % rises twice in a row → auto mark Charging (optional assist)
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

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
    charge_detail: str = ""
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
        # auto/battery = on battery; charging/full = user (or assist) selected
        self._override: PowerMode = "battery"
        self._last_percent: Optional[int] = None
        self._rise_streak: int = 0
        self._assist_enabled: bool = True  # auto flip to charging on clear rise

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
        # Longer drain avoids stale multipacket dumps polluting status
        self._drain(0.06)
        packet = bytes([REPORT_ID] + (payload + [0] * 7)[:7])
        try:
            self._dev.write(packet)
        except Exception as exc:
            raise RuntimeError(f"HID write failed: {exc}") from exc
        time.sleep(0.02)
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
    def set_power_override(self, mode: PowerMode) -> MouseStatus:
        """UI mode. Always updates status immediately (no HID required)."""
        with self._lock:
            if mode == "auto":
                mode = "battery"
            self._override = mode
            # Reset rise streak so assist doesn't fight the user
            if mode == "battery":
                self._rise_streak = 0
            pct = self.status.battery_percent
            self._apply_power_state(pct, from_user=True)
            self.status.last_update = time.time()
            return self.status

    def get_power_override(self) -> PowerMode:
        return self._override

    def set_charging_cable(self, plugged_in: bool) -> MouseStatus:
        """Big UI switch: True = charging cable connected."""
        return self.set_power_override("charging" if plugged_in else "battery")

    def _note_percent_trend(self, pct: int) -> None:
        """Assist: rise → suggest/set charging; fall while charging → on battery."""
        if self._last_percent is None:
            self._last_percent = pct
            self._rise_streak = 0
            return

        delta = pct - self._last_percent
        self._last_percent = pct

        if delta <= -1:
            self._rise_streak = 0
            # Unplugged while marked charging: battery starts falling
            if self._override == "charging":
                self._override = "battery"
            return

        if delta >= 1:
            self._rise_streak += 1
            # Two consecutive rises = almost certainly on a charger
            if self._assist_enabled and self._rise_streak >= 2:
                if self._override in ("auto", "battery"):
                    self._override = "charging"
            return

        # flat — do nothing (keep current mode)

    def _apply_power_state(
        self, pct: Optional[int], from_user: bool = False
    ) -> None:
        """Apply labels from current override (+ optional trend assist)."""
        p = 0 if pct is None else int(pct)

        if not from_user and pct is not None:
            self._note_percent_trend(p)

        if self._override == "auto":
            self._override = "battery"

        self.status.override_mode = self._override

        if self._override == "charging":
            self.status.is_charging = True
            self.status.is_full = p >= 100
            self.status.power_source = "charging"
            self.status.charge_label = "Charging"
            if from_user:
                self.status.charge_detail = (
                    "Cable marked connected — turn the switch OFF when you unplug"
                )
            else:
                self.status.charge_detail = (
                    "Charging (battery % rose, or you marked the cable connected)"
                )
            return

        if self._override == "full":
            self.status.is_charging = False
            self.status.is_full = True
            self.status.power_source = "full"
            self.status.charge_label = "Fully charged"
            self.status.charge_detail = "Marked full / 100%"
            return

        # battery (default)
        self.status.is_charging = False
        self.status.is_full = p >= 100
        if p >= 100:
            self.status.power_source = "full"
            self.status.charge_label = "Fully charged"
            self.status.charge_detail = "battery reports 100%"
        else:
            self.status.power_source = "battery"
            self.status.charge_label = "On battery · in use"
            self.status.charge_detail = (
                "This PC cannot see the USB-C plug on PopGo. "
                "Turn ON “Charging cable connected” when you plug in."
            )

    # ----------------------------------------------------------------- reads
    def read_battery(self) -> Optional[int]:
        with self._lock:
            if not self.open():
                return None
            try:
                # Only CMD 01 — never interleave dumps here (they pollute reads)
                pkts = self._query([0x01], listen=0.14)
            except RuntimeError as exc:
                self.status.last_error = str(exc)
                self.close()
                return None

            pkt = self._first_matching(pkts, 0x01)
            self.status.raw_status = pkt
            if not pkt or len(pkt) < 4:
                return None

            # ONLY trust first 4 bytes (rest is often garbage)
            self.status.status_flags = int(pkt[2])
            pct = int(pkt[3]) & 0x7F
            if pct > 100:
                pct = 100

            self.status.battery_percent = pct
            self._apply_power_state(pct, from_user=False)
            self.status.last_update = time.time()
            return pct

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
            self.status.last_error = "Receiver not plugged in or mouse off"
            # Keep manual charge label if set; otherwise clear to battery-ish dash
            if self._override in ("auto", "battery"):
                self.status.is_charging = False
                self.status.power_source = "unknown"
                self.status.charge_label = "—"
                self.status.charge_detail = "receiver not found"
            self.status.override_mode = self._override
            self.status.last_update = time.time()
            return self.status

        if not self.open():
            self.status.override_mode = self._override
            return self.status

        self.read_battery()
        if self.status.firmware is None:
            try:
                self.read_info()
            except Exception:
                pass

        if self._tracked_dpi_index is not None:
            self.status.dpi_index = self._tracked_dpi_index
            self.status.dpi = DPI_LEVELS[self._tracked_dpi_index]
        else:
            self.status.dpi_index = None
            self.status.dpi = None

        self.status.connected = True
        # Always re-apply so charge label cannot stick from a previous mode
        self._apply_power_state(self.status.battery_percent)
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
        interval: float = 1.0,
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
