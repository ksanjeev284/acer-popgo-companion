"""Probe charge indicators: full dumps, all 32C2 devices, Windows batteries."""
from __future__ import annotations

import subprocess
import time

import hid

VID, PID = 0x32C2, 0x0066
RID = 0xB5


def open_v():
    for d in hid.enumerate(VID, PID):
        if d.get("usage_page") == 0xFFB5:
            h = hid.device()
            h.open_path(d["path"])
            h.set_nonblocking(True)
            return h
    raise SystemExit("no device")


def drain(h, t=0.05):
    out = []
    t0 = time.time()
    while time.time() - t0 < t:
        r = h.read(64)
        if r:
            out.append(list(r))
            t0 = time.time()
        else:
            time.sleep(0.001)
    return out


def q(h, payload, listen=0.5):
    drain(h, 0.05)
    h.write(bytes([RID] + (list(payload) + [0] * 7)[:7]))
    time.sleep(0.02)
    return drain(h, listen)


def reconstruct(pkts, cmd):
    blob = bytearray(256)
    for p in pkts:
        if len(p) >= 8 and p[0] == RID and p[1] == cmd:
            off, size = p[2], p[3]
            data = p[4 : 4 + min(size, 4)]
            if off + len(data) <= len(blob):
                blob[off : off + len(data)] = data
    return blob


def main():
    h = open_v()
    for cmd in (2, 5):
        pkts = q(h, [cmd], 0.6)
        blob = reconstruct(pkts, cmd)
        print(f"CMD{cmd:02X} first 128:", list(blob[:128]))
        print(f"CMD{cmd:02X} hex:", blob[:128].hex(" "))

    print("\n=== status samples ===")
    for i in range(8):
        p = q(h, [0x01], 0.1)
        print(i, p[0] if p else None)

    print("\n=== all 32C2 devices ===")
    for d in hid.enumerate():
        if d["vendor_id"] == 0x32C2:
            print(
                hex(d["product_id"]),
                d.get("product_string"),
                "UP",
                hex(d.get("usage_page") or 0),
                "U",
                hex(d.get("usage") or 0),
                "if",
                d.get("interface_number"),
            )

    print("\n=== Windows battery devices (CIM) ===")
    ps = r"""
Get-CimInstance -Namespace root\wmi -ClassName BatteryStatus -ErrorAction SilentlyContinue |
  Select-Object InstanceName, RemainingCapacity, ChargeRate, DischargeRate, Charging, PowerOnline |
  ConvertTo-Json -Compress
Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
  Where-Object { $_.InstanceId -match '32C2|BTHENUM' -or $_.FriendlyName -match 'Battery' } |
  Select-Object Status, Class, FriendlyName, InstanceId |
  ConvertTo-Json -Compress
"""
    try:
        r = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps],
            text=True,
            errors="replace",
            timeout=30,
        )
        print(r[:4000])
    except Exception as e:
        print("ps err", e)

    h.close()
    print("DONE")


if __name__ == "__main__":
    main()
