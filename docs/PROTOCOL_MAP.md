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

## BLE mode (paired as “Acer PopGo BT5.4”) — deep map

Live dumps: `research/deep_ble_out/ble_report.json`, `ble_deep2_report.json`, `ble_deep3_report.json`

### Identity

| Item | Value |
|------|--------|
| GAP name `0x2A00` | `Acer PopGo BT5.4` (29-byte buffer, NUL-padded) |
| Address (this unit) | `F9:00:C6:02:14:01` (`BTHLE\Dev_f900c6021401`) |
| Appearance `0x2A01` | `0x03C2` — **HID Mouse** |
| PnP ID `0x2A50` | source=**USB (2)**, VID **`0x32C2`**, PID **`0x0026`**, rev **`0x0100`** |
| HID product string | `Acer PopGo BT5.4` |
| Windows category | `Input.Mouse` |
| Secure bonding | `was_secure_connection_used_for_bonding` available on WinRT |

### Complete GATT table (UNCACHED)

Exactly **five** primary services — **no vendor 128-bit UUIDs**, no OnMicro custom GATT:

| Service | UUID | Access | Characteristics |
|---------|------|--------|-----------------|
| Generic Access | `0x1800` | OK | `0x2A00` name, `0x2A01` appearance, `0x2A04` PPCP |
| Generic Attribute | `0x1801` | OK | `0x2A05` Service Changed (Indicate) only |
| Device Information | `0x180A` | OK | **`0x2A50` PnP ID only** — no Model / FW / Manufacturer strings |
| **Battery** | **`0x180F`** | OK | **`0x2A19` Level** Read+Notify (+ CCCD `0x2902`) |
| HID over GATT | `0x1812` | **AccessDenied (3)** | Windows Bluetooth stack owns HOGP |

**Not present on this firmware:**

- Battery Power State `0x2A1A` / Level Status `0x2A1B` → **no software charge bit over BLE either**
- Model Number, Serial, FW/HW/SW revision, Manufacturer Name
- Any custom OEM service for DPI / LED / charge

### Preferred connection parameters (`0x2A04`)

| Field | Raw | Meaning |
|-------|-----|---------|
| Interval min/max | `0x0006` / `0x0006` | **7.5 ms** (aggressive mouse interval) |
| Slave latency | `0x002C` = 44 | Skip up to 44 connection events when idle |
| Supervision timeout | `0x00D8` | **2160 ms** |

Power-friendly HID profile: low latency when moving, long sleep when still.

### Battery Level (`0x2A19`) — best SOC source

| Property | Value |
|----------|--------|
| Format | **uint8 percent** (live sample **100** = `0x64`) |
| Properties | **Read + Notify** |
| CCCD write | **SUCCESS** — notifications work |
| Notify behaviour | Device **does send** notify events (observed; rate is low / on change + occasional refresh) |
| vs 2.4G | BLE % stays accurate while dongle MCU byte can freeze (e.g. 56%) |

**App policy:** Prefer BLE `0x2A19` over 2.4G firmware % when a fresh GATT reading exists (`ble_battery.py`).

### HOGP / HID over BLE (what Windows exposes)

GATT characteristics under `0x1812` are **locked** (`request_access` / `get_characteristics` → AccessDenied).  
Windows still re-exports the same logical HID collections via **PnP / hidapi**:

| Collection | BLE `32C2:0026` | Dongle `32C2:0066` |
|------------|-----------------|---------------------|
| Mouse `0x01/0x02` | **Yes** (Report ID **1**) | **Yes** (Report ID **2**) |
| Consumer `0x0C/0x01` | Yes (RID **2**) | Yes (RID **1**) |
| Keyboard `0x01/0x06` | Yes (RID **3**) | Yes (no RID / MI_00) |
| System control `0x01/0x80` | **No** | Yes (RID **3**) |
| **Vendor `0xFFB5`** | **No** | **Yes** (RID **`0xB5`**) |

**Critical:** Battery / OEM vendor channel **`0xFFB5` does not exist on BLE.**  
Over Bluetooth you get **standard HOGP mouse + GATT Battery only**.

### Mouse report map (identical body on both radios)

Decoded from `get_report_descriptor()` (files under `research/deep_ble_out/rd_*.bin`):

- **8 button bits** (Usage Page Button 1–8)
- **12-bit relative X/Y**, logical range **−2047…2047**
- **8-bit vertical wheel** (−127…127)
- **AC Pan** Consumer `0x0238` — **horizontal scroll** (−127…127)

Dongle vs BLE mouse descriptor: **byte-identical except Report ID** (`0x02` dongle / `0x01` BLE).

### Vendor page descriptor (dongle only) — full decode

```text
UsagePage(0xFFB5) Usage(0x01) Collection(Application)
  ReportID(0xB5)
  Usage(0x02) ReportSize(8) ReportCount(7) Input  (Data,Var,Abs)
  Usage(0x02) ReportSize(8) ReportCount(7) Output (Data,Var,Abs)
End Collection
```

Confirms: **7+1 payload bytes**, **Input+Output only**, **zero Feature reports**.  
No hidden feature channel for DPI/charge.

### Dual-radio behaviour (observed live)

| State | What works |
|-------|------------|
| Dongle plugged + mouse on **2.4G** | Vendor `0xFFB5` CMD `0x01` responds (battery/voltage) |
| Dongle plugged + mouse on **BLE** | HID devices for **both** IDs still enumerate, but **vendor writes get no replies** (RF link is BLE; dongle is idle) |
| BLE only | GATT Battery `100%` works; no FFB5 |

So: **you cannot use dongle vendor battery while the mouse is in BT mode.** Use BLE GATT instead.

### Config dump `0x05` ↔ BLE identity

Bank B contains the same ID fragment seen on 2.4G CMD `0x29` / `0x2C`:

- Signature bytes `02 C6 32 … 5A CC C2` at offsets in `dump_05.bin`  
- Ties dongle NVRAM blob to OnMicro identity family used on BLE PnP (`32C2`)

---

## What we can still try later (research backlog)

1. ~~**Bluetooth Battery Service**~~ **DONE** — `0x2A19` read + notify  
2. ~~Full GATT service map~~ **DONE** — only 5 standard services  
3. ~~HID report maps dongle + BLE~~ **DONE** — saved under `research/deep_ble_out/`  
4. ~~Vendor RD proves no Feature reports~~ **DONE**  
5. BLE Battery Power State — **not implemented in firmware**  
6. **USB-C data mode** — second USB device when plugged into PC (still never seen)  
7. **Long-run voltage log** on 2.4G while discharging  
8. **Logic analyzer** for DPI / charge LED on the PCB  
9. **OnMicro SDK / bbs** host command tables  
10. **CMD `0x2B` byte[2]** while pressing DPI on 2.4G-only link 

---

## Bottom line for the product

Over **2.4 GHz**, the host gets:

1. Normal mouse HID (always)  
2. A **small vendor API**: status (battery/voltage), identity, opaque dumps  

It does **not** get:

- Charge LED state  
- Reliable software DPI control  

That is why this companion focuses on **battery (voltage + %)**, tray, and OS pointer tools — the only high-value data the radio actually carries.
