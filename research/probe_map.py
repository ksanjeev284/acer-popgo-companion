"""Carefully map Acer PopGo vendor protocol (report ID 0xB5)."""
from __future__ import annotations

import time
import hid

VID, PID = 0x32C2, 0x0066
RID = 0xB5


def open_vendor():
    for d in hid.enumerate(VID, PID):
        if d["usage_page"] == 0xFFB5:
            h = hid.device()
            h.open_path(d["path"])
            h.set_nonblocking(True)
            return h
    raise RuntimeError("vendor iface missing")


def drain(h, timeout=0.15):
    pkts = []
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = h.read(64)
        if r:
            pkts.append(list(r))
            t0 = time.time()  # keep reading while streaming
        else:
            time.sleep(0.003)
    return pkts


def query(h, payload, listen=0.25):
    """Send [0xB5, ...payload padded to 7 bytes] and collect all response packets."""
    # fully drain first
    drain(h, 0.08)
    pkt = bytes([RID] + (list(payload) + [0] * 7)[:7])
    h.write(pkt)
    time.sleep(0.02)
    return drain(h, listen)


def main():
    h = open_vendor()
    print("opened\n", flush=True)

    # Single-command scan: cmd 0x00..0x3F with zeros
    print("=== CMD SCAN 0x00-0x3F ===", flush=True)
    for cmd in range(0x00, 0x40):
        resp = query(h, [cmd], listen=0.2)
        if resp:
            print(f"CMD {cmd:02X}: {len(resp)} pkts", flush=True)
            for i, p in enumerate(resp[:12]):
                print(f"  [{i}] {p}", flush=True)
            if len(resp) > 12:
                print(f"  ... +{len(resp)-12} more", flush=True)

    # Extended interesting commands
    print("\n=== EXTENDED CMDS ===", flush=True)
    for cmd in [0x40, 0x50, 0x60, 0x67, 0x70, 0x80, 0x81, 0x90, 0xA0, 0xAA, 0xB0, 0xC0, 0xD0, 0xE0, 0xF0, 0xFF]:
        resp = query(h, [cmd], listen=0.2)
        if resp:
            print(f"CMD {cmd:02X}: {len(resp)} pkts", flush=True)
            for i, p in enumerate(resp[:8]):
                print(f"  [{i}] {p}", flush=True)

    # Subcommands for cmd 0x01 (status?)
    print("\n=== CMD 01 variants ===", flush=True)
    for sub in range(0, 16):
        resp = query(h, [0x01, sub], listen=0.15)
        if resp:
            print(f"01 {sub:02X}: {resp}", flush=True)

    # Subcommands for likely battery/dpi
    print("\n=== CMD 06/05/04/03/02 variants ===", flush=True)
    for cmd in [0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C]:
        for sub in [0, 1, 2, 3, 4, 5, 6, 7, 8]:
            resp = query(h, [cmd, sub], listen=0.12)
            if resp:
                print(f"{cmd:02X} {sub:02X}: {resp[:4]}{'...' if len(resp)>4 else ''}", flush=True)

    # Re-read cmd 01 several times for stability (battery may change slowly)
    print("\n=== CMD 01 stability x5 ===", flush=True)
    for i in range(5):
        resp = query(h, [0x01], listen=0.15)
        print(f"  #{i}: {resp}", flush=True)
        time.sleep(0.3)

    # Try setting DPI: common patterns
    # After set, re-read status
    print("\n=== DPI SET ATTEMPTS ===", flush=True)
    dpi_levels = [800, 1200, 1600, 2400, 3200, 4000, 5000, 6400]
    set_patterns = []
    for i, dpi in enumerate(dpi_levels):
        set_patterns.append((f"idx{i}", [0x02, i]))
        set_patterns.append((f"dpi_lo_hi{dpi}", [0x02, dpi & 0xFF, (dpi >> 8) & 0xFF]))
        set_patterns.append((f"cmd05_idx{i}", [0x05, i]))
        set_patterns.append((f"cmd05_dpi{dpi}", [0x05, dpi & 0xFF, (dpi >> 8) & 0xFF]))
        set_patterns.append((f"cmd04_idx{i}", [0x04, i]))
        set_patterns.append((f"cmd03_idx{i}", [0x03, i]))
        set_patterns.append((f"cmd10_idx{i}", [0x10, i]))
        set_patterns.append((f"cmd11_idx{i}", [0x11, i]))
        set_patterns.append((f"cmd20_idx{i}", [0x20, i]))

    # Only try a subset carefully and re-read 01
    baseline = query(h, [0x01], listen=0.15)
    print(f"baseline 01: {baseline}", flush=True)

    trials = [
        [0x02, 0x00],
        [0x02, 0x01],
        [0x02, 0x02],
        [0x02, 0x03],
        [0x03, 0x00],
        [0x03, 0x01],
        [0x04, 0x00],
        [0x04, 0x01],
        [0x05, 0x00],
        [0x05, 0x01],
        [0x05, 0x02],
        [0x05, 0x03],
        [0x05, 0x04],
        [0x05, 0x05],
        [0x05, 0x06],
        [0x05, 0x07],
        [0x06, 0x00],
        [0x06, 0x01],
        [0x10, 0x00],
        [0x10, 0x01],
        [0x11, 0x00],
        [0x12, 0x00],
        [0x20, 0x00],
        [0x20, 0x01],
        [0x01, 0x00],  # get status again
        [0x01, 0x01],
        [0x01, 0x02],
        # write-style with length
        [0x82, 0x00],
        [0x85, 0x00],
        [0x85, 0x01],
        [0x85, 0x02],
        [0x85, 0x03],
        [0xC1, 0x00],
        [0xC2, 0x00],
        [0xC5, 0x00],
    ]
    for t in trials:
        resp = query(h, t, listen=0.12)
        status = query(h, [0x01], listen=0.12)
        print(f"SET {t} -> resp={resp[:2] if resp else None} status01={status}", flush=True)

    # Dump full cmd 02 stream and decode
    print("\n=== FULL DUMP CMD 02 ===", flush=True)
    resp = query(h, [0x02], listen=0.5)
    print(f"{len(resp)} packets", flush=True)
    # reconstruct if format is [B5, type, offset, size, d0, d1, d2, d3]
    blob = bytearray(256)
    for p in resp:
        if len(p) >= 8 and p[0] == RID:
            typ, off, size = p[1], p[2], p[3]
            data = p[4:4 + size] if size <= 4 else p[4:]
            print(f"  type={typ} off={off} size={size} data={list(data)} raw={p}", flush=True)
            if off + len(data) <= len(blob):
                blob[off:off + len(data)] = data
    print("reconstructed blob:", list(blob[:128]), flush=True)

    # Same for other dump-like cmds
    for cmd in [0x03, 0x04, 0x05, 0x06, 0x07, 0x08]:
        print(f"\n=== FULL DUMP CMD {cmd:02X} ===", flush=True)
        resp = query(h, [cmd], listen=0.4)
        print(f"{len(resp)} packets", flush=True)
        for p in resp[:20]:
            print(f"  {p}", flush=True)

    h.close()
    print("\nDONE", flush=True)


if __name__ == "__main__":
    main()
