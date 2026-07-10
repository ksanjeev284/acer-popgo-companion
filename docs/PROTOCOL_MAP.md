# 2.4 GHz HID map — Acer PopGo / OnMicro `32C2:0066`

Last deep-probed on live hardware (Windows, dongle only).  
Raw dump: `research/deep_probe_out/report.json`

## Chip family (public OnMicro docs)

OnMicro sells dual-mode (BLE + 2.4G private) mouse SoCs with **on-chip charge circuitry**, e.g.:

- **HS6621CQ-C** — mid/high dual-mode mouse, built-in charger  
- **OM6621Ex / Fx** — mid/low dual-mode mice  
- **OM6229** — low-end dual-mode + **dongle**, USB + AES-128  

PopGo’s dongle enumerates as **OnMicro `VID 32C2` `PID 0066`**, product `"2.4G Wireless"`.  
The **same USB ID** is used by other white-label mice (e.g. some “RaceGT” 2.4G dongles). So this is a **shared OEM stack**, not Acer-unique protocol documentation.

Public OnMicro pages describe silicon features (BLE 5.x, 2.4G proprietary RF, charger) — **not** the host HID command set. Host protocol is product firmware.

---

## What Windows sees over the dongle

USB composite device → several HID collections on one radio link:

| Usage page | Usage | Role |
|------------|-------|------|
| `0x0001` | `0x0006` Keyboard | Side buttons as keys (if mapped) |
| `0x0001` | `0x0002` Mouse | **Normal pointer** (X/Y/buttons/wheel) — owned by Windows |
| `0x000C` | `0x0001` Consumer | Media / consumer controls |
| `0x0001` | `0x0080` System control | Present; **no useful reports** in our listens |
| **`0xFFB5`** | `0x0001` Vendor | **Custom control** — battery / OEM status |

### Vendor channel (what our app uses)

| Property | Value |
|----------|--------|
| Usage page | `0xFFB5` |
| Report ID | **`0xB5` (181)** |
| Report size | **8 bytes** in / out |
| Feature reports | **None** (caps: Feat=0) |

Write: `[0xB5, cmd, …]`  
Read:  `[0xB5, cmd_or_type, …]`

---

## Commands that respond (full scan `0x00`–`0xFF`)

Only these commands returned data in a full scan:

| Cmd | Name (our label) | Typical response | Notes |
|-----|------------------|------------------|--------|
| **`0x01`** | **Status / battery** | `[B5, 01, flags, pct, a, b, c, d]` | **Main useful command** |
| **`0x02`** | Dump bank A | 64× chunks `[B5, 02, off, 4, d0..d3]` | All **zeros** on this unit (empty bank) |
| **`0x04`** | State | `[B5, 04, 1, 0, …]` | Stable; not a live DPI index |
| **`0x05`** | Dump bank B | 64× chunks | **Non-zero config / descriptor-like blob** |
| **`0x20`** | Identity / FW | `[B5, 20, 64, 83, …]` | Tag **`64.83`** (shown as firmware in app) |
| **`0x23`** | Info block | `[B5, 23, 66, 0, 97, 1, 138, 2]` | Fixed OEM params |
| **`0x29`** | ID A | `[B5, 29, 2, 198, 50, 90, 204, 194]` | Stable ID-like |
| **`0x2A`** | Mask / caps | `[B5, 2A, FF, FF, FF, 3F, FF, C2]` | Mostly 0xFF |
| **`0x2B`** | Level / stage? | `[B5, 2B, 5, FF, FF, 3F, FF, 80]` | Byte `5` — *could* be DPI step (unproven; did not change in idle poll) |
| **`0x2C`** | ID B | `[B5, 2C, 0, 148, 166, 161, 123, 2]` | Stable |
| **`0x2D`** | ID C | `[B5, 2D, 2, 198, 50, …]` | Related to 0x29 |
| **`0x2E`** | ID D | `[B5, 2E, 90, 204, 194, …]` | Related |

**All other command bytes:** no response (or not implemented on this firmware).

---

## Status packet `0x01` — deepest decode so far

Live stable sample:

```text
[181, 1, 1, 56, 27, 2, 14, 40]
  RID  cmd flg pct  a  b  c   d
```

| Byte | Value | Interpretation |
|------|-------|----------------|
| 0 | `0xB5` | Report ID |
| 1 | `0x01` | Command echo |
| 2 | `0x01` | Flags / state — **always 1** in all our captures (not charge LED) |
| 3 | `56` | **Firmware %** — often **stuck** for long periods |
| 4–5 | `27, 2` | Not a valid LiPo mV as LE (539) |
| 5–6 | `2, 14` | LE **`0x0E02` = 3586 mV** ≈ **3.59 V** LiPo — **used by app** |
| 6–7 | `14, 40` | Also near 3.6 V if read as BE `0x0E28` = 3624 mV |

### App behavior

1. Prefer **voltage → estimated %** when mV ∈ 3000–4300  
2. Still show **MCU raw %** when it differs (e.g. stuck 56%)  
3. Green **charge LED is local** — does **not** appear in this packet  

---

## What is *not* on the 2.4G vendor channel

Verified by probe:

| Feature | On 2.4G HID? | Where it lives |
|---------|--------------|----------------|
| Cursor move / click / wheel | Yes (standard mouse) | Windows mouse stack, **not** `0xFFB5` |
| Battery % / voltage | **Yes** (vendor) | `CMD 0x01` |
| Charge LED (green) | **No** | Local charge IC / MCU on mouse |
| Charge plugged bit | **No** | Never seen to flip |
| Software set DPI | **Not found** | DPI button on mouse only |
| Live DPI report when button pressed | **No spontaneous vendor packets** | Local LED/MCU only |
| RGB control | N/A / not exposed | — |
| Pairing / BT config over dongle | Not in responding cmds | Mouse BT is separate radio mode |
| Firmware update | Not found | — |

**Live listen (12 s)** on vendor iface while using the mouse: **0 packets**.  
Movement never goes through `0xFFB5` — only through the standard mouse collection.

---

## Config dump `0x05` (bank B)

- 64 packets × 4 data bytes ≈ 256-byte image  
- Contains embedded `0xB5 0x05` markers, masks `0xFFFF` / `0xFF3F`, and values that look like **factory calibration / descriptors**, not live battery  
- **Not fully decoded**; safe to treat as read-only OEM blob  
- Raw: `research/deep_probe_out/dump_05.bin`

Dump `0x02` (bank A) is all zeros on this unit.

---

## Same dongle ID elsewhere

Public reports of **`32c2:0066` “2.4G Wireless”** on non-Acer mice mean:

- Protocol knowledge may transfer across rebrands  
- Or firmware differs per SKU while USB ID stays shared  

No open-source host protocol for this ID was found (no libratbag/OpenRGB entry).

---

## BLE mode (paired as “Acer PopGo BT5.4”) — confirmed live

When connected over Bluetooth LE (not the dongle):

| Item | Value |
|------|--------|
| Name | `Acer PopGo BT5.4` |
| Address (example) | `F9:00:C6:02:14:01` |
| BLE VID/PID | `0x32C2` / `0x0026` (PnP: `DEV_VID&0232C2_PID&0026`) |
| **Battery Service** | **`0x180F`** |
| **Battery Level** | **`0x2A19`** — **uint8 percent, READ + NOTIFY** |
| Live reading | **100%** (while 2.4G MCU byte was stuck at 56%) |
| Device Information | `0x180A` / PnP ID `0x2A50` → VID `32C2` PID `0026` |
| HID over GATT | `0x1812` (Windows owns; characteristic enum may access-deny) |
| GAP name | `0x2A00` = `Acer PopGo BT5.4` |

**Conclusion:** Prefer **BLE Battery Level** over 2.4G firmware % when the mouse is paired over BT. Implemented in `ble_battery.py` (Windows WinRT / winsdk).

## What we can still try later (research backlog)

1. ~~**Bluetooth Battery Service**~~ **DONE** — `0x2A19` works  
2. BLE Battery Power State / Level Status if firmware adds them  
3. Subscribe to `0x2A19` notifications (prop includes Notify) instead of polling  
4. **USB-C data mode** — second USB device when plugged into PC  
5. **Long-run voltage log** on 2.4G — confirm mV tracks charge  
6. **Logic analyzer** for DPI / charge LED  
7. **OnMicro SDK / bbs** host command tables  
8. **CMD `0x2B` byte[2]** as DPI stage while pressing DPI button  

---

## Bottom line for the product

Over **2.4 GHz**, the host gets:

1. Normal mouse HID (always)  
2. A **small vendor API**: status (battery/voltage), identity, opaque dumps  

It does **not** get:

- Charge LED state  
- Reliable software DPI control  

That is why this companion focuses on **battery (voltage + %)**, tray, and OS pointer tools — the only high-value data the radio actually carries.
