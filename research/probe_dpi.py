"""Focus on battery + DPI read/write for Acer PopGo."""
from __future__ import annotations

import time
import hid

VID, PID = 0x32C2, 0x0066
RID = 0xB5
DPI_LEVELS = [800, 1200, 1600, 2400, 3200, 4000, 5000, 6400]


def open_vendor():
    for d in hid.enumerate(VID, PID):
        if d["usage_page"] == 0xFFB5:
            h = hid.device()
            h.open_path(d["path"])
            h.set_nonblocking(True)
            return h
    raise RuntimeError("not found")


def drain(h, timeout=0.12):
    pkts = []
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = h.read(64)
        if r:
            pkts.append(list(r))
            t0 = time.time()
        else:
            time.sleep(0.002)
    return pkts


def send(h, payload, listen=0.15):
    drain(h, 0.05)
    pkt = bytes([RID] + (list(payload) + [0] * 7)[:7])
    h.write(pkt)
    time.sleep(0.015)
    return drain(h, listen)


def get_status(h):
    r = send(h, [0x01], 0.12)
    return r[0] if r else None


def get_cmd(h, cmd, listen=0.15):
    return send(h, [cmd], listen)


def main():
    h = open_vendor()
    print("Device open\n", flush=True)

    # Status
    for i in range(3):
        s = get_status(h)
        print(f"status01 #{i}: {s}", flush=True)
        if s:
            print(
                f"  battery?=byte3={s[3]}  b2={s[2]} b4-7={s[4:]}",
                flush=True,
            )

    # CMD04 - possible DPI index
    print("\nCMD04 (dpi index?):", get_cmd(h, 0x04), flush=True)
    print("CMD20:", get_cmd(h, 0x20), flush=True)
    print("CMD23:", get_cmd(h, 0x23), flush=True)
    print("CMD29:", get_cmd(h, 0x29), flush=True)
    print("CMD2A:", get_cmd(h, 0x2A), flush=True)
    print("CMD2B:", get_cmd(h, 0x2B), flush=True)
    print("CMD2C:", get_cmd(h, 0x2C), flush=True)
    print("CMD2D:", get_cmd(h, 0x2D), flush=True)
    print("CMD2E:", get_cmd(h, 0x2E), flush=True)

    # Reconstruct CMD05 fully
    print("\n=== CMD05 full reconstruct ===", flush=True)
    resp = send(h, [0x05], listen=0.6)
    blob = bytearray(256)
    for p in resp:
        if len(p) >= 8 and p[0] == RID and p[1] == 5:
            off, size = p[2], p[3]
            data = p[4 : 4 + min(size, 4)]
            if off + len(data) <= len(blob):
                blob[off : off + len(data)] = data
    print("blob hex:", blob[:128].hex(" "), flush=True)
    print("blob list:", list(blob[:128]), flush=True)
    # interpret as u16 LE pairs
    print("u16 LE pairs:", flush=True)
    for i in range(0, 64, 2):
        val = blob[i] | (blob[i + 1] << 8)
        if val:
            print(f"  offset {i:3d}: {val} (0x{val:04X})", flush=True)

    # Try WRITE patterns for DPI set, then re-read cmd04/status
    print("\n=== TRY SET DPI ===", flush=True)
    # pattern families
    patterns = []
    for idx, dpi in enumerate(DPI_LEVELS):
        lo, hi = dpi & 0xFF, (dpi >> 8) & 0xFF
        patterns += [
            (f"04 set idx {idx}", [0x04, idx, 0, 0, 0, 0, 0]),
            (f"04 set idx+1 {idx+1}", [0x04, idx + 1, 0, 0, 0, 0, 0]),
            (f"84 set idx {idx}", [0x84, idx, 0, 0, 0, 0, 0]),
            (f"05 write idx {idx}", [0x05, 0x01, idx, 0, 0, 0, 0]),
            (f"05 write dpi {dpi}", [0x05, 0x01, lo, hi, 0, 0, 0]),
            (f"03 set idx {idx}", [0x03, idx, 0, 0, 0, 0, 0]),
            (f"06 set idx {idx}", [0x06, idx, 0, 0, 0, 0, 0]),
            (f"07 set idx {idx}", [0x07, idx, 0, 0, 0, 0, 0]),
            (f"08 set idx {idx}", [0x08, idx, 0, 0, 0, 0, 0]),
            (f"09 set idx {idx}", [0x09, idx, 0, 0, 0, 0, 0]),
            (f"0A set idx {idx}", [0x0A, idx, 0, 0, 0, 0, 0]),
            (f"0B set idx {idx}", [0x0B, idx, 0, 0, 0, 0, 0]),
            (f"0C set idx {idx}", [0x0C, idx, 0, 0, 0, 0, 0]),
            (f"20 set idx {idx}", [0x20, idx, 0, 0, 0, 0, 0]),
            (f"20 set dpi {dpi}", [0x20, lo, hi, 0, 0, 0, 0]),
            (f"21 set idx {idx}", [0x21, idx, 0, 0, 0, 0, 0]),
            (f"22 set idx {idx}", [0x22, idx, 0, 0, 0, 0, 0]),
            (f"23 set idx {idx}", [0x23, idx, 0, 0, 0, 0, 0]),
            (f"24 set idx {idx}", [0x24, idx, 0, 0, 0, 0, 0]),
            (f"write mem dpi@off", [0x02, 0x01, 0x00, idx, 0, 0, 0]),  # speculative
        ]

    # Only test setting to a few indices and watch for status/cmd04 changes
    baseline04 = get_cmd(h, 0x04)
    baseline01 = get_status(h)
    print(f"baseline 01={baseline01} 04={baseline04}", flush=True)

    # Compact set of write trials focusing on index 0,3,7
    trials = []
    for idx in [0, 1, 2, 3, 4, 5, 6, 7]:
        dpi = DPI_LEVELS[idx]
        lo, hi = dpi & 0xFF, (dpi >> 8) & 0xFF
        trials += [
            [0x04, idx],
            [0x04, idx, 1],
            [0x84, idx],
            [0x04, 0x01, idx],
            [0x04, 0x00, idx],
            [0x05, 0x00, idx],  # might be dangerous (overwrite config)
            [0x03, idx],
            [0x06, idx],
            [0x0B, idx],
            [0x20, idx],
            [0x20, lo, hi],
            [0x23, idx],
            [0x23, lo, hi],
            [0x24, idx],
            [0x25, idx],
            [0x30, idx],
            [0x40, idx],
            [0x50, idx],
            # write-style: cmd | 0x80
            [0x80 | 0x04, idx],
            [0x80 | 0x05, 0, 4, idx, 0, 0, 0],
            [0x80 | 0x01, idx],
        ]

    seen_change = []
    for t in trials:
        before01 = get_status(h)
        before04 = get_cmd(h, 0x04)
        before20 = get_cmd(h, 0x20)
        resp = send(h, t, 0.1)
        after01 = get_status(h)
        after04 = get_cmd(h, 0x04)
        after20 = get_cmd(h, 0x20)
        changed = (before01 != after01) or (before04 != after04) or (before20 != after20)
        if changed or (resp and resp[0][1] not in (1, 2, 4, 5, 0x20)):
            print(
                f"TRY {t}: resp={resp[:1] if resp else None}\n"
                f"  01 {before01} -> {after01}\n"
                f"  04 {before04} -> {after04}\n"
                f"  20 {before20} -> {after20}",
                flush=True,
            )
            if changed:
                seen_change.append(t)

    print(f"\ncommands that changed state: {seen_change}", flush=True)

    # Interactive-ish: poll status for 10s asking user to press DPI
    print(
        "\n*** For 12 seconds: press the DPI button on the mouse a few times ***",
        flush=True,
    )
    print("Watching status01 / cmd04 / cmd20 for changes...\n", flush=True)
    last = None
    t0 = time.time()
    while time.time() - t0 < 12:
        s01 = get_status(h)
        s04 = get_cmd(h, 0x04)
        s20 = get_cmd(h, 0x20)
        cur = (tuple(s01) if s01 else None, tuple(s04[0]) if s04 else None, tuple(s20[0]) if s20 else None)
        if cur != last:
            print(f"t={time.time()-t0:5.1f}s 01={s01} 04={s04} 20={s20}", flush=True)
            last = cur
        time.sleep(0.25)

    h.close()
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
