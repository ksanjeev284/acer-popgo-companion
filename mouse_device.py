"""
HID communication layer for Acer PopGo wireless mouse.

VID:PID 32C2:0066 · vendor page 0xFFB5 · report ID 0xB5

CMD 0x01 (clean, isolated):
  [0xB5, 0x01, 0x01, percent, 0, 0, 0, 0]
  - byte[2] is always 0x01 on this firmware (NOT a charge flag)
  - byte[3] is battery percent 0–100
  - bytes[4..7] are often stale buffer garbage after other commands

Charging is NOT reported as a dedicated HID flag on the 2.4G link.
We detect it by:
  1) Battery % rising between polls (sticky)
  2) Optional USB cable presence (extra 32C2 device / power path)
  3) Manual override from the UI (always wins)
"""
from __future__ import annotations

import re
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
    usb_cable_hint: bool = False

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


def detect_usb_charge_cable() -> bool:
    """
    Heuristic: if more than one unique USB 32C2 product instance is present,
    the mouse body may be USB-tethered (charging from the PC) in addition
    to the 2.4G dongle. Wall-charger-only will not show up here.
    """
    products: set[str] = set()
    try:
        for d in hid.enumerate(VENDOR_ID):
            path = d.get("path") or b""
            if isinstance(path, bytes):
                path = path.decode("utf-8", "replace")
            # Normalize to VID/PID (+ optional MI) root
            m = re.search(
                r"(VID_32C2&PID_[0-9A-Fa-f]{4})(?:&MI_\d+)?",
                path,
                re.IGNORECASE,
            )
            if m:
                products.add(m.group(1).upper())
            else:
                products.add(f"PID_{d.get('product_id', 0):04X}")
    except Exception:
        return False
    # Single dongle → one product id. Extra product id → likely wired mouse too.
    return len(products) > 1


class PopGoMouse:
    """Thread-safe reader for the Acer PopGo vendor HID interface."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._dev: Optional[hid.device] = None
        self._path: Optional[bytes] = None
        self.status = MouseStatus()
        self._tracked_dpi_index: Optional[int] = None
        self._battery_history: Deque[Tuple[float, int]] = deque(maxlen=120)
        self._last_percent: Optional[int] = None
        # None = unknown, True = charging, False = on battery (sticky)
        self._sticky_charging: Optional[bool] = None
        self._override: PowerMode = "auto"
        self._flat_polls: int = 0

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
        """UI override. Always updates status immediately (no HID required)."""
        with self._lock:
            self._override = mode
            self.status.override_mode = mode
            pct = self.status.battery_percent
            self._apply_power_state(pct if pct is not None else 0, usb_cable=False)
            # If we have no percent yet, still set labels for override
            if pct is None and mode != "auto":
                self._force_override_labels(mode)
            self.status.last_update = time.time()
            return self.status

    def get_power_override(self) -> PowerMode:
        return self._override

    def _force_override_labels(self, mode: PowerMode) -> None:
        if mode == "charging":
            self.status.is_charging = True
            self.status.is_full = False
            self.status.power_source = "charging"
            self.status.charge_label = "Charging"
            self.status.charge_detail = "manual override"
        elif mode == "battery":
            self.status.is_charging = False
            self.status.is_full = False
            self.status.power_source = "battery"
            self.status.charge_label = "On battery · in use"
            self.status.charge_detail = "manual override"
        elif mode == "full":
            self.status.is_charging = False
            self.status.is_full = True
            self.status.power_source = "full"
            self.status.charge_label = "Fully charged"
            self.status.charge_detail = "manual override"

    def _update_sticky_from_percent(self, pct: int) -> Optional[bool]:
        """
        Sticky charge inference from percent changes.
        Rise by >=1%  → charging
        Fall by >=1%  → on battery
        Flat          → keep previous sticky (or unknown)
        """
        if self._last_percent is None:
            self._last_percent = pct
            self._flat_polls = 0
            return self._sticky_charging

        delta = pct - self._last_percent
        self._last_percent = pct

        if delta >= 1:
            self._sticky_charging = True
            self._flat_polls = 0
        elif delta <= -1:
            self._sticky_charging = False
            self._flat_polls = 0
        else:
            self._flat_polls += 1
            # After many flat polls while previously charging, stay charging
            # (percent often freezes mid-charge). After long flat from unknown,
            # assume battery (wireless use).
            if self._sticky_charging is None and self._flat_polls >= 3:
                self._sticky_charging = False

        return self._sticky_charging

    def _apply_power_state(self, pct: int, usb_cable: bool) -> None:
        """Resolve final power_source from override / USB / sticky trend."""
        self.status.override_mode = self._override
        self.status.usb_cable_hint = usb_cable

        # ---- Manual override always wins ----
        if self._override == "charging":
            self.status.is_charging = True
            self.status.is_full = pct >= 100
            self.status.power_source = "charging"
            self.status.charge_label = "Charging · full" if pct >= 100 else "Charging"
            self.status.charge_detail = "manual override"
            return
        if self._override == "battery":
            self.status.is_charging = False
            self.status.is_full = False
            self.status.power_source = "battery"
            self.status.charge_label = "On battery · in use"
            self.status.charge_detail = "manual override"
            return
        if self._override == "full":
            self.status.is_charging = False
            self.status.is_full = True
            self.status.power_source = "full"
            self.status.charge_label = "Fully charged"
            self.status.charge_detail = "manual override"
            return

        # ---- Auto ----
        sticky = self._update_sticky_from_percent(pct)

        if pct >= 100:
            if sticky is True or usb_cable:
                self.status.is_charging = True
                self.status.is_full = True
                self.status.power_source = "charging"
                self.status.charge_label = "Charging · full"
                self.status.charge_detail = (
                    "USB cable" if usb_cable else "level was rising · at 100%"
                )
            else:
                self.status.is_charging = False
                self.status.is_full = True
                self.status.power_source = "full"
                self.status.charge_label = "Fully charged"
                self.status.charge_detail = "100%"
            return

        if sticky is True or usb_cable:
            self.status.is_charging = True
            self.status.is_full = False
            self.status.power_source = "charging"
            self.status.charge_label = "Charging"
            bits = []
            if sticky is True:
                bits.append("battery % rising")
            if usb_cable:
                bits.append("USB cable detected")
            self.status.charge_detail = " · ".join(bits)
            return

        if sticky is False:
            self.status.is_charging = False
            self.status.is_full = False
            self.status.power_source = "battery"
            self.status.charge_label = "On battery · in use"
            self.status.charge_detail = (
                "battery % falling or stable · no charge flag from mouse"
            )
            return

        # Unknown (first polls)
        self.status.is_charging = None
        self.status.is_full = False
        self.status.power_source = "unknown"
        self.status.charge_label = "Detecting…"
        self.status.charge_detail = (
            "Firmware has no charge bit — waiting for % change, "
            "or click Charging / On battery"
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

            usb_cable = detect_usb_charge_cable()
            self.status.battery_percent = pct
            self._battery_history.append((time.time(), pct))
            self._apply_power_state(pct, usb_cable=usb_cable)
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
            if self._override == "auto":
                self.status.is_charging = None
                self.status.is_full = False
                self.status.power_source = "unknown"
                self.status.charge_label = "—"
                self.status.charge_detail = "receiver not found"
            self.status.last_error = "Receiver not plugged in or mouse off"
            self.status.last_update = time.time()
            self.status.override_mode = self._override
            return self.status

        if not self.open():
            self.status.override_mode = self._override
            return self.status

        self.read_battery()
        if self.status.firmware is None:
            # Read info AFTER battery so we don't pollute the next battery read
            # as badly; still drain heavily in _query.
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
        self.status.override_mode = self._override
        # Re-apply override in case a concurrent path cleared labels
        if self._override != "auto" and self.status.battery_percent is not None:
            self._apply_power_state(self.status.battery_percent, usb_cable=False)
        elif self._override != "auto":
            self._force_override_labels(self._override)

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
