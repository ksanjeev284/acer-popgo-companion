# Acer PopGo Companion

**Unofficial companion app for the Acer PopGo wireless mouse** — live battery %, DPI tracker, and OS pointer tools.

Acer does not ship software for this mouse. This open-source tool talks to the 2.4 GHz USB receiver over a reverse-engineered HID protocol.

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/ksanjeev284/acer-popgo-companion)](https://github.com/ksanjeev284/acer-popgo-companion/releases)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blue.svg)](#downloads)
[![Python](https://img.shields.io/badge/python-3.10%2B-yellow.svg)](#run-from-source)

> **Not affiliated with Acer Inc.** Community project under the MIT license.

---

## Downloads

Grab pre-built binaries from the latest **[GitHub Release](https://github.com/ksanjeev284/acer-popgo-companion/releases)**:

| Platform | File |
|----------|------|
| **Windows x64** | `AcerPopGoCompanion-windows-x64.exe` |
| **Linux x64** | `AcerPopGoCompanion-linux-x64.tar.gz` |
| **macOS Intel** | `AcerPopGoCompanion-macos-x64.zip` |
| **macOS Apple Silicon** | `AcerPopGoCompanion-macos-arm64.zip` |

Releases are built automatically with [GitHub Actions](.github/workflows/release.yml) + [PyInstaller](https://pyinstaller.org/) on each version tag (`v*`).

### Windows

1. Download `AcerPopGoCompanion-windows-x64.exe`
2. Run it (SmartScreen may warn on first run → More info → Run anyway)
3. Plug in the PopGo **USB dongle** and wake the mouse

### Linux

```bash
tar -xzf AcerPopGoCompanion-linux-x64.tar.gz
# Allow non-root HID access (once):
sudo cp 99-acer-popgo.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
# Replug dongle, then:
./AcerPopGoCompanion-linux-x64
```

### macOS

1. Unzip the archive for your chip (Intel vs Apple Silicon)
2. If Gatekeeper blocks it: right-click → **Open**, or:
   ```bash
   xattr -cr AcerPopGoCompanion.app   # or the binary
   ```

---

## Features

| Feature | Details |
|--------|---------|
| **Live battery %** | Read from the mouse MCU over HID |
| **Charging status** | Shows *Charging*, *On battery · in use*, or *Fully charged* |
| **Fixed window** | Non-resizable UI sized so all controls fit |
| **DPI tracker** | 800 → 6400 (8 steps); mark the active level after the hardware DPI button |
| **Windows pointer speed** | OS slider 1–20 (Windows builds) |
| **System tray** | Close to tray; battery tooltip (bolt icon while charging) |
| **Low-battery toast** | Notification when battery hits **≤10%** while not charging |

---

## Run from source

### Requirements

- Python **3.10+**
- PopGo via **2.4 GHz USB dongle** (`VID:PID 32C2:0066`)

```bash
git clone https://github.com/ksanjeev284/acer-popgo-companion.git
cd acer-popgo-companion
python -m pip install -r requirements.txt
python app.py
```

**Windows one-click:** `run.bat`

**CLI status:**

```bash
python cli_status.py
```

### Linux packages (source)

```bash
# Debian/Ubuntu
sudo apt install python3-tk libhidapi-hidraw0 libhidapi-libusb0
```

Install the [udev rule](packaging/99-acer-popgo.rules) as shown above.

---

## Build binaries yourself

```bash
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean popgo.spec
# Output: dist/AcerPopGoCompanion[.exe]
```

Or push a tag to trigger CI:

```bash
git tag v1.1.0
git push origin v1.1.0
```

---

## How it works (protocol)

| Item | Value |
|------|--------|
| USB VID:PID | `32C2:0066` (OnMicro) |
| Product | `2.4G Wireless` |
| Vendor HID | usage page `0xFFB5`, report ID `0xB5`, 8-byte I/O |
| Battery | Write `B5 01 …` → response byte `[3]` = percent |
| DPI steps | 800 / 1200 / 1600 / 2400 / 3200 / 4000 / 5000 / 6400 |
| Battery pack | 500 mAh rechargeable |

Sensor DPI is changed with the **physical DPI button**. Software DPI *write* is not verified yet; the app tracks which step you select.

Research scripts: [`research/`](research/).

---

## Project layout

```text
app.py                 GUI (CustomTkinter) — fixed 480×700 window
mouse_device.py        HID protocol + poller
cli_status.py          One-shot JSON status
popgo.spec             PyInstaller one-file build
.github/workflows/     Multi-OS release CI
packaging/             Linux udev rule
research/              Protocol probes
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| “Not connected” | Plug in the USB receiver, move the mouse, click **Refresh** |
| Linux permission denied | Install `99-acer-popgo.rules`, replug dongle |
| macOS “damaged” / blocked | `xattr -cr` the app, or right-click → Open |
| No battery on Bluetooth | Use the **2.4 GHz dongle** — BT may not expose the vendor interface |

---

## Contributing

PRs welcome — especially confirmed DPI set commands, Bluetooth support, and packaging improvements.

## Disclaimer

Unofficial community tool. Use at your own risk. Not affiliated with Acer Inc.

## License

[MIT](LICENSE)
