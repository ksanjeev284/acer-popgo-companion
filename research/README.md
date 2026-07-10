# Protocol research tools

Scripts used while reverse-engineering the Acer PopGo / OnMicro HID + BLE stack.

## 2.4 GHz dongle (`32C2:0066`)

| Script | Purpose |
|--------|---------|
| `probe_hid.py` | Enumerate collections, caps, feature reports |
| `probe_protocol.py` | Broad command scan |
| `probe_map.py` | Structured command map |
| `probe_dpi.py` | Battery stability + DPI write experiments |
| `deep_probe.py` | Full cmd scan + dumps → `deep_probe_out/` |

## Bluetooth LE (`32C2:0026` / Acer PopGo BT5.4)

| Script | Purpose |
|--------|---------|
| `probe_ble.py` | bleak scan (optional) |
| `probe_ble_winrt.py` | WinRT battery read |
| `deep_ble.py` | Full GATT dump + battery notify test → `deep_ble_out/ble_report.json` |
| `deep_ble2.py` | Dual-radio, PnP, ads, HID access → `ble_deep2_report.json` |
| `deep_ble3.py` | Report descriptors decode, vendor-while-BLE, dump05 → `ble_deep3_report.json` |

## App integration

- `../ble_battery.py` — production WinRT reader (notify + poll)
- `../cli_status.py` / `../app.py` — prefer BLE % when connected

These are **developer tools**, not part of the end-user install. Prefer `python cli_status.py` or `python app.py` for normal use.

Canonical write-up: [`../docs/PROTOCOL_MAP.md`](../docs/PROTOCOL_MAP.md).
