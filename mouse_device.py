"""
HID communication layer for Acer PopGo wireless mouse.

Detected device:
  USB VID:PID = 32C2:0066  (OnMicro 2.4G receiver, product string "2.4G Wireless")
  Vendor usage page 0xFFB5, report ID 0xB5, 8-byte input/output reports.

Protocol (reverse-engineered):
  Write:  [0xB5, cmd, ...]
  Read:   [0xB5, cmd, ...]

  CMD 0x01 -> status: byte[3] = battery percent (0-100)
  CMD 0x04 -> device state packet (stable third byte observed as stage flag)
  CMD 0x20 -> firmware / identity packet
  CMD 0x05 -> multi-packet config dump (not fully decoded)
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

try:
    import hid
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency 'hidapi'. Install with: pip install hidapi"
    ) from exc


# Acer PopGo (OnMicro 2.4G dongle observed on this machine)
VENDOR_ID = 0x32C2
PRODUCT_ID = 0x0066
USAGE_PAGE_VENDOR = 0xFFB5
REPORT_ID = 0xB5

# Official PopGo DPI steps (from product listing)
DPI_LEVELS: tuple[int, ...] = (800, 1200, 1600, 2400, 3200, 4000, 5000, 6400)

BATTERY_CAPACITY_MAH = 500


@dataclass
class MouseStatus:
    connected: bool = False
    product_name: str = "Acer PopGo"
    battery_percent: Optional[int] = None
    dpi_index: Optional[int] = None  # 0-based into DPI_LEVELS when known
    dpi: Optional[int] = None
    firmware: Optional[str] = None
    raw_status: Optional[list[int]] = None
    raw_state: Optional[list[int]] = None
    raw_info: Optional[list[int]] = None
    last_error: Optional[str] = None
    last_update: float = field(default_factory=time.time)

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
        if p <= 10:
            return "critical"
        if p <= 20:
            return "low"
        if p <= 50:
            return "medium"
        if p <= 80:
            return "good"
        return "full"


class PopGoMouse:
    """Thread-safe reader for the Acer PopGo vendor HID interface."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._dev: Optional[hid.device] = None
        self._path: Optional[bytes] = None
        self.status = MouseStatus()
        # Soft DPI tracking: hardware button cycles levels; software mirrors it.
        self._tracked_dpi_index: Optional[int] = None

    # ------------------------------------------------------------------ open
    def find_device_info(self) -> list[dict]:
        """Prefer vendor usage page 0xFFB5; fall back to any non-boot collection."""
        all_devs = list(hid.enumerate(VENDOR_ID, PRODUCT_ID))
        vendor = [d for d in all_devs if d.get("usage_page") == USAGE_PAGE_VENDOR]
        if vendor:
            return vendor
        # Some Linux/macOS stacks report usage pages differently
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
        self._drain(0.03)
        packet = bytes([REPORT_ID] + (payload + [0] * 7)[:7])
        try:
            self._dev.write(packet)
        except Exception as exc:
            raise RuntimeError(f"HID write failed: {exc}") from exc
        time.sleep(0.012)
        return self._drain(listen)

    def _first_matching(self, pkts: list[list[int]], cmd: int) -> Optional[list[int]]:
        for p in pkts:
            if len(p) >= 4 and p[0] == REPORT_ID and p[1] == cmd:
                return p
        # Some firmwares echo without strict cmd match — accept first RID packet
        for p in pkts:
            if len(p) >= 4 and p[0] == REPORT_ID:
                return p
        return None

    # ----------------------------------------------------------------- reads
    def read_battery(self) -> Optional[int]:
        """Return battery percent 0-100, or None."""
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
            if not pkt:
                return None
            # byte[3] observed as stable battery percentage
            pct = int(pkt[3])
            if pct > 100:
                pct = min(pct, 100)
            self.status.battery_percent = pct
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
            # Note: CMD 0x04 returns a stable state packet. Byte[2] has been
            # observed as a fixed flag (not a reliable live DPI index). Sensor
            # DPI is changed with the hardware button; the app tracks the
            # active step via set_tracked_dpi_index / cycle_tracked_dpi.
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
                # bytes [2],[3] look like a version pair (observed 64, 83)
                self.status.firmware = f"{pkt[2]}.{pkt[3]}"
            return pkt

    def refresh(self) -> MouseStatus:
        """Refresh all known fields. Safe to call from a timer thread."""
        if not self.is_present():
            self.close()
            self.status.connected = False
            self.status.battery_percent = None
            self.status.last_error = "Receiver not plugged in or mouse off"
            self.status.last_update = time.time()
            return self.status

        if not self.open():
            return self.status

        self.read_battery()
        self.read_state()
        # firmware rarely changes — only fetch if missing
        if self.status.firmware is None:
            self.read_info()

        # DPI: firmware does not expose a verified live stage over HID yet.
        # Soft-track mirrors the hardware DPI button / user selection.
        if self._tracked_dpi_index is not None:
            self.status.dpi_index = self._tracked_dpi_index
            self.status.dpi = DPI_LEVELS[self._tracked_dpi_index]
        else:
            self.status.dpi_index = None
            self.status.dpi = None

        self.status.connected = True
        self.status.last_update = time.time()
        return self.status

    # ----------------------------------------------------------- DPI tracking
    def set_tracked_dpi_index(self, index: int) -> None:
        """Record which DPI step the user believes is active (hardware button)."""
        if not 0 <= index < len(DPI_LEVELS):
            raise ValueError("DPI index out of range")
        self._tracked_dpi_index = index
        self.status.dpi_index = index
        self.status.dpi = DPI_LEVELS[index]

    def cycle_tracked_dpi(self) -> int:
        """Advance soft DPI tracker (mirrors pressing the mouse DPI button)."""
        cur = self._tracked_dpi_index
        if cur is None:
            cur = self.status.dpi_index if self.status.dpi_index is not None else 0
        nxt = (cur + 1) % len(DPI_LEVELS)
        self.set_tracked_dpi_index(nxt)
        return nxt

    def get_tracked_dpi_index(self) -> Optional[int]:
        return self._tracked_dpi_index


class StatusPoller:
    """Background poller that pushes MouseStatus to a callback."""

    def __init__(
        self,
        mouse: PopGoMouse,
        on_update: Callable[[MouseStatus], None],
        interval: float = 2.0,
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
            except Exception as exc:  # keep thread alive
                self.mouse.status.last_error = str(exc)
                self.on_update(self.mouse.status)
            self._stop.wait(self.interval)
