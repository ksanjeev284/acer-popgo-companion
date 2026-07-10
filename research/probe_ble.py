"""Probe Acer PopGo over BLE for Battery Service and other GATT data."""
from __future__ import annotations

import asyncio
import sys

from bleak import BleakClient, BleakScanner

# From Device Manager: BTHLE\DEV_F900C6021401
KNOWN_ADDRS = [
    "F9:00:C6:02:14:01",
    "f9:00:c6:02:14:01",
]


async def dump_client(client: BleakClient, label: str) -> None:
    print(f"\n=== {label} connected={client.is_connected} ===")
    # bleak 0.22+ uses client.services after connect
    services = client.services
    if services is None:
        await client.get_services()
        services = client.services

    for s in services:
        print(f"SERVICE {s.uuid}  ({s.description})")
        for ch in s.characteristics:
            props = ",".join(ch.properties)
            print(f"  CHAR {ch.uuid}  [{props}]  {ch.description}")
            if "read" in ch.properties:
                try:
                    val = await client.read_gatt_char(ch.uuid)
                    print(f"    VALUE hex={val.hex()} list={list(val)}")
                    if len(val) == 1:
                        print(f"    uint8={val[0]}")
                    u = ch.uuid.lower()
                    if "2a19" in u:
                        print(f"    *** BATTERY LEVEL = {val[0]}% ***")
                    if "2a1a" in u and len(val) >= 1:
                        # Battery Power State (optional)
                        print(f"    *** BATTERY POWER STATE flags={val[0]:08b} ***")
                    if "2a1b" in u:
                        print(f"    *** BATTERY LEVEL STATUS ***")
                except Exception as e:
                    print(f"    read err: {e}")


async def main() -> int:
    print("Scanning BLE (10s)...")
    devices = await BleakScanner.discover(timeout=10.0, return_adv=True)
    targets: list[str] = []

    # devices may be dict address -> (BLEDevice, AdvertisementData) if return_adv
    if isinstance(devices, dict):
        items = devices.items()
        for addr, (dev, adv) in items:
            name = dev.name or adv.local_name or ""
            rssi = getattr(adv, "rssi", None)
            print(f"  {addr}  RSSI={rssi}  name={name!r}")
            blob = (name + addr).lower().replace(":", "")
            if any(k in blob for k in ("pop", "acer", "f900c6021401", "c6021401")):
                targets.append(addr)
    else:
        for dev in devices:
            name = dev.name or ""
            print(f"  {dev.address}  name={name!r}")
            blob = (name + dev.address).lower().replace(":", "")
            if any(k in blob for k in ("pop", "acer", "f900c6021401", "c6021401")):
                targets.append(dev.address)

    for a in KNOWN_ADDRS:
        if a not in targets and a.upper() not in [t.upper() for t in targets]:
            targets.append(a)

    if not targets:
        print("No targets found")
        return 1

    for addr in targets:
        print(f"\nConnecting to {addr} ...")
        try:
            async with BleakClient(addr, timeout=20.0) as client:
                await dump_client(client, addr)
        except Exception as e:
            print(f"connect/dump failed: {type(e).__name__}: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
