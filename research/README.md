# Protocol research tools

Scripts used while reverse-engineering the Acer PopGo / OnMicro `32C2:0066` HID interface.

| Script | Purpose |
|--------|---------|
| `probe_hid.py` | Enumerate collections, caps, feature reports |
| `probe_protocol.py` | Broad command scan |
| `probe_map.py` | Structured command map |
| `probe_dpi.py` | Battery stability + DPI write experiments |

These are **developer tools**, not part of the end-user app. Prefer `python cli_status.py` or `python app.py` for normal use.
