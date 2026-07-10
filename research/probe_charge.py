"""Poll status packets; compare bytes while charging vs on battery."""
from __future__ import annotations

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


def drain(h, t=0.1):
    pkts = []
    t0 = time.time()
    while time.time() - t0 < t:
        r = h.read(64)
        if r:
            pkts.append(list(r))
            t0 = time.time()
        else:
            time.sleep(0.002)
    return pkts


def q(h, payload, listen=0.12):
    drain(h, 0.03)
    h.write(bytes([RID] + (list(payload) + [0] * 7)[:7]))
    time.sleep(0.015)
    return drain(h, listen)


def main():
    h = open_v()
    print("Polling CMD 01/04 and scanning 0x00-0x3F for charge-related bits...")
    print("If you can, plug/unplug the mouse USB-C cable during this run.\n")

    seen = {}
    t0 = time.time()
    while time.time() - t0 < 12:
        for cmd in [0x01, 0x04, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x20, 0x21, 0x22, 0x23, 0x29, 0x2B, 0x2C, 0x2D, 0x2E]:
            pkts = q(h, [cmd], 0.07)
            if not pkts:
                continue
            p = pkts[0]
            key = (cmd, tuple(p))
            if seen.get(cmd) != tuple(p):
                print(f"t={time.time()-t0:5.1f}s CMD{cmd:02X}: {p}")
                seen[cmd] = tuple(p)
        time.sleep(0.25)

    # bit-level view of status
    print("\n--- 20 samples of CMD 01 ---")
    samples = []
    for _ in range(20):
        pkts = q(h, [0x01], 0.08)
        if pkts:
            samples.append(pkts[0])
            print(samples[-1])
        time.sleep(0.1)

    if samples:
        # show which bytes vary
        print("\nByte variance across samples:")
        for i in range(8):
            vals = sorted({s[i] for s in samples if len(s) > i})
            print(f"  byte[{i}]: {vals}")

    # full command scan looking for packets with 0/1 flags near battery
    print("\n--- CMD scan 0x00-0x50 (one shot) ---")
    for cmd in range(0x00, 0x51):
        pkts = q(h, [cmd], 0.08)
        if pkts:
            print(f"CMD{cmd:02X}: {pkts[0]}" + (f" (+{len(pkts)-1})" if len(pkts) > 1 else ""))

    h.close()
    print("\ndone")


if __name__ == "__main__":
    main()
