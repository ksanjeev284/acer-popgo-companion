"""
Deeper BLE / dual-radio probe for Acer PopGo.
- RequestAccess on HID GATT service
- Advertisement watcher (manufacturer data)
- Windows HID enumerate for 32C2:0026 BLE mouse
- Side-by-side 2.4G vendor status vs BLE 0x2A19
- Read CCCD; try Battery Level Status if present
- Connection parameters from BluetoothLEDevice
"""
from __future__ import annotations

import asyncio
import json
import struct
import sys
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent / "deep_ble_out"
OUT.mkdir(exist_ok=True)

ADDR = 0xF900C6021401
VID_DONGLE, PID_DONGLE = 0x32C2, 0x0066
VID_BLE, PID_BLE = 0x32C2, 0x0026


def short_uuid(u: str) -> str:
    u = u.lower().replace("-", "")
    if u.startswith("0000") and u.endswith("00001000800000805f9b34fb"):
        return u[4:8]
    return u[:8]


async def read_bytes(ch):
    from winsdk.windows.devices.bluetooth.genericattributeprofile import (
        GattCommunicationStatus,
    )
    from winsdk.windows.storage.streams import DataReader

    resp = await ch.read_value_async()
    if resp.status != GattCommunicationStatus.SUCCESS:
        return None, int(resp.status)
    reader = DataReader.from_buffer(resp.value)
    data = bytearray(resp.value.length)
    reader.read_bytes(data)
    return bytes(data), 0


async def deep_gatt(report: dict) -> None:
    from winsdk.windows.devices.bluetooth import BluetoothCacheMode, BluetoothLEDevice
    from winsdk.windows.devices.bluetooth.genericattributeprofile import (
        GattCharacteristicProperties,
        GattClientCharacteristicConfigurationDescriptorValue,
        GattCommunicationStatus,
        GattSharingMode,
    )
    from winsdk.windows.storage.streams import DataReader

    ble = await BluetoothLEDevice.from_bluetooth_address_async(ADDR)
    if ble is None:
        report["error"] = "BLE device null — pair/connect Acer PopGo BT5.4"
        return

    report["name"] = ble.name
    report["connection_status"] = int(ble.connection_status)
    report["device_id"] = ble.device_id
    report["bluetooth_address"] = f"{ble.bluetooth_address:012X}"
    try:
        report["was_secure_connection"] = bool(ble.was_secure_connection_used_for_bonding)
    except Exception as e:
        report["was_secure_connection_err"] = str(e)

    # Device properties
    try:
        info = ble.device_information
        report["device_info"] = {
            "id": info.id,
            "name": info.name,
            "is_enabled": info.is_enabled,
            "kind": int(info.kind) if info.kind is not None else None,
        }
        # pairing
        try:
            report["pairing"] = {
                "is_paired": info.pairing.is_paired,
                "can_pair": info.pairing.can_pair,
                "protection": int(info.pairing.protection_level),
            }
        except Exception as e:
            report["pairing_err"] = str(e)
    except Exception as e:
        report["device_info_err"] = str(e)

    # Appearance etc already known — focus HID access + full char dump with descriptors
    res = await ble.get_gatt_services_async(BluetoothCacheMode.UNCACHED)
    report["gatt_status"] = int(res.status)
    services = []

    for svc in res.services:
        su = str(svc.uuid).lower()
        su_s = short_uuid(su)
        entry = {"uuid": su, "short": su_s, "session": {}, "characteristics": []}

        # Request exclusive / shared access (matters for HID)
        try:
            acc = await svc.request_access_async()
            entry["request_access"] = int(acc)
        except Exception as e:
            entry["request_access_err"] = str(e)

        try:
            # Open as shared if possible
            open_res = await svc.open_async(GattSharingMode.SHARED_READ_AND_WRITE)
            entry["open_shared"] = int(open_res)
        except Exception as e:
            entry["open_shared_err"] = str(e)

        try:
            sess = svc.session
            if sess:
                entry["session"] = {
                    "can_maintain": sess.can_maintain_connection,
                    "maintain": sess.maintain_connection,
                    "status": int(sess.session_status),
                }
                # Try maintain connection
                try:
                    sess.maintain_connection = True
                    entry["session"]["maintain_set"] = True
                except Exception as e:
                    entry["session"]["maintain_err"] = str(e)
        except Exception as e:
            entry["session_err"] = str(e)

        # Device ID for service
        try:
            entry["device_id"] = svc.device_id
            entry["attribute_handle"] = svc.attribute_handle
        except Exception:
            pass

        cres = await svc.get_characteristics_async(BluetoothCacheMode.UNCACHED)
        entry["chars_status"] = int(cres.status)
        print(f"SERVICE {su_s} access={entry.get('request_access')} chars={cres.status} n={len(cres.characteristics) if cres.status==0 else '?'}")

        if cres.status != GattCommunicationStatus.SUCCESS:
            # Try CACHED for HID
            cres2 = await svc.get_characteristics_async(BluetoothCacheMode.CACHED)
            entry["chars_status_cached"] = int(cres2.status)
            if cres2.status == GattCommunicationStatus.SUCCESS:
                chars_iter = cres2.characteristics
            else:
                services.append(entry)
                continue
        else:
            chars_iter = cres.characteristics

        for ch in chars_iter:
            cu = str(ch.uuid).lower()
            props = int(ch.characteristic_properties)
            ch_e = {
                "uuid": cu,
                "short": short_uuid(cu),
                "props": props,
                "handle": getattr(ch, "attribute_handle", None),
                "protection": int(ch.protection_level) if hasattr(ch, "protection_level") else None,
            }
            # presentation format
            try:
                pf = ch.presentation_formats
                if pf and len(pf) > 0:
                    ch_e["presentation"] = [
                        {
                            "format_type": int(p.format_type),
                            "exponent": p.exponent,
                            "unit": int(p.unit),
                            "namespace": int(p.namespace),
                            "description": int(p.description),
                        }
                        for p in pf
                    ]
            except Exception:
                pass

            if props & int(GattCharacteristicProperties.READ):
                val, st = await read_bytes(ch)
                if val is not None:
                    ch_e["value_hex"] = val.hex()
                    ch_e["value_list"] = list(val)
                    if short_uuid(cu) == "2a19" and val:
                        ch_e["battery"] = val[0]
                    if short_uuid(cu) == "2a4b":  # Report Map
                        ch_e["report_map_len"] = len(val)
                        (OUT / "hogp_report_map.bin").write_bytes(val)
                        print(f"  Report Map {len(val)} bytes saved")
                    if short_uuid(cu) == "2a4a" and len(val) >= 4:
                        ch_e["hid_info"] = {
                            "bcdHID": f"0x{val[0] | (val[1]<<8):04X}",
                            "country": val[2],
                            "flags": val[3],
                        }
                    if short_uuid(cu) == "2a4d":
                        ch_e["report_bytes"] = list(val)
                    print(f"  CHAR {short_uuid(cu)} props={props} val={val.hex()[:64]}")
                else:
                    ch_e["read_status"] = st
                    print(f"  CHAR {short_uuid(cu)} props={props} READ fail st={st}")

            # descriptors + CCCD read
            try:
                dres = await ch.get_descriptors_async(BluetoothCacheMode.UNCACHED)
                if dres.status == GattCommunicationStatus.SUCCESS:
                    descs = []
                    for d in dres.descriptors:
                        du = str(d.uuid).lower()
                        de = {"uuid": du, "short": short_uuid(du)}
                        try:
                            dr = await d.read_value_async()
                            if dr.status == GattCommunicationStatus.SUCCESS:
                                reader = DataReader.from_buffer(dr.value)
                                raw = bytearray(dr.value.length)
                                reader.read_bytes(raw)
                                de["value_hex"] = bytes(raw).hex()
                                de["value_list"] = list(raw)
                        except Exception as e:
                            de["err"] = str(e)
                        descs.append(de)
                    ch_e["descriptors"] = descs
            except Exception as e:
                ch_e["desc_err"] = str(e)

            # User description
            try:
                ud = ch.user_description
                if ud:
                    ch_e["user_description"] = ud
            except Exception:
                pass

            entry["characteristics"].append(ch_e)

        services.append(entry)

    report["services"] = services

    # Notify rate test via re-get battery char
    from winsdk.windows.devices.bluetooth.genericattributeprofile import (
        GattClientCharacteristicConfigurationDescriptorValue as CCCD,
    )

    bat_notes = []
    resb = await ble.get_gatt_services_async(BluetoothCacheMode.CACHED)
    for svc in resb.services:
        if "180f" not in str(svc.uuid).lower():
            continue
        cres = await svc.get_characteristics_async(BluetoothCacheMode.CACHED)
        for ch in cres.characteristics:
            if "2a19" not in str(ch.uuid).lower():
                continue

            def handler(sender, args):
                try:
                    reader = DataReader.from_buffer(args.characteristic_value)
                    data = bytearray(args.characteristic_value.length)
                    reader.read_bytes(data)
                    bat_notes.append({"t": time.time(), "v": list(data)})
                except Exception:
                    pass

            st = await ch.write_client_characteristic_configuration_descriptor_async(
                CCCD.NOTIFY
            )
            report["battery_cccd_write"] = int(st)
            token = ch.add_value_changed(handler)
            t0 = time.time()
            print("Listening battery notify 12s for rate...")
            await asyncio.sleep(12.0)
            ch.remove_value_changed(token)
            try:
                await ch.write_client_characteristic_configuration_descriptor_async(
                    CCCD.NONE
                )
            except Exception:
                pass
            report["battery_notify_events"] = bat_notes
            if len(bat_notes) >= 2:
                dts = [
                    bat_notes[i + 1]["t"] - bat_notes[i]["t"]
                    for i in range(len(bat_notes) - 1)
                ]
                report["battery_notify_interval_s"] = {
                    "count": len(bat_notes),
                    "min": min(dts),
                    "max": max(dts),
                    "avg": sum(dts) / len(dts),
                    "span_s": bat_notes[-1]["t"] - bat_notes[0]["t"],
                }
                print("Notify rate:", report["battery_notify_interval_s"])
            else:
                print(f"Notify count={len(bat_notes)}")

    ble.close()


def hid_enumerate() -> dict:
    out = {"dongle_32c2_0066": [], "ble_32c2_0026": [], "all_32c2": [], "popgo_name": []}
    try:
        import hid
    except ImportError:
        out["error"] = "hid not installed"
        return out

    for d in hid.enumerate():
        vid = d.get("vendor_id") or 0
        pid = d.get("product_id") or 0
        name = (d.get("product_string") or "") + " " + (d.get("manufacturer_string") or "")
        entry = {
            "vid": f"0x{vid:04X}",
            "pid": f"0x{pid:04X}",
            "usage_page": hex(d.get("usage_page") or 0),
            "usage": hex(d.get("usage") or 0),
            "interface": d.get("interface_number"),
            "product": d.get("product_string"),
            "manufacturer": d.get("manufacturer_string"),
            "serial": d.get("serial_number"),
            "path": d.get("path", b"").decode("utf-8", "replace")
            if isinstance(d.get("path"), (bytes, bytearray))
            else d.get("path"),
            "release": d.get("release_number"),
        }
        if vid == 0x32C2:
            out["all_32c2"].append(entry)
        if vid == VID_DONGLE and pid == PID_DONGLE:
            out["dongle_32c2_0066"].append(entry)
        if vid == VID_BLE and pid == PID_BLE:
            out["ble_32c2_0026"].append(entry)
        if "popgo" in name.lower() or "pop go" in name.lower():
            out["popgo_name"].append(entry)
    return out


def dongle_status() -> dict | None:
    try:
        import hid
    except ImportError:
        return None
    for d in hid.enumerate(VID_DONGLE, PID_DONGLE):
        if d.get("usage_page") != 0xFFB5:
            continue
        h = hid.device()
        try:
            h.open_path(d["path"])
            h.set_nonblocking(True)
            # drain
            t0 = time.time()
            while time.time() - t0 < 0.05:
                if not h.read(64):
                    time.sleep(0.001)
            h.write(bytes([0xB5, 0x01, 0, 0, 0, 0, 0, 0]))
            time.sleep(0.05)
            pkts = []
            t0 = time.time()
            while time.time() - t0 < 0.15:
                r = h.read(64)
                if r:
                    pkts.append(list(r))
                else:
                    time.sleep(0.001)
            h.close()
            if not pkts:
                return {"error": "no response"}
            p = pkts[0]
            # voltage LE at [5:7]
            mv = None
            if len(p) >= 7:
                mv = p[5] | (p[6] << 8)
            return {
                "raw": p,
                "fw_percent": p[3] if len(p) > 3 else None,
                "flags": p[2] if len(p) > 2 else None,
                "mv_le_5_6": mv,
                "all_pkts": pkts,
            }
        except Exception as e:
            return {"error": str(e)}
    return {"error": "vendor iface not found"}


async def watch_ads(seconds: float = 8.0) -> list:
    """Capture BLE advertisements mentioning PopGo or our address."""
    from winsdk.windows.devices.bluetooth.advertisement import (
        BluetoothLEAdvertisementFilter,
        BluetoothLEAdvertisementWatcher,
        BluetoothLEScanningMode,
    )

    found = []

    def on_recv(watcher, args):
        try:
            addr = args.bluetooth_address
            adv = args.advertisement
            local = adv.local_name or ""
            mfg = []
            for s in adv.manufacturer_data:
                buf = bytes(s.data)
                mfg.append({"company_id": s.company_id, "data_hex": buf.hex()})
            suuids = [str(u).lower() for u in adv.service_uuids]
            # only care about our addr or name
            if addr == ADDR or "pop" in local.lower() or "acer" in local.lower() or mfg:
                found.append(
                    {
                        "address": f"{addr:012X}",
                        "rssi": args.raw_signal_strength_in_d_bm,
                        "local_name": local,
                        "service_uuids": suuids,
                        "manufacturer": mfg,
                        "flags": int(adv.flags) if adv.flags is not None else None,
                        "is_connectable": bool(getattr(args, "is_connectable", False))
                        if hasattr(args, "is_connectable")
                        else None,
                    }
                )
        except Exception as e:
            found.append({"err": str(e)})

    watcher = BluetoothLEAdvertisementWatcher()
    watcher.scanning_mode = BluetoothLEScanningMode.ACTIVE
    watcher.add_received(on_recv)
    watcher.start()
    print(f"Advertisement watch {seconds}s...")
    await asyncio.sleep(seconds)
    watcher.stop()
    # dedupe by address keeping strongest RSSI
    by = {}
    for f in found:
        a = f.get("address")
        if not a:
            continue
        if a not in by or (f.get("rssi") or -999) > (by[a].get("rssi") or -999):
            by[a] = f
    return list(by.values()) + [f for f in found if "err" in f]


def pnp_powershell() -> dict:
    import subprocess

    ps = r"""
$ErrorActionPreference='SilentlyContinue'
$out = @{}
# BLE device
$bt = Get-PnpDevice -FriendlyName '*PopGo*' | Select-Object Status,Class,FriendlyName,InstanceId
$out.pnp = @($bt | ForEach-Object { @{status=$_.Status; class=$_.Class; name=$_.FriendlyName; id=$_.InstanceId} })
# HID under BT
$hid = Get-PnpDevice -Class HIDClass | Where-Object { $_.InstanceId -match '32C2|PopGo|BTHLE' } |
  Select-Object Status,FriendlyName,InstanceId
$out.hid = @($hid | ForEach-Object { @{status=$_.Status; name=$_.FriendlyName; id=$_.InstanceId} })
# Battery devices
$bat = Get-PnpDevice | Where-Object { $_.FriendlyName -match 'PopGo|Battery' -and $_.InstanceId -match 'BTHLE|32C2' } |
  Select-Object Status,Class,FriendlyName,InstanceId
$out.battery_pnp = @($bat | ForEach-Object { @{status=$_.Status; class=$_.Class; name=$_.FriendlyName; id=$_.InstanceId} })
# Bluetooth attributes via Get-PnpDeviceProperty if available
$dev = Get-PnpDevice -FriendlyName '*PopGo*' | Select-Object -First 1
if ($dev) {
  $props = Get-PnpDeviceProperty -InstanceId $dev.InstanceId | Where-Object {
    $_.KeyName -match 'Battery|Bluetooth|Address|Manufacturer|DeviceDesc|Hardware|Bus|Container'
  } | Select-Object KeyName, Data, Type
  $out.props = @($props | ForEach-Object { @{key=$_.KeyName; data="$($_.Data)"; type="$($_.Type)"} })
}
$out | ConvertTo-Json -Depth 6
"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode != 0:
            return {"stderr": r.stderr, "stdout": r.stdout[:2000]}
        return json.loads(r.stdout) if r.stdout.strip() else {"empty": True}
    except Exception as e:
        return {"error": str(e)}


def try_ble_hid_report_descriptor() -> dict:
    """If Windows exposes BLE mouse as HID, try to open and get report descriptor length."""
    result = {"attempts": []}
    try:
        import hid
    except ImportError:
        return {"error": "no hid"}

    for d in hid.enumerate():
        path = d.get("path") or b""
        if isinstance(path, bytes):
            path_s = path.decode("utf-8", "replace")
        else:
            path_s = str(path)
        vid, pid = d.get("vendor_id") or 0, d.get("product_id") or 0
        if vid != VID_BLE and "BTHLEDEVICE" not in path_s.upper() and "BTHENUM" not in path_s.upper():
            # still try serial match
            ser = (d.get("serial_number") or "").replace(":", "").upper()
            if ser and "F900C6021401" not in ser and "C6021401" not in ser:
                if vid != VID_BLE:
                    continue
        if vid not in (VID_BLE, 0) and "BTHLE" not in path_s.upper():
            continue
        if vid == VID_BLE or "BTHLE" in path_s.upper() or "PopGo" in (d.get("product_string") or ""):
            entry = {
                "vid": hex(vid),
                "pid": hex(pid),
                "product": d.get("product_string"),
                "usage_page": hex(d.get("usage_page") or 0),
                "usage": hex(d.get("usage") or 0),
                "path": path_s[:200],
            }
            try:
                h = hid.device()
                h.open_path(d["path"])
                # some backends expose get_report_descriptor
                if hasattr(h, "get_report_descriptor"):
                    rd = h.get_report_descriptor()
                    entry["report_descriptor_len"] = len(rd) if rd else 0
                    entry["report_descriptor_hex"] = (rd.hex() if rd else "")[:200]
                    if rd:
                        (OUT / f"hid_rd_{vid:04x}_{pid:04x}.bin").write_bytes(rd)
                h.close()
            except Exception as e:
                entry["open_err"] = str(e)
            result["attempts"].append(entry)
    return result


async def main() -> None:
    report: dict = {
        "ts": time.time(),
        "address": f"{ADDR:012X}",
    }

    print("=== HID enumerate ===")
    report["hid_enum"] = hid_enumerate()
    print(
        f"  dongle ifaces={len(report['hid_enum']['dongle_32c2_0066'])} "
        f"ble={len(report['hid_enum']['ble_32c2_0026'])} "
        f"all_32c2={len(report['hid_enum']['all_32c2'])}"
    )
    for e in report["hid_enum"]["all_32c2"]:
        print(f"  {e['vid']}:{e['pid']} up={e['usage_page']} u={e['usage']} {e['product']}")

    print("\n=== 2.4G dongle status ===")
    report["dongle_status"] = dongle_status()
    print(report["dongle_status"])

    print("\n=== PnP PowerShell ===")
    report["pnp"] = pnp_powershell()
    print(json.dumps(report["pnp"], indent=2)[:2500])

    print("\n=== BLE HID report descriptor ===")
    report["ble_hid_rd"] = try_ble_hid_report_descriptor()
    print(json.dumps(report["ble_hid_rd"], indent=2)[:1500])

    print("\n=== Deep GATT + HID access ===")
    await deep_gatt(report)

    print("\n=== Advertisement watch ===")
    try:
        ads = await watch_ads(8.0)
        report["advertisements"] = ads
        print(json.dumps(ads, indent=2)[:2000])
    except Exception as e:
        report["advertisements_err"] = str(e)
        print("ads err", e)

    # Dual radio summary
    ble_pct = None
    for svc in report.get("services") or []:
        for ch in svc.get("characteristics") or []:
            if ch.get("battery") is not None:
                ble_pct = ch["battery"]
    d = report.get("dongle_status") or {}
    report["dual_radio_summary"] = {
        "ble_battery_percent": ble_pct,
        "dongle_fw_percent": d.get("fw_percent"),
        "dongle_mv": d.get("mv_le_5_6"),
        "dongle_flags": d.get("flags"),
        "both_radios_present": bool(report["hid_enum"]["dongle_32c2_0066"])
        and report.get("connection_status") == 1,
    }
    print("\n=== DUAL RADIO ===")
    print(json.dumps(report["dual_radio_summary"], indent=2))

    out = OUT / "ble_deep2_report.json"
    # make JSON safe
    def scrub(o):
        if isinstance(o, dict):
            return {str(k): scrub(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [scrub(x) for x in o]
        if isinstance(o, (bytes, bytearray)):
            return o.hex()
        if isinstance(o, (int, float, str, bool)) or o is None:
            return o
        return str(o)

    out.write_text(json.dumps(scrub(report), indent=2), encoding="utf-8")
    print("\nWrote", out)


if __name__ == "__main__":
    asyncio.run(main())
