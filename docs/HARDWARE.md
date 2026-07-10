# Acer PopGo — what the hardware actually supports

This document summarizes **product documentation**, **USB/HID reverse engineering on a real PopGo (VID `32C2` / PID `0066`)**, and **why some features cannot be controlled from a PC app**.

## Product identity

| Item | Value |
|------|--------|
| Marketing name | Acer PopGo Wireless Mouse |
| Example model / ASIN | `ZC.A01SI.3ZD` (Amazon), dual-mode 2.4G + BT 5.4 |
| OEM / manufacturer (Amazon) | FORCETAKE INCORPORATED (Xizhi, New Taipei) — white-label OEM rebranded Acer |
| RF chip / dongle USB ID | Beijing **OnMicro** — `VID_32C2` `PID_0066`, product string `"2.4G Wireless"` / `"2.4G Mouse"` |
| BLE mode USB/PnP ID | Same vendor — `VID_32C2` `PID_0026`, name **`Acer PopGo BT5.4`** |
| Battery | Built-in **500 mAh** Li-ion, USB-C charge cable in box |
| DPI steps (listed) | **800 / 1200 / 1600 / 2400 / 3200 / 4000 / 5000 / 6400** |
| Official PC software | **None** (no Acer driver for this SKU; plug-and-play only) |

Public listings (Amazon/JioMart/etc.) advertise dual-mode connectivity, silent clicks, and 8 DPI levels. They do **not** advertise:

- Live battery % on the PC  
- Software DPI editor  
- Software RGB / LED control  
- An official companion app  

## Green LED when charging

### What you see on the mouse

When you plug in USB-C, a **green LED turns on**. That is **local hardware** on the mouse:

- Driven by the charge controller / MCU **on the mouse PCB**
- Means “power present / charging” (typical OEM behaviour: solid while charging, often off or different when full)

### What the PC sees

We probed the **same HID status report** with the cable **plugged and unplugged**:

```text
CMD 0x01  →  [0xB5, 0x01, 0x01, battery%, ?, ?, ?, ?]
```

- `battery%` is reported (useful — this app reads it)
- The state byte is **always `0x01`** in our captures
- **No bit flips** when the green LED turns on
- **No extra USB device** appears when charging (charge path is power-only / no data interface to Windows)

**Conclusion:** The green LED is **not exposed to the computer**. No app — official or community — can “see” that LED over the 2.4 GHz dongle. The LED is for **your eyes**, not for software.

That is normal for this price class of dual-mode OEM mice (many Zebronics/Portronics/“Acer” rebrands work the same way).

## Why software cannot set DPI (today)

DPI is changed with the **physical DPI button** on the mouse. Listings say “cycle through sensitivity settings.”

HID reverse engineering found:

| Interface | Role |
|-----------|------|
| Mouse (usage 0x02) | Standard pointer movement (Windows owns this) |
| Keyboard / consumer | Side buttons / media keys |
| System controller (0x80) | No useful battery/LED reports in our probes |
| Vendor page `0xFFB5` | Custom 8-byte I/O, report ID `0xB5` |

Useful vendor command:

- **`B5 01 …` → status packet**  
  - Byte `[3]`: firmware “percent” — **often freezes** (observed stuck at **56%** for long periods)  
  - Bytes mid-packet: sometimes a **LiPo voltage in mV** (e.g. `0x0E02` ≈ 3.59 V)  
  - This app **prefers voltage → estimated %** when mV is in the 3.0–4.3 V range, and still shows the raw MCU % for comparison  

Not found after extensive write scans:

- A stable **read current DPI index** command  
- A **write DPI** command that changes the sensor (no LED change, no cursor sensitivity change)

So DPI is almost certainly handled **entirely inside the mouse MCU**, with the button and local LED only. The dongle just forwards normal mouse packets.

**Without a leaked OEM protocol or official app**, software DPI control is not reverse-engineerable from battery status alone. Future work would need a logic analyzer / firmware dump — out of scope for a safe user-space app.

## Bluetooth LE mode (deep findings)

When the mouse is paired as **Acer PopGo BT5.4** (`32C2:0026`):

| What | Result |
|------|--------|
| GATT services | Only standard 5: GAP, GATT, Device Info, **Battery**, HID |
| Battery | **`0x2A19` = true percent** (Read + Notify) — best SOC source |
| Charge bit | **Not present** (no `0x2A1A` / Level Status) |
| Vendor battery HID `0xFFB5` | **Not on BLE** — dongle-only |
| Mouse report map | Same as dongle (8 buttons, 12-bit X/Y, wheel, AC Pan) |
| Dual-radio | Dongle stays plugged but **vendor cmds go silent** while mouse is on BT |

Full GATT/HID map: [`PROTOCOL_MAP.md`](PROTOCOL_MAP.md).

## What this project *can* do (and is valuable)

| Feature | Status |
|---------|--------|
| Live battery % | **Works** via HID (Acer ships no app for this) |
| Tray / low-battery toast | **Works** |
| DPI step list + manual mark after button press | **Works** (tracks what you set on hardware) |
| Windows pointer speed | **Works** (OS setting, not sensor DPI) |
| Charging cable UI switch | **Works** (user marks cable; PC cannot auto-detect LED) |
| Auto charging from green LED | **Impossible** over this dongle |
| Software DPI change | **Not supported by exposed protocol** |

So the project is **not** a full Synapse/G HUB clone — that would require hardware Acer never exposed. It **is** the missing battery companion + desktop utilities for a mouse that officially has **zero** software.

## Comparison: mice that *do* software charging/DPI

| Class | Example | Why software works |
|-------|---------|-------------------|
| Gaming brands | Logitech G, Razer, SteelSeries | Documented/private HID + official host app |
| OEM budget dual-mode | PopGo, many “2.4G + BT” rebrands | LED + DPI local only; battery sometimes over RF |

PopGo is the second class.

## Honest LED / indicator guide (hardware)

Based on product class + user observation (PopGo):

| LED (on mouse) | Typical meaning |
|----------------|-----------------|
| **Green solid** (USB-C plugged) | Charging / power present |
| Green off after charge | Often full or charge circuit idle (model-dependent) |
| Red flash (some Acer SKUs) | Low battery (listed on other Acer mice; confirm on yours) |
| DPI button + LED blinks | DPI step change (count flashes / colour if multi-colour) |

These are **on-device only**. This app cannot mirror them unless the MCU sends a matching HID report (it does not, for charge LED).

## If you need full software control

Choose a mouse that ships with:

- Official Windows software, or  
- A known open reverse-engineered protocol (OpenRGB/libratbag-style devices)

PopGo is designed as **plug-and-play office/portable** hardware, not a programmable gaming peripheral.

## References

- Amazon product listing PopGo (dual mode, 500 mAh, 8 DPI, box: mouse + manual + charge cable) — model `ZC.A01SI.3ZD`  
- USB ID: OnMicro `32C2:0066` observed on Windows HID  
- In-repo research probes: `research/probe_*.py`  
- Companion app HID layer: `mouse_device.py`
