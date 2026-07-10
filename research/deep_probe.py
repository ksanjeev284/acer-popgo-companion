"""
Deep probe of Acer PopGo / OnMicro 32C2:0066 over 2.4G HID.
Collects: interfaces, caps, full cmd responses, config dumps, live traffic.
"""
from __future__ import annotations

import json
import struct
import time
from pathlib import Path

import hid

VID, PID = 0x32C2, 0x0066
RID = 0xB5
OUT = Path(__file__).resolve().parent / "deep_probe_out"
OUT.mkdir(exist_ok=True)


def open_by_usage(usage_page: int | None = None, usage: int | None = None):
    for d in hid.enumerate(VID, PID):
        if usage_page is not None and d.get("usage_page") != usage_page:
            continue
        if usage is not None and d.get("usage") != usage:
            continue
        h = hid.device()
        try:
            h.open_path(d["path"])
            h.set_nonblocking(True)
            return h, d
        except Exception as e:
            print("open fail", e)
    return None, None


def drain(h, t=0.06):
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


def query(h, payload, listen=0.12, drain_first=0.08):
    drain(h, drain_first)
    pkt = bytes([RID] + (list(payload) + [0] * 7)[:7])
    try:
        h.write(pkt)
    except Exception as e:
        return [], str(e)
    time.sleep(0.02)
    return drain(h, listen), None


def reconstruct_dump(pkts, cmd_echo):
    blob = bytearray(512)
    meta = []
    for p in pkts:
        if len(p) < 8 or p[0] != RID:
            continue
        typ, off, size = p[1], p[2], p[3]
        data = p[4 : 4 + min(size, 4)]
        meta.append({"type": typ, "off": off, "size": size, "data": data, "raw": p})
        if off + len(data) <= len(blob):
            blob[off : off + len(data)] = data
    return bytes(blob), meta


def main():
    report = {
        "vid": hex(VID),
        "pid": hex(PID),
        "interfaces": [],
        "cmd_scan": {},
        "dumps": {},
        "status_timeline": [],
        "live_listen": [],
        "notes": [],
    }

    print("=== Enumerate all HID for this VID/PID ===")
    for d in hid.enumerate(VID, PID):
        info = {
            "usage_page": hex(d.get("usage_page") or 0),
            "usage": hex(d.get("usage") or 0),
            "interface": d.get("interface_number"),
            "product": d.get("product_string"),
            "manufacturer": d.get("manufacturer_string"),
            "release": d.get("release_number"),
            "path": (d["path"].decode() if isinstance(d["path"], bytes) else d["path"])[:160],
        }
        report["interfaces"].append(info)
        print(info)

    h, d = open_by_usage(0xFFB5, 1)
    if not h:
        print("NO VENDOR IFACE")
        (OUT / "report.json").write_text(json.dumps(report, indent=2))
        return
    print("Opened vendor 0xFFB5")

    # --- Status stability ---
    print("\n=== CMD 01 timeline (30 samples) ===")
    for i in range(30):
        pkts, err = query(h, [0x01], 0.1)
        p = pkts[0] if pkts else None
        report["status_timeline"].append(p)
        if i % 5 == 0:
            print(i, p)
        time.sleep(0.05)

    # Unique status packets
    uniq = {tuple(p) for p in report["status_timeline"] if p}
    print("unique status count:", len(uniq))
    for u in uniq:
        print(" ", list(u))

    # --- Full command scan 0x00-0xFF ---
    print("\n=== Full cmd scan 0x00-0xFF ===")
    for cmd in range(256):
        pkts, err = query(h, [cmd], 0.07 if cmd not in (2, 5) else 0.45)
        if not pkts:
            continue
        entry = {
            "n": len(pkts),
            "first": pkts[0],
            "all": pkts if len(pkts) <= 8 else pkts[:4] + ["..."] + pkts[-2:],
        }
        report["cmd_scan"][f"{cmd:02X}"] = entry
        if len(pkts) > 5:
            print(f"CMD {cmd:02X}: {len(pkts)} pkts first={pkts[0]}")
        else:
            print(f"CMD {cmd:02X}: {pkts[0]}")

    # --- Dumps 02 and 05 full reconstruct ---
    for cmd in (0x02, 0x05):
        print(f"\n=== Reconstruct dump CMD {cmd:02X} ===")
        pkts, _ = query(h, [cmd], 0.7)
        blob, meta = reconstruct_dump(pkts, cmd)
        report["dumps"][f"{cmd:02X}"] = {
            "packet_count": len(pkts),
            "blob_hex": blob[:256].hex(" "),
            "blob_list": list(blob[:256]),
            "non_zero_offsets": [
                i for i, b in enumerate(blob[:256]) if b != 0
            ],
        }
        (OUT / f"dump_{cmd:02X}.bin").write_bytes(blob)
        print("nonzero offsets:", report["dumps"][f"{cmd:02X}"]["non_zero_offsets"][:40])
        print("hex[:128]:", blob[:128].hex(" "))
        # u16 LE pairs non-zero
        print("u16 LE non-zero:")
        for i in range(0, 128, 2):
            v = blob[i] | (blob[i + 1] << 8)
            if v:
                print(f"  @{i:3d}: {v:5d} (0x{v:04X})")

    # --- Multi-byte query patterns ---
    print("\n=== Multi-byte patterns ===")
    patterns = []
    for cmd in [0x01, 0x04, 0x06, 0x0B, 0x10, 0x11, 0x12, 0x20, 0x21, 0x23]:
        for a in range(0, 8):
            patterns.append([cmd, a])
            patterns.append([cmd, a, 0, 0, 0, 0, 0])
        for dpi in (800, 1600, 3200, 6400):
            patterns.append([cmd, dpi & 0xFF, (dpi >> 8) & 0xFF])
    multi = {}
    for pl in patterns:
        pkts, _ = query(h, pl, 0.06)
        if not pkts:
            continue
        key = " ".join(f"{b:02X}" for b in pl[:4])
        # skip giant dumps
        if len(pkts) > 5:
            continue
        multi[key] = pkts[0]
    report["multi_byte"] = multi
    print(f"multi-byte hits: {len(multi)}")
    for k, v in list(multi.items())[:40]:
        print(f"  {k} -> {v}")

    # --- Live listen: movement / buttons / DPI ---
    print("\n=== Live listen 12s (move mouse, click, press DPI if possible) ===")
    # also open mouse iface if possible for comparison - may fail access
    t0 = time.time()
    while time.time() - t0 < 12:
        r = h.read(64)
        if r:
            report["live_listen"].append({"t": round(time.time() - t0, 3), "data": list(r)})
            print(f"t={time.time()-t0:5.2f}", list(r))
        else:
            time.sleep(0.002)
    print("live packets:", len(report["live_listen"]))

    # --- System controller listen ---
    print("\n=== System controller 0x01/0x80 listen 3s ===")
    h.close()
    hs, _ = open_by_usage(0x01, 0x80)
    if hs:
        t0 = time.time()
        while time.time() - t0 < 3:
            r = hs.read(64)
            if r:
                print("SYS", list(r))
                report["live_listen"].append({"iface": "sys", "data": list(r)})
            else:
                time.sleep(0.002)
        hs.close()
    else:
        print("sys open fail")

    # reopen vendor for final status
    h, _ = open_by_usage(0xFFB5, 1)
    if h:
        pkts, _ = query(h, [0x01], 0.12)
        report["final_status"] = pkts[0] if pkts else None
        print("final status", report["final_status"])
        h.close()

    # Analysis notes
    report["notes"] = [
        "Report ID 0xB5 on vendor page 0xFFB5, 8-byte I/O.",
        "CMD 0x01 status: byte3 often frozen firmware %; mid bytes may be mV.",
        "CMD 0x02/0x05 return multipacket memory dumps.",
        "DPI set not confirmed; hardware DPI button is primary.",
        "Charge LED is local; no HID charge bit observed.",
    ]

    out_json = OUT / "report.json"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\nWrote", out_json)
    print("DONE")


if __name__ == "__main__":
    main()
