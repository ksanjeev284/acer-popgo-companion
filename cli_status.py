"""Quick CLI check: print live PopGo battery / status."""
from __future__ import annotations

import json
import sys

from mouse_device import PopGoMouse


def main() -> int:
    m = PopGoMouse()
    if not m.is_present():
        print("Mouse not found. Plug in the 2.4G USB receiver and power the mouse on.")
        return 1
    s = m.refresh()
    print(
        json.dumps(
            {
                "connected": s.connected,
                "product": s.product_name,
                "battery_percent": s.battery_percent,
                "is_charging": s.is_charging,
                "is_full": s.is_full,
                "power_source": s.power_source,
                "charge_label": s.charge_label,
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
