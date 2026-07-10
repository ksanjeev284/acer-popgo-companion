"""
Deep BLE probe for Acer PopGo BT5.4.
Reads all GATT services/characteristics, tries notify on battery,
parses Device Info, PnP ID, Appearance, HID report map if available.
"""
from __future__ import annotations

import asyncio
import json
import struct
import time
from pathlib import Path

from winsdk.windows.devices.bluetooth import BluetoothCacheMode, BluetoothLEDevice
from winsdk.windows.devices.bluetooth.genericattributeprofile import (
    GattCharacteristicProperties,
    GattClientCharacteristicConfigurationDescriptorValue,
    GattCommunicationStatus,
)
from winsdk.windows.storage.streams import DataReader, DataWriter

OUT = Path(__file__).resolve().parent / "deep_ble_out"
OUT.mkdir(exist_ok=True)

ADDR = 0xF900C6021401

UUID_NAMES = {
    "1800": "Generic Access",
    "1801": "Generic Attribute",
    "180a": "Device Information",
    "180f": "Battery Service",
    "1812": "HID over GATT",
    "2a00": "Device Name",
    "2a01": "Appearance",
    "2a04": "Peripheral Preferred Connection Parameters",
    "2a05": "Service Changed",
    "2a19": "Battery Level",
    "2a1a": "Battery Power State",
    "2a1b": "Battery Level Status",
    "2a23": "System ID",
    "2a24": "Model Number",
    "2a25": "Serial Number",
    "2a26": "Firmware Revision",
    "2a27": "Hardware Revision",
    "2a28": "Software Revision",
    "2a29": "Manufacturer Name",
    "2a4a": "HID Information",
    "2a4b": "Report Map",
    "2a4c": "HID Control Point",
    "2a4d": "Report",
    "2a4e": "Protocol Mode",
    "2a50": "PnP ID",
}


def short_uuid(u: str) -> str:
    u = u.lower().replace("-", "")
    if u.startswith("0000") and u.endswith("00001000800000805f9b34fb"):
        return u[4:8]
    return u[:8]


def name_for(u: str) -> str:
    s = short_uuid(u)
    return UUID_NAMES.get(s, "")


async def read_bytes(ch) -> bytes | None:
    resp = await ch.read_value_async()
    if resp.status != GattCommunicationStatus.SUCCESS:
        return None
    reader = DataReader.from_buffer(resp.value)
    data = bytearray(resp.value.length)
    reader.read_bytes(data)
    return bytes(data)


def decode_value(uuid: str, val: bytes) -> dict:
    out: dict = {"hex": val.hex(), "list": list(val)}
    su = short_uuid(uuid)
    if su == "2a00":
        out["text"] = val.split(b"\x00")[0].decode("utf-8", "replace")
    elif su == "2a19" and val:
        out["battery_percent"] = val[0]
    elif su == "2a01" and len(val) >= 2:
        app = val[0] | (val[1] << 8)
        out["appearance"] = app
        out["appearance_hex"] = f"0x{app:04X}"
        # Bluetooth SIG appearance: 0x03C2 = HID Mouse category bits
        cats = {
            0x03C0: "HID Generic",
            0x03C1: "Keyboard",
            0x03C2: "Mouse",
            0x03C3: "Joystick",
            0x03C4: "Gamepad",
        }
        out["appearance_name"] = cats.get(app & 0xFFC0, cats.get(app, f"raw {app}"))
    elif su == "2a50" and len(val) >= 7:
        # vendor_id_source, vendor_id, product_id, product_version
        src, vid, pid, ver = struct.unpack_from("<BHHH", val)
        out["pnp"] = {
            "vendor_id_source": src,  # 1=SIG, 2=USB
            "vendor_id": f"0x{vid:04X}",
            "product_id": f"0x{pid:04X}",
            "product_version": f"0x{ver:04X}",
        }
    elif su == "2a04" and len(val) >= 8:
        imin, imax, lat, timeout = struct.unpack_from("<HHHH", val)
        out["conn_params"] = {
            "interval_min_ms": imin * 1.25,
            "interval_max_ms": imax * 1.25,
            "latency": lat,
            "timeout_ms": timeout * 10,
        }
    elif su in ("2a24", "2a25", "2a26", "2a27", "2a28", "2a29"):
        out["text"] = val.split(b"\x00")[0].decode("utf-8", "replace")
    elif su == "2a1a" and val:
        # Battery Power State bitfield (older spec)
        b = val[0]
        out["power_state_bits"] = f"{b:08b}"
        out["present"] = bool(b & 0x01)
        out["discharging"] = bool(b & 0x02)
        out["charging"] = bool(b & 0x04)
        out["level_known"] = bool(b & 0x08)
    return out


async def try_notify_battery(ch, seconds: float = 8.0) -> list:
    """Subscribe to battery notifications if supported."""
    notes = []
    props = int(ch.characteristic_properties)
    if not (props & int(GattCharacteristicProperties.NOTIFY)):
        return notes

    def handler(sender, args):
        try:
            reader = DataReader.from_buffer(args.characteristic_value)
            data = bytearray(args.characteristic_value.length)
            reader.read_bytes(data)
            notes.append({"t": time.time(), "value": list(data)})
            print(f"  NOTIFY battery {list(data)}")
        except Exception as e:
            print("  notify parse err", e)

    # Write CCCD
    status = await ch.write_client_characteristic_configuration_descriptor_async(
        GattClientCharacteristicConfigurationDescriptorValue.NOTIFY
    )
    print(f"  CCCD notify write status={status}")
    if status != GattCommunicationStatus.SUCCESS:
        return notes

    token = ch.add_value_changed(handler)
    print(f"  Listening notifications {seconds}s...")
    await asyncio.sleep(seconds)
    ch.remove_value_changed(token)
    try:
        await ch.write_client_characteristic_configuration_descriptor_async(
            GattClientCharacteristicConfigurationDescriptorValue.NONE
        )
    except Exception:
        pass
    return notes


async def main() -> None:
    report: dict = {
        "address": f"{ADDR:012X}",
        "services": [],
        "battery_notifies": [],
        "notes": [],
    }

    print(f"Opening BLE {ADDR:012X}...")
    ble = await BluetoothLEDevice.from_bluetooth_address_async(ADDR)
    if ble is None:
        print("Device not found — keep Acer PopGo BT5.4 connected")
        return

    print(f"name={ble.name!r} conn={ble.connection_status}")
    report["name"] = ble.name
    report["connection_status"] = int(ble.connection_status)

    try:
        for mode_name, mode in (
            ("UNCACHED", BluetoothCacheMode.UNCACHED),
            ("CACHED", BluetoothCacheMode.CACHED),
        ):
            res = await ble.get_gatt_services_async(mode)
            print(f"\n=== Services {mode_name} status={res.status} n={len(res.services)} ===")
            if res.status != GattCommunicationStatus.SUCCESS:
                continue

            bat_char = None
            for svc in res.services:
                su = str(svc.uuid).lower()
                sname = name_for(su)
                print(f"\nSERVICE {su}  {sname}")
                svc_entry = {"uuid": su, "name": sname, "characteristics": []}

                cres = await svc.get_characteristics_async(mode)
                if cres.status != GattCommunicationStatus.SUCCESS:
                    print(f"  characteristics status={cres.status}")
                    # Access denied often for HID while Windows owns it
                    svc_entry["chars_status"] = str(cres.status)
                    report["services"].append(svc_entry)
                    continue

                for ch in cres.characteristics:
                    cu = str(ch.uuid).lower()
                    cname = name_for(cu)
                    props = int(ch.characteristic_properties)
                    prop_names = []
                    if props & 0x01:
                        prop_names.append("Broadcast")
                    if props & 0x02:
                        prop_names.append("Read")
                    if props & 0x04:
                        prop_names.append("WriteWithoutResponse")
                    if props & 0x08:
                        prop_names.append("Write")
                    if props & 0x10:
                        prop_names.append("Notify")
                    if props & 0x20:
                        prop_names.append("Indicate")
                    print(f"  CHAR {cu}  {cname}  props={props} ({','.join(prop_names)})")

                    ch_entry = {
                        "uuid": cu,
                        "name": cname,
                        "props": props,
                        "prop_names": prop_names,
                    }

                    if props & int(GattCharacteristicProperties.READ):
                        val = await read_bytes(ch)
                        if val is not None:
                            decoded = decode_value(cu, val)
                            ch_entry["value"] = decoded
                            print(f"    VALUE {decoded}")
                        else:
                            ch_entry["read_failed"] = True
                            print("    READ failed")

                    if "2a19" in cu:
                        bat_char = ch

                    # descriptors
                    try:
                        dres = await ch.get_descriptors_async(mode)
                        if dres.status == GattCommunicationStatus.SUCCESS:
                            for desc in dres.descriptors:
                                du = str(desc.uuid).lower()
                                print(f"    DESC {du}")
                                ch_entry.setdefault("descriptors", []).append(du)
                    except Exception as e:
                        print(f"    desc err {e}")

                    svc_entry["characteristics"].append(ch_entry)

                report["services"].append(svc_entry)

            if bat_char is not None and mode_name == "UNCACHED":
                print("\n=== Battery notify test ===")
                notes = await try_notify_battery(bat_char, 6.0)
                report["battery_notifies"] = notes

            break  # one successful mode enough

        # Also enumerate HID via separate path note
        report["notes"].append(
            "HID service 0x1812 may return ACCESS_DENIED while Windows Bluetooth stack owns HOGP."
        )
        report["notes"].append(
            "Battery 0x2A19 is the best SOC source when connected over BLE."
        )

    finally:
        ble.close()

    out = OUT / "ble_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\nWrote", out)

    # Summary
    print("\n======== SUMMARY ========")
    for svc in report["services"]:
        print(f"\n{svc.get('name') or svc['uuid']}")
        for ch in svc.get("characteristics", []):
            val = ch.get("value", {})
            extra = ""
            if "battery_percent" in val:
                extra = f" => {val['battery_percent']}%"
            elif "text" in val:
                extra = f" => {val['text']!r}"
            elif "pnp" in val:
                extra = f" => {val['pnp']}"
            print(f"  {ch.get('name') or ch['uuid']}: {ch.get('prop_names')}{extra}")


if __name__ == "__main__":
    asyncio.run(main())
