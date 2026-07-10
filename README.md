# Acer PopGo Companion

**Unofficial Windows companion app for the [Acer PopGo](https://www.acer.com/) wireless mouse.**

Acer does not ship software for this mouse. This open-source tool talks to the 2.4 GHz USB receiver over a reverse-engineered HID protocol so you can finally see **battery level**, track **DPI**, and tweak **Windows pointer speed**.

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-blue.svg)](#requirements)
[![Python](https://img.shields.io/badge/python-3.10%2B-yellow.svg)](#requirements)

---

## Why this exists

The PopGo is a solid rechargeable mouse (dual-mode 2.4G + BT, up to 6400 DPI, silent clicks) — but **there is no official app** to check battery or settings. The only low-battery cue is a blinking LED.

This project fills that gap.

| Feature | Details |
|--------|---------|
| **Live battery %** | Read from the mouse MCU over HID |
| **DPI tracker** | 800 → 6400 (8 steps); mark the active level after using the hardware DPI button |
| **Windows pointer speed** | OS slider 1–20 without opening Settings |
| **System tray** | Close to tray; battery tooltip |
| **Low-battery toast** | Windows notification at ≤15% |

> **Not affiliated with Acer Inc.** Community project under the MIT license.

---

## Screenshots / demo

```text
┌─────────────────────────────────────┐
│  Acer PopGo                         │
│  ● Connected · 2.4G Wireless        │
│                                     │
│  BATTERY                            │
│  56%  ████████████░░░░  500 mAh     │
│                                     │
│  SENSITIVITY (DPI)                  │
│  [800][1200][1600][2400]            │
│  [3200][4000][5000][6400]           │
│                                     │
│  WINDOWS POINTER SPEED  ──●── 10/20 │
└─────────────────────────────────────┘
```

*(Run the app on your machine for the live UI.)*

---

## Requirements

- **Windows 10 or 11**
- **Python 3.10+**
- Acer PopGo connected via the **2.4 GHz USB dongle**

| Item | Value |
|------|--------|
| USB VID:PID | `32C2:0066` (OnMicro receiver) |
| Product string | `2.4G Wireless` |
| Battery pack | 500 mAh rechargeable |
| DPI steps | 800 / 1200 / 1600 / 2400 / 3200 / 4000 / 5000 / 6400 |

> **Tip:** Prefer the USB dongle for battery readout. Bluetooth may not expose the same vendor HID interface.

---

## Install & run

### Option A — one click

```bat
git clone https://github.com/ksanjeev284/acer-popgo-companion.git
cd acer-popgo-companion
run.bat
```

`run.bat` installs dependencies if needed, then launches the GUI.

### Option B — manual

```bat
git clone https://github.com/ksanjeev284/acer-popgo-companion.git
cd acer-popgo-companion
python -m pip install -r requirements.txt
python app.py
```

### CLI status (no GUI)

```bat
python cli_status.py
```

Example output:

```json
{
  "connected": true,
  "product": "Acer PopGo (2.4G Wireless)",
  "battery_percent": 56,
  "firmware": "64.83"
}
```

---

## How it works (protocol)

The dongle exposes a vendor HID collection:

```text
Usage page  0xFFB5
Report ID   0xB5
Report size 8 bytes in / 8 bytes out  (no feature reports)
```

| Command | Packet | Meaning |
|---------|--------|---------|
| Status / battery | Write `B5 01 …` → read `B5 01 xx **PP** …` | **PP** = battery percent (0–100) |
| Device state | `B5 04 …` | Stable state packet |
| Identity | `B5 20 …` | Firmware-ish tag (e.g. `64.83`) |
| Config dump | `B5 05 …` | Multi-packet dump (partially decoded) |

Sensor DPI is changed with the **physical DPI button** on the mouse. Software DPI *write* is not verified yet — the app tracks which step you select so the UI stays in sync.

Research/probe scripts used during reverse engineering live under [`research/`](research/).

---

## Project layout

```text
app.py              GUI (CustomTkinter)
mouse_device.py     HID protocol + background poller
cli_status.py       One-shot JSON status
run.bat             Windows launcher
requirements.txt
research/           Protocol probing tools
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| “Not connected” | Plug in the USB receiver, move the mouse to wake it, click **Refresh** |
| No battery number | Confirm Device Manager shows HID devices under `VID_32C2&PID_0066` |
| Wrong mouse opened | Only `32C2:0066` is used; other brands are ignored |
| Tray icon missing | `pip install pystray Pillow` |

---

## Contributing

PRs welcome — especially:

- Confirmed DPI set/read command bytes
- Bluetooth path support
- Packaging (portable `.exe` / installer)
- Localization

1. Fork the repo  
2. Create a branch (`git checkout -b feature/…`)  
3. Commit and open a pull request  

---

## Disclaimer

This is an **unofficial** community tool. Use at your own risk. HID writes used here are limited to status queries that were validated on real hardware; aggressive probing scripts in `research/` are for developers only.

---

## License

[MIT](LICENSE) © PopGo Companion contributors
