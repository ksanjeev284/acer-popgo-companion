"""Probe Acer PopGo (VID 32C2 / PID 0066) HID interfaces for battery/DPI reports."""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

import hid

VID = 0x32C2
PID = 0x0066

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
hid_dll = ctypes.WinDLL("hid", use_last_error=True)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
INVALID_HANDLE = wintypes.HANDLE(-1).value

CreateFileW = kernel32.CreateFileW
CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
CreateFileW.restype = wintypes.HANDLE
CloseHandle = kernel32.CloseHandle

HidD_GetPreparsedData = hid_dll.HidD_GetPreparsedData
HidD_FreePreparsedData = hid_dll.HidD_FreePreparsedData
HidP_GetCaps = hid_dll.HidP_GetCaps
HidD_GetFeature = hid_dll.HidD_GetFeature
HidD_SetFeature = hid_dll.HidD_SetFeature
HidD_GetProductString = hid_dll.HidD_GetProductString
HidD_GetManufacturerString = hid_dll.HidD_GetManufacturerString
HidD_GetAttributes = hid_dll.HidD_GetAttributes
HidD_GetInputReport = hid_dll.HidD_GetInputReport


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.ULONG),
        ("VendorID", wintypes.USHORT),
        ("ProductID", wintypes.USHORT),
        ("VersionNumber", wintypes.USHORT),
    ]


def open_handle(path: str):
    handle = CreateFileW(
        path,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if handle in (INVALID_HANDLE, 0):
        handle = CreateFileW(
            path,
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
    if handle in (INVALID_HANDLE, 0):
        return None
    return handle


def probe_caps():
    print("=" * 60)
    print("HID CAPS / FEATURE PROBE")
    print("=" * 60)
    for d in hid.enumerate(VID, PID):
        path = d["path"].decode() if isinstance(d["path"], bytes) else d["path"]
        print(
            f"\n--- usage_page={d['usage_page']:#06x} usage={d['usage']:#06x} "
            f"if={d['interface_number']} ---"
        )
        print(f"path: {path}")
        handle = open_handle(path)
        if not handle:
            print("  CreateFile FAILED", ctypes.get_last_error())
            continue
        print("  handle OK")

        buf = ctypes.create_unicode_buffer(126)
        if HidD_GetProductString(handle, buf, ctypes.sizeof(buf)):
            print(f"  product: {buf.value!r}")
        if HidD_GetManufacturerString(handle, buf, ctypes.sizeof(buf)):
            print(f"  mfr: {buf.value!r}")

        attrs = HIDD_ATTRIBUTES()
        attrs.Size = ctypes.sizeof(attrs)
        if HidD_GetAttributes(handle, ctypes.byref(attrs)):
            print(
                f"  VID={attrs.VendorID:04X} PID={attrs.ProductID:04X} "
                f"VER={attrs.VersionNumber:04X}"
            )

        preparsed = ctypes.c_void_p()
        caps = None
        if HidD_GetPreparsedData(handle, ctypes.byref(preparsed)):
            caps = HIDP_CAPS()
            status = HidP_GetCaps(preparsed, ctypes.byref(caps))
            print(f"  HidP_GetCaps status={status:#x}")
            print(
                f"  In={caps.InputReportByteLength} Out={caps.OutputReportByteLength} "
                f"Feat={caps.FeatureReportByteLength}"
            )
            print(
                f"  InBtn={caps.NumberInputButtonCaps} InVal={caps.NumberInputValueCaps} "
                f"OutBtn={caps.NumberOutputButtonCaps} OutVal={caps.NumberOutputValueCaps} "
                f"FeatBtn={caps.NumberFeatureButtonCaps} FeatVal={caps.NumberFeatureValueCaps}"
            )
            HidD_FreePreparsedData(preparsed)

        # Probe feature reports
        feat_len = caps.FeatureReportByteLength if caps else 0
        lengths = sorted(
            {2, 3, 4, 5, 8, 9, 16, 17, 32, 33, 64, 65, feat_len} - {0}
        )
        print(f"  Probing feature report lengths: {lengths}")
        for rid in range(0, 64):
            for flen in lengths:
                fbuf = (ctypes.c_ubyte * flen)()
                fbuf[0] = rid
                if HidD_GetFeature(handle, fbuf, flen):
                    data = list(fbuf)
                    nonzero = any(x != 0 for x in data[1:])
                    if nonzero or rid < 8:
                        print(f"  GET_FEATURE rid={rid} len={flen}: {data}")

        # Probe input reports via HidD_GetInputReport
        in_len = caps.InputReportByteLength if caps else 0
        in_lengths = sorted({2, 3, 4, 5, 8, 9, 16, 17, 32, 33, 64, in_len} - {0})
        for rid in range(0, 16):
            for ilen in in_lengths:
                ibuf = (ctypes.c_ubyte * ilen)()
                ibuf[0] = rid
                if HidD_GetInputReport(handle, ibuf, ilen):
                    data = list(ibuf)
                    if any(x != 0 for x in data[1:]) or rid < 4:
                        print(f"  GET_INPUT rid={rid} len={ilen}: {data}")

        CloseHandle(handle)


def probe_write_read():
    print("\n" + "=" * 60)
    print("WRITE / READ COMMAND PROBE (vendor page)")
    print("=" * 60)

    vendor = [
        d
        for d in hid.enumerate(VID, PID)
        if d["usage_page"] == 0xFFB5 or d["usage_page"] > 0xFF00
    ]
    if not vendor:
        # fall back to all non-mouse/keyboard
        vendor = [
            d
            for d in hid.enumerate(VID, PID)
            if d["usage_page"] not in (0x01,) or d["usage"] not in (0x02, 0x06)
        ]

    for d in vendor:
        print(
            f"\nInterface usage_page={d['usage_page']:#x} usage={d['usage']:#x}"
        )
        h = hid.device()
        try:
            h.open_path(d["path"])
            h.set_nonblocking(True)
        except Exception as e:
            print("  open fail:", e)
            continue

        # Common OEM query patterns
        cmds = []
        for rid in range(0, 0x20):
            cmds.append([rid])
            cmds.append([rid, 0x00])
            cmds.append([rid, 0x01])
            cmds.append([rid, 0x80])
            cmds.append([rid, 0x81])
            cmds.append([rid, 0x06])  # battery-ish
            cmds.append([rid, 0x05])  # dpi-ish
            cmds.append([rid, 0x04])
            cmds.append([rid, 0x0B])
            cmds.append([rid, 0x0C])
            cmds.append([rid, 0x10])
            cmds.append([rid, 0x20])
            cmds.append([rid, 0x40])
            cmds.append([rid, 0x50])
            cmds.append([rid, 0xAA, 0x55])
            cmds.append([rid, 0x55, 0xAA])
            cmds.append([rid, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

        # Also try no-report-id packets
        for b in range(0, 0x30):
            cmds.append([b])
            cmds.append([0x00, b])

        seen = set()
        hits = 0
        for cmd in cmds:
            key = tuple(cmd)
            if key in seen:
                continue
            seen.add(key)
            # pad to several lengths
            for pad in (8, 16, 32, 64):
                packet = bytes(cmd + [0] * max(0, pad - len(cmd)))
                try:
                    h.write(packet)
                except Exception:
                    continue
                time.sleep(0.015)
                for _ in range(3):
                    r = h.read(64)
                    if r:
                        print(f"  WRITE {list(packet[:8])}... -> {list(r)}")
                        hits += 1
                        break
                # also try get_feature after write
                try:
                    rid = cmd[0]
                    data = h.get_feature_report(rid, 64)
                    if data and any(b != 0 for b in data[1:]):
                        print(f"  AFTER write feat rid={rid}: {list(data[:16])}")
                        hits += 1
                except Exception:
                    pass

        print(f"  hits: {hits}")
        # Live listen while user might click DPI
        print("  Listening 3s for unsolicited reports (press DPI button if you can)...")
        t0 = time.time()
        while time.time() - t0 < 3.0:
            r = h.read(64)
            if r:
                print("  LIVE:", list(r))
        h.close()


def probe_system_controller():
    """System controller (usage 0x80) sometimes carries battery."""
    print("\n" + "=" * 60)
    print("SYSTEM CONTROLLER / CONSUMER LISTEN")
    print("=" * 60)
    for d in hid.enumerate(VID, PID):
        if d["usage_page"] not in (0x01, 0x0C) or d["usage"] not in (0x80, 0x01):
            continue
        print(f"\nusage_page={d['usage_page']:#x} usage={d['usage']:#x}")
        h = hid.device()
        try:
            h.open_path(d["path"])
            h.set_nonblocking(True)
        except Exception as e:
            print("  open fail:", e)
            continue
        t0 = time.time()
        while time.time() - t0 < 2.0:
            r = h.read(64)
            if r:
                print("  LIVE:", list(r))
        h.close()


def main():
    print("Enumerating VID=0x32C2 PID=0x0066...")
    devs = list(hid.enumerate(VID, PID))
    print(f"Found {len(devs)} HID collections")
    for d in devs:
        print(
            f"  UP={d['usage_page']:#06x} U={d['usage']:#06x} "
            f"IF={d['interface_number']} product={d.get('product_string')!r}"
        )

    probe_caps()
    probe_write_read()
    probe_system_controller()
    print("\nDONE")


if __name__ == "__main__":
    main()
