"""Quick CLI check: print live PopGo battery / status."""
from __future__ import annotations

import json
import sys

from mouse_device import PopGoMouse

try:
    from ble_battery import read_ble_battery_sync
except ImportError:
    read_ble_battery_sync = None  # type: ignore


def main() -> int:
    m = PopGoMouse()
    if read_ble_battery_sync is not None:
        ble = read_ble_battery_sync()
        if ble is not None:
            m.set_ble_battery(ble.percent, ble.device_name)
            print(f"BLE: {ble.device_name} battery={ble.percent}% addr={ble.address_hex}")
    if not m.is_present() and m.status.ble_percent is None:
        print("Mouse not found. Plug in the 2.4G USB receiver or pair via Bluetooth.")
        return 1
    s = m.refresh()
    print(
        json.dumps(
            {
                "connected": s.connected,
                "product": s.product_name,
                "battery_percent": s.battery_percent,
                "firmware_percent": s.firmware_percent,
                "voltage_mv": s.voltage_mv,
                "percent_source": s.percent_source,
                "ble_percent": s.ble_percent,
                "connection_mode": s.connection_mode,
                "is_charging": s.is_charging,
                "is_full": s.is_full,
                "power_source": s.power_source,
                "charge_label": s.charge_label,
                "charge_detail": s.charge_detail,
                "override_mode": s.override_mode,
                "dpi": s.dpi,
                "dpi_index": s.dpi_index,
                "firmware": s.firmware,
                "status_flags": s.status_flags,
                "raw_status": s.raw_status,
                "raw_state": s.raw_state,
                "error": s.last_error,
            },
            indent=2,
        )
    )
    m.close()
    return 0 if s.battery_percent is not None else 2


if __name__ == "__main__":
    sys.exit(main())
