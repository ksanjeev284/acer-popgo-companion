"""
Even deeper: HID report descriptors for dongle + BLE, Windows battery devices,
retry 2.4G vendor while BLE connected, decode descriptors.
"""
from __future__ import annotations

import json
import struct
import subprocess
import time
from pathlib import Path

import hid

OUT = Path(__file__).resolve().parent / "deep_ble_out"
OUT.mkdir(exist_ok=True)

VID = 0x32C2


def rd_to_bytes(rd) -> bytes:
    if rd is None:
        return b""
    if isinstance(rd, (bytes, bytearray)):
        return bytes(rd)
    if isinstance(rd, list):
        return bytes(rd)
    return bytes(rd)


def dump_report_descriptors() -> list:
    results = []
    for d in hid.enumerate(VID):
        path = d.get("path")
        path_s = (
            path.decode("utf-8", "replace")
            if isinstance(path, (bytes, bytearray))
            else str(path)
        )
        entry = {
            "vid": f"0x{d['vendor_id']:04X}",
            "pid": f"0x{d['product_id']:04X}",
            "usage_page": f"0x{d.get('usage_page') or 0:X}",
            "usage": f"0x{d.get('usage') or 0:X}",
            "product": d.get("product_string"),
            "interface": d.get("interface_number"),
            "path": path_s[:180],
        }
        try:
            h = hid.device()
            h.open_path(path)
            rd = None
            if hasattr(h, "get_report_descriptor"):
                rd = h.get_report_descriptor()
            raw = rd_to_bytes(rd)
            entry["rd_len"] = len(raw)
            entry["rd_hex"] = raw.hex()
            # save
            tag = f"{d['product_id']:04x}_up{d.get('usage_page') or 0:x}_u{d.get('usage') or 0:x}"
            if raw:
                (OUT / f"rd_{tag}.bin").write_bytes(raw)
                entry["decoded"] = decode_hid_rd(raw)
            h.close()
        except Exception as e:
            entry["err"] = str(e)
        results.append(entry)
        print(
            f"{entry['vid']}:{entry['pid']} up={entry['usage_page']} "
            f"u={entry['usage']} rd={entry.get('rd_len')} {entry.get('product')}"
        )
        if entry.get("rd_hex"):
            print(f"  {entry['rd_hex']}")
            if entry.get("decoded"):
                for line in entry["decoded"]:
                    print(f"  | {line}")
    return results


def decode_hid_rd(data: bytes) -> list[str]:
    """Minimal HID report descriptor decoder (short items)."""
    lines = []
    i = 0
    usage_page = None
    while i < len(data):
        prefix = data[i]
        i += 1
        btype = (prefix >> 2) & 0x03
        btag = (prefix >> 4) & 0x0F
        size = prefix & 0x03
        if size == 3:
            size = 4
        val = 0
        if size and i + size <= len(data):
            val = int.from_bytes(data[i : i + size], "little", signed=False)
            # signed for some
            if btype == 1 and btag in (0x1, 0x2) and size:  # logical min/max can be signed
                sval = int.from_bytes(data[i : i + size], "little", signed=True)
            else:
                sval = val
            i += size
        else:
            sval = 0

        type_names = {0: "Main", 1: "Global", 2: "Local", 3: "Reserved"}
        if btype == 0:  # Main
            main = {
                0x8: "Input",
                0x9: "Output",
                0xA: "Collection",
                0xB: "Feature",
                0xC: "End Collection",
            }.get(btag, f"Main({btag})")
            if btag == 0xA:
                coll = {0: "Physical", 1: "Application", 2: "Logical"}.get(val, str(val))
                lines.append(f"Collection({coll})")
            elif btag == 0xC:
                lines.append("End Collection")
            else:
                bits = []
                if val & 1:
                    bits.append("Const")
                else:
                    bits.append("Data")
                if val & 2:
                    bits.append("Var")
                else:
                    bits.append("Array")
                if val & 4:
                    bits.append("Rel")
                else:
                    bits.append("Abs")
                lines.append(f"{main}({val:#x} {','.join(bits)})")
        elif btype == 1:  # Global
            g = {
                0: "UsagePage",
                1: "LogicalMin",
                2: "LogicalMax",
                3: "PhysicalMin",
                4: "PhysicalMax",
                5: "UnitExponent",
                6: "Unit",
                7: "ReportSize",
                8: "ReportID",
                9: "ReportCount",
                0xA: "Push",
                0xB: "Pop",
            }.get(btag, f"Global({btag})")
            if btag == 0:
                usage_page = val
                names = {
                    0x01: "GenericDesktop",
                    0x0C: "Consumer",
                    0xFFB5: "VendorFFB5",
                }
                lines.append(f"{g}({val:#x} {names.get(val, '')})")
            elif btag in (1, 2):
                lines.append(f"{g}({sval})")
            else:
                lines.append(f"{g}({val})")
        elif btype == 2:  # Local
            loc = {
                0: "Usage",
                1: "UsageMin",
                2: "UsageMax",
            }.get(btag, f"Local({btag})")
            usage_names = {
                (0x01, 0x02): "Mouse",
                (0x01, 0x06): "Keyboard",
                (0x01, 0x30): "X",
                (0x01, 0x31): "Y",
                (0x01, 0x38): "Wheel",
                (0x01, 0x80): "SystemControl",
                (0x0C, 0x01): "ConsumerControl",
                (0xFFB5, 0x01): "Vendor01",
            }
            un = usage_names.get((usage_page, val), "")
            lines.append(f"{loc}({val:#x} {un})".rstrip())
        else:
            lines.append(f"? type={btype} tag={btag} val={val}")
    return lines


def probe_vendor_all_paths() -> list:
    """Try every FFB5 open; also try feature/get_input."""
    out = []
    for d in hid.enumerate(0x32C2, 0x0066):
        if d.get("usage_page") != 0xFFB5:
            continue
        path = d["path"]
        path_s = path.decode("utf-8", "replace") if isinstance(path, bytes) else str(path)
        e = {"path": path_s[-80:]}
        try:
            h = hid.device()
            h.open_path(path)
            h.set_nonblocking(True)
            # try multiple cmds
            for cmd in (0x01, 0x04, 0x20, 0x23, 0x2B):
                # drain
                t0 = time.time()
                while time.time() - t0 < 0.03:
                    if not h.read(64):
                        time.sleep(0.001)
                try:
                    n = h.write(bytes([0xB5, cmd] + [0] * 6))
                except Exception as ex:
                    e[f"cmd_{cmd:02x}_write_err"] = str(ex)
                    continue
                time.sleep(0.08)
                pkts = []
                t0 = time.time()
                while time.time() - t0 < 0.2:
                    r = h.read(64)
                    if r:
                        pkts.append(list(r[:8]))
                    else:
                        time.sleep(0.002)
                e[f"cmd_{cmd:02x}"] = pkts
            # also try get_feature if any
            for rid in (0xB5, 0x01, 0x02):
                try:
                    feat = h.get_feature_report(rid, 9)
                    e[f"feature_{rid:02x}"] = list(feat) if feat else None
                except Exception as ex:
                    e[f"feature_{rid:02x}_err"] = str(ex)
            h.close()
        except Exception as ex:
            e["err"] = str(ex)
        out.append(e)
        print("vendor probe:", json.dumps(e)[:500])
    return out


def windows_battery() -> dict:
    ps = r"""
$ErrorActionPreference='SilentlyContinue'
$r = @{}
# Win32 batteries
$r.win32 = @(Get-CimInstance Win32_Battery | Select-Object Name,EstimatedChargeRemaining,BatteryStatus,DeviceID |
  ForEach-Object { @{name=$_.Name; pct=$_.EstimatedChargeRemaining; status=$_.BatteryStatus; id=$_.DeviceID} })
# Aggregate
try {
  Add-Type -AssemblyName System.Windows.Forms
  $p = [System.Windows.Forms.SystemInformation]::PowerStatus
  $r.system = @{battery_percent=[int]($p.BatteryLifePercent*100); line=$p.PowerLineStatus.ToString(); charge=$p.BatteryChargeStatus.ToString()}
} catch {}
# PnP battery devices related to bluetooth
$r.bt_bat = @(Get-PnpDevice | Where-Object {
  $_.InstanceId -match 'BTHLE|32C2|f900c6021401' -or $_.FriendlyName -match 'PopGo'
} | Select-Object Status,Class,FriendlyName,InstanceId | ForEach-Object {
  @{status=$_.Status; class=$_.Class; name=$_.FriendlyName; id=$_.InstanceId}
})
# Bluetooth LE battery via registry / MSFT
$r.hid_bt = @(Get-PnpDevice -Class HIDClass | Where-Object {
  $_.InstanceId -match 'BTHLEDEVICE|VID_32C2&PID_0026'
} | ForEach-Object { @{status=$_.Status; name=$_.FriendlyName; id=$_.InstanceId} })
$r | ConvertTo-Json -Depth 6
"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=25,
        )
        if r.stdout.strip():
            return json.loads(r.stdout)
        return {"stderr": r.stderr}
    except Exception as e:
        return {"error": str(e)}


def ble_battery_quick() -> dict | None:
    import asyncio
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ble_battery import read_ble_battery_sync

    r = read_ble_battery_sync()
    if not r:
        return None
    return {
        "percent": r.percent,
        "name": r.device_name,
        "addr": r.address_hex,
        "source": r.source,
    }


def decode_dump05() -> dict:
    p = Path(__file__).resolve().parent / "deep_probe_out" / "dump_05.bin"
    if not p.exists():
        return {}
    b = p.read_bytes()
    # Known markers from earlier
    findings = {
        "len": len(b),
        "nonzero_bytes": sum(1 for x in b if x),
        "markers": [],
    }
    # search for 0xB5 0x05
    for i in range(len(b) - 1):
        if b[i] == 0xB5 and b[i + 1] == 0x05:
            findings["markers"].append({"off": i, "ctx": b[max(0, i - 2) : i + 10].hex()})
    # LE 16-bit values that look like voltages or VIDs
    vids = []
    for i in range(0, min(len(b) - 1, 256)):
        v = b[i] | (b[i + 1] << 8)
        if v in (0x32C2, 0x0026, 0x0066, 0xFFB5, 0x0E02):
            vids.append({"off": i, "val": hex(v)})
    findings["interesting_u16"] = vids
    # extract plausible MAC-like sequences (we know F9:00:C6:02:14:01 and pieces from 2.4G IDs)
    # from PROTOCOL: 0x29 [2, 198, 50, 90, 204, 194] = 02 c6 32 5a cc c2
    sig = bytes([0x02, 0xC6, 0x32])
    findings["sig_02c632"] = [i for i in range(len(b) - 2) if b[i : i + 3] == sig]
    sig2 = bytes([0x5A, 0xCC, 0xC2])
    findings["sig_5accc2"] = [i for i in range(len(b) - 2) if b[i : i + 3] == sig2]
    findings["hex_head"] = b[:128].hex()
    return findings


def main():
    report = {}
    print("=== Report descriptors ===")
    report["report_descriptors"] = dump_report_descriptors()

    print("\n=== Vendor channel while BLE up ===")
    report["vendor_while_ble"] = probe_vendor_all_paths()

    print("\n=== Windows battery / PnP ===")
    report["windows_battery"] = windows_battery()
    print(json.dumps(report["windows_battery"], indent=2)[:2000])

    print("\n=== BLE battery quick ===")
    report["ble_battery"] = ble_battery_quick()
    print(report["ble_battery"])

    print("\n=== dump_05 re-decode ===")
    report["dump05"] = decode_dump05()
    print(json.dumps(report["dump05"], indent=2))

    out = OUT / "ble_deep3_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\nWrote", out)


if __name__ == "__main__":
    main()
