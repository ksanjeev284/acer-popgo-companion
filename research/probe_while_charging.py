"""Run this WHILE the mouse USB-C is plugged into the PC (charging)."""
from __future__ import annotations

import subprocess
import time

import hid

VID, PID = 0x32C2, 0x0066
RID = 0xB5


def main() -> None:
    print("=== ALL relevant HID devices ===")
    for d in hid.enumerate():
        vs = d.get("vendor_id") or 0
        ps = d.get("product_id") or 0
        name = d.get("product_string") or ""
        mfr = d.get("manufacturer_string") or ""
        path = d.get("path") or b""
        if isinstance(path, bytes):
            path = path.decode("utf-8", "replace")
        interesting = (
            vs == VID
            or "2.4" in name
            or "mouse" in name.lower()
            or "acer" in name.lower()
        )
        if not interesting:
            continue
        print(
            f"VID={vs:04X} PID={ps:04X} UP={d.get('usage_page'):#x} "
            f"U={d.get('usage'):#x} IF={d.get('interface_number')} "
            f"name={name!r} mfr={mfr!r}"
        )
        print("  path", path[:140])

    print("\n=== PnP devices (32C2 / Battery / Mouse) ===")
    ps = r"""
Get-PnpDevice -PresentOnly | Where-Object {
  $_.InstanceId -match 'VID_32C2' -or
  $_.FriendlyName -match 'Battery|Charging|2\.4G|Wireless'
} | Select-Object Status, Class, FriendlyName, InstanceId |
  ConvertTo-Json -Depth 4
"""
    try:
        r = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps],
            text=True,
            errors="replace",
            timeout=30,
        )
        print(r[:6000])
    except Exception as e:
        print("ps err", e)

    print("\n=== Vendor status CMD 01 x12 ===")
    dev = None
    for d in hid.enumerate(VID, PID):
        if d.get("usage_page") == 0xFFB5:
            dev = hid.device()
            dev.open_path(d["path"])
            dev.set_nonblocking(True)
            break
    if dev is None:
        print("NO VENDOR INTERFACE — dongle missing?")
        return

    def drain(t=0.08):
        out = []
        t0 = time.time()
        while time.time() - t0 < t:
            r = dev.read(64)
            if r:
                out.append(list(r))
                t0 = time.time()
            else:
                time.sleep(0.001)
        return out

    def q(payload, listen=0.12):
        drain(0.05)
        dev.write(bytes([RID] + (list(payload) + [0] * 7)[:7]))
        time.sleep(0.025)
        return drain(listen)

    for i in range(12):
        pkts = q([0x01], 0.12)
        print(i, pkts[0] if pkts else None)

    print("\n=== Short cmd scan 0x00-0x40 ===")
    for cmd in range(0x41):
        pkts = q([cmd], 0.07)
        if not pkts:
            continue
        if len(pkts) > 3:
            print(f"{cmd:02X} n={len(pkts)} first={pkts[0]}")
        else:
            print(f"{cmd:02X}", pkts[0])

    # Also try opening EVERY 32C2 path and read status if possible
    print("\n=== Try all 32C2 vendor-like paths ===")
    for d in hid.enumerate(VID):
        if d.get("usage_page") not in (0xFFB5, 0xFF00) and d.get("usage_page", 0) < 0xFF00:
            continue
        try:
            h = hid.device()
            h.open_path(d["path"])
            h.set_nonblocking(True)
            h.write(bytes([RID, 1, 0, 0, 0, 0, 0, 0]))
            time.sleep(0.03)
            r = h.read(64)
            print("path UP", hex(d.get("usage_page") or 0), "->", list(r) if r else None)
            h.close()
        except Exception as e:
            print("open fail", e)

    dev.close()
    print("DONE")


if __name__ == "__main__":
    main()
