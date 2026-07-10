"""
BLE Battery Service reader for Acer PopGo (Windows WinRT).

When paired as "Acer PopGo BT5.4", the mouse exposes standard GATT:
  Service 0x180F Battery
  Characteristic 0x2A19 Battery Level (uint8 percent) + Notify

This is more trustworthy than the 2.4G MCU percent (often frozen).
Polls periodically and also enables Notify when possible.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger("popgo.ble")

BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
BATTERY_SERVICE_UUID = "0000180f-0000-1000-8000-00805f9b34fb"

# Known PopGo BLE identity from Device Manager
POPGO_NAME_HINTS = ("popgo", "acer pop", "acer popgo")
# Address observed: F9:00:C6:02:14:01
DEFAULT_BT_ADDR = 0xF900C6021401


@dataclass
class BleBatteryReading:
    percent: int
    device_name: str
    address_hex: str
    source: str = "ble-gatt-2a19"
    timestamp: float = 0.0


def _run_coro(coro):
    """Run async code from sync context (own event loop in worker)."""
    return asyncio.run(coro)


async def _read_battery_winrt(address: Optional[int] = None) -> Optional[BleBatteryReading]:
    if sys.platform != "win32":
        return None
    try:
        from winsdk.windows.devices.bluetooth import BluetoothCacheMode, BluetoothLEDevice
        from winsdk.windows.devices.bluetooth.genericattributeprofile import (
            GattCharacteristicProperties,
            GattCommunicationStatus,
        )
        from winsdk.windows.storage.streams import DataReader
    except ImportError as e:
        log.debug("winsdk not available: %s", e)
        return None

    addr = address or DEFAULT_BT_ADDR
    ble = await BluetoothLEDevice.from_bluetooth_address_async(addr)
    if ble is None:
        log.debug("BLE device null for addr %012X", addr)
        return None

    try:
        name = ble.name or "Acer PopGo BT"
        for mode in (BluetoothCacheMode.UNCACHED, BluetoothCacheMode.CACHED):
            res = await ble.get_gatt_services_async(mode)
            if res.status != GattCommunicationStatus.SUCCESS:
                continue
            for svc in res.services:
                if BATTERY_SERVICE_UUID not in str(svc.uuid).lower():
                    continue
                cres = await svc.get_characteristics_async(mode)
                if cres.status != GattCommunicationStatus.SUCCESS:
                    continue
                for ch in cres.characteristics:
                    if BATTERY_LEVEL_UUID not in str(ch.uuid).lower():
                        continue
                    if not (
                        int(ch.characteristic_properties)
                        & int(GattCharacteristicProperties.READ)
                    ):
                        continue
                    resp = await ch.read_value_async()
                    if resp.status != GattCommunicationStatus.SUCCESS:
                        continue
                    reader = DataReader.from_buffer(resp.value)
                    raw = bytearray(resp.value.length)
                    reader.read_bytes(raw)
                    if not raw:
                        continue
                    pct = int(raw[0])
                    if pct > 100:
                        pct = 100
                    return BleBatteryReading(
                        percent=pct,
                        device_name=name,
                        address_hex=f"{ble.bluetooth_address:012X}",
                        timestamp=time.time(),
                    )
        return None
    finally:
        ble.close()


def read_ble_battery_sync(address: Optional[int] = None) -> Optional[BleBatteryReading]:
    """Blocking read of BLE battery level (Windows)."""
    if sys.platform != "win32":
        return None
    try:
        return _run_coro(_read_battery_winrt(address))
    except Exception as e:
        log.debug("BLE battery read failed: %s", e)
        return None


class BleBatteryPoller:
    """
    Background BLE battery reader.

    Prefers GATT Notify on 0x2A19 (confirmed on PopGo) and falls back to
    periodic Read if the notify session drops.
    """

    def __init__(
        self,
        on_reading: Callable[[BleBatteryReading], None],
        interval: float = 15.0,
        address: Optional[int] = None,
        prefer_notify: bool = True,
    ) -> None:
        self.on_reading = on_reading
        self.interval = interval
        self.address = address
        self.prefer_notify = prefer_notify
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last: Optional[BleBatteryReading] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="PopGoBLE", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=8.0)
            self._thread = None

    def _emit(self, reading: BleBatteryReading) -> None:
        self.last = reading
        try:
            self.on_reading(reading)
        except Exception:
            pass

    def _run(self) -> None:
        if self.prefer_notify and sys.platform == "win32":
            try:
                _run_coro(self._notify_loop())
                return
            except Exception as e:
                log.debug("BLE notify loop failed, falling back to poll: %s", e)

        # Poll fallback
        while not self._stop.is_set():
            reading = read_ble_battery_sync(self.address)
            if reading is not None:
                self._emit(reading)
            self._stop.wait(self.interval)

    async def _notify_loop(self) -> None:
        from winsdk.windows.devices.bluetooth import BluetoothCacheMode, BluetoothLEDevice
        from winsdk.windows.devices.bluetooth.genericattributeprofile import (
            GattCharacteristicProperties,
            GattClientCharacteristicConfigurationDescriptorValue as CCCD,
            GattCommunicationStatus,
        )
        from winsdk.windows.storage.streams import DataReader

        addr = self.address or DEFAULT_BT_ADDR

        while not self._stop.is_set():
            ble = await BluetoothLEDevice.from_bluetooth_address_async(addr)
            if ble is None:
                await asyncio.sleep(min(self.interval, 5.0))
                continue

            name = ble.name or "Acer PopGo BT"
            ch = None
            try:
                for mode in (BluetoothCacheMode.UNCACHED, BluetoothCacheMode.CACHED):
                    res = await ble.get_gatt_services_async(mode)
                    if res.status != GattCommunicationStatus.SUCCESS:
                        continue
                    for svc in res.services:
                        if BATTERY_SERVICE_UUID not in str(svc.uuid).lower():
                            continue
                        cres = await svc.get_characteristics_async(mode)
                        if cres.status != GattCommunicationStatus.SUCCESS:
                            continue
                        for c in cres.characteristics:
                            if BATTERY_LEVEL_UUID in str(c.uuid).lower():
                                ch = c
                                break
                        if ch:
                            break
                    if ch:
                        break

                if ch is None:
                    ble.close()
                    await asyncio.sleep(self.interval)
                    continue

                def parse_raw(raw: bytes) -> Optional[BleBatteryReading]:
                    if not raw:
                        return None
                    pct = int(raw[0])
                    if pct > 100:
                        pct = 100
                    return BleBatteryReading(
                        percent=pct,
                        device_name=name,
                        address_hex=f"{addr:012X}",
                        source="ble-gatt-2a19-notify",
                        timestamp=time.time(),
                    )

                # Initial read
                props = int(ch.characteristic_properties)
                if props & int(GattCharacteristicProperties.READ):
                    resp = await ch.read_value_async()
                    if resp.status == GattCommunicationStatus.SUCCESS:
                        reader = DataReader.from_buffer(resp.value)
                        raw = bytearray(resp.value.length)
                        reader.read_bytes(raw)
                        r = parse_raw(bytes(raw))
                        if r:
                            self._emit(r)

                notes: list = []

                def handler(sender, args):
                    try:
                        reader = DataReader.from_buffer(args.characteristic_value)
                        raw = bytearray(args.characteristic_value.length)
                        reader.read_bytes(raw)
                        r = parse_raw(bytes(raw))
                        if r:
                            notes.append(r)
                    except Exception:
                        pass

                can_notify = bool(props & int(GattCharacteristicProperties.NOTIFY))
                token = None
                if can_notify:
                    st = await ch.write_client_characteristic_configuration_descriptor_async(
                        CCCD.NOTIFY
                    )
                    if st == GattCommunicationStatus.SUCCESS:
                        token = ch.add_value_changed(handler)

                # Hold session open; also poll occasionally as heartbeat
                next_poll = time.time() + self.interval
                while not self._stop.is_set():
                    # Drain notify queue
                    while notes:
                        self._emit(notes.pop(0))
                    if time.time() >= next_poll:
                        # Heartbeat read in case notify is sparse
                        try:
                            resp = await ch.read_value_async()
                            if resp.status == GattCommunicationStatus.SUCCESS:
                                reader = DataReader.from_buffer(resp.value)
                                raw = bytearray(resp.value.length)
                                reader.read_bytes(raw)
                                r = parse_raw(bytes(raw))
                                if r:
                                    r = BleBatteryReading(
                                        percent=r.percent,
                                        device_name=r.device_name,
                                        address_hex=r.address_hex,
                                        source="ble-gatt-2a19",
                                        timestamp=r.timestamp,
                                    )
                                    self._emit(r)
                        except Exception:
                            break  # re-open device
                        next_poll = time.time() + self.interval
                    await asyncio.sleep(0.4)

                if token is not None:
                    try:
                        ch.remove_value_changed(token)
                    except Exception:
                        pass
                    try:
                        await ch.write_client_characteristic_configuration_descriptor_async(
                            CCCD.NONE
                        )
                    except Exception:
                        pass
            finally:
                try:
                    ble.close()
                except Exception:
                    pass

            if not self._stop.is_set():
                await asyncio.sleep(2.0)
