"""Read Acer PopGo BLE Battery Service via Windows Runtime (winsdk)."""
from __future__ import annotations

import asyncio
import sys

from winsdk.windows.devices.bluetooth import BluetoothCacheMode, BluetoothLEDevice
from winsdk.windows.devices.bluetooth.genericattributeprofile import (
    GattCharacteristicProperties,
    GattCommunicationStatus,
    GattDeviceServicesResult,
)
from winsdk.windows.devices.enumeration import DeviceInformation
from winsdk.windows.storage.streams import DataReader


async def read_char(ch) -> bytes | None:
    resp = await ch.read_value_async()
    if resp.status != GattCommunicationStatus.SUCCESS:
        print(f"    read status={resp.status}")
        return None
    reader = DataReader.from_buffer(resp.value)
    data = bytearray(resp.value.length)
    reader.read_bytes(data)
    return bytes(data)


async def dump_ble(ble: BluetoothLEDevice) -> None:
    print(
        f"name={ble.name!r} addr={ble.bluetooth_address:012X} "
        f"conn={ble.connection_status}"
    )
    # Uncached to force live values
    res: GattDeviceServicesResult = await ble.get_gatt_services_async(
        BluetoothCacheMode.UNCACHED
    )
    print(f"services status={res.status} count={len(res.services)}")
    if res.status != GattCommunicationStatus.SUCCESS:
        # try cached
        res = await ble.get_gatt_services_async(BluetoothCacheMode.CACHED)
        print(f"cached services status={res.status} count={len(res.services)}")

    for svc in res.services:
        su = str(svc.uuid).lower()
        print(f"SVC {su}")
        cres = await svc.get_characteristics_async(BluetoothCacheMode.UNCACHED)
        if cres.status != GattCommunicationStatus.SUCCESS:
            cres = await svc.get_characteristics_async(BluetoothCacheMode.CACHED)
        if cres.status != GattCommunicationStatus.SUCCESS:
            print(f"  chars fail {cres.status}")
            continue
        for ch in cres.characteristics:
            cu = str(ch.uuid).lower()
            props = int(ch.characteristic_properties)
            print(f"  CHAR {cu} props={props}")
            if props & int(GattCharacteristicProperties.READ):
                val = await read_char(ch)
                if val is not None:
                    print(f"    -> hex={val.hex()} list={list(val)}")
                    if "2a19" in cu and val:
                        print(f"    *** BATTERY LEVEL = {val[0]}% ***")
                    if "2a1a" in cu and val:
                        print(f"    *** POWER STATE = {val[0]:08b} ***")


async def main() -> int:
    aqs = BluetoothLEDevice.get_device_selector()
    print("AQS", aqs)
    devices = await DeviceInformation.find_all_async(aqs)
    print(f"Found {len(devices)} BLE device infos")
    targets = []
    for d in devices:
        name = d.name or ""
        print(f"  name={name!r} id={d.id[:100]}")
        blob = (name + d.id).upper()
        if any(k in blob for k in ("POPGO", "ACER", "F900C6021401", "32C2", "0026")):
            targets.append(d)

    if not targets:
        print("No filter match; trying address path")
        addr = int("F900C6021401", 16)
        ble = await BluetoothLEDevice.from_bluetooth_address_async(addr)
        if ble:
            await dump_ble(ble)
            ble.close()
            return 0
        print("address open failed")
        return 1

    for d in targets:
        print(f"\n=== from_id {d.name!r} ===")
        ble = await BluetoothLEDevice.from_id_async(d.id)
        if not ble:
            print("null")
            continue
        try:
            await dump_ble(ble)
        finally:
            ble.close()

    # Also try raw address
    print("\n=== from_bluetooth_address F900C6021401 ===")
    ble = await BluetoothLEDevice.from_bluetooth_address_async(int("F900C6021401", 16))
    if ble:
        try:
            await dump_ble(ble)
        finally:
            ble.close()
    else:
        print("null from address")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
