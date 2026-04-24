#!/usr/bin/env python3
"""
Parse ArduPilot DataFlash log text export (.log) from Mission Planner / other tools.
Extracts IMU[instance], XKF1[core], ARM, and optional merged series with armed-only filtering.

Binary .bin logs: use pymavlink's mavlogdump.py (see docstring in main).
"""

from __future__ import annotations

import argparse
import bisect
import csv
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

# Combat robot (fixed); used for merged CSV kinetic energy when --merge is used
ROBOT_MASS_KG = 11.95


def _parse_row(line: str) -> list[str] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    return line.split(",")


def iter_message_rows(path: Path) -> Iterator[tuple[str, list[str]]]:
    with path.open("r", errors="replace", encoding="utf-8", newline="") as f:
        for line in f:
            row = _parse_row(line)
            if not row:
                continue
            yield row[0], row


@dataclass(frozen=True)
class ArmInterval:
    start_us: int
    end_us: int | None  # None = still armed at end of log


def build_armed_intervals(arm_rows: Sequence[tuple[int, int]]) -> list[ArmInterval]:
    """
    arm_rows: list of (TimeUS, ArmState) in log order. ArmState 1 = armed, 0 = disarmed
    (ArduPilot common convention for ARM message).
    """
    intervals: list[ArmInterval] = []
    armed_start: int | None = None
    for t, state in arm_rows:
        if state == 1 and armed_start is None:
            armed_start = t
        elif state == 0 and armed_start is not None:
            intervals.append(ArmInterval(armed_start, t))
            armed_start = None
    if armed_start is not None:
        intervals.append(ArmInterval(armed_start, None))
    return intervals


def time_armed(t_us: int, intervals: Sequence[ArmInterval]) -> bool:
    for iv in intervals:
        if t_us < iv.start_us:
            return False
        if iv.end_us is None:
            return t_us >= iv.start_us
        if iv.start_us <= t_us < iv.end_us:
            return True
    return False


def main() -> int:
    p = argparse.ArgumentParser(
        description="Extract IMU, XKF1, ARM from ArduPilot DataFlash .log (text export)."
    )
    p.add_argument(
        "logfile",
        type=Path,
        help="Path to .log (text DataFlash export)",
    )
    p.add_argument(
        "--imu-instance",
        type=int,
        default=1,
        help="IMU instance I (default: 1 for secondary IMU)",
    )
    p.add_argument(
        "--xkf-core",
        type=int,
        default=1,
        help="XKF1 EKF core index C (default: 1)",
    )
    p.add_argument(
        "-o",
        "--outdir",
        type=Path,
        default=None,
        help="Output directory (default: same folder as log)",
    )
    p.add_argument(
        "--merge",
        action="store_true",
        help="Write merged_nearest.csv (IMU + XKF1 by nearest time, max gap 50ms)",
    )
    p.add_argument(
        "--mass-kg",
        type=float,
        default=ROBOT_MASS_KG,
        help=f"Kinetic energy columns in merged output (default: {ROBOT_MASS_KG} kg). Use 0 to omit KE fields.",
    )
    p.add_argument(
        "--armed-only",
        action="store_true",
        help="Filter merged output to times inside ARM ArmState=1 intervals",
    )
    args = p.parse_args()
    if not args.logfile.is_file():
        print(f"File not found: {args.logfile}", file=sys.stderr)
        return 1

    outdir = args.outdir or args.logfile.parent
    outdir.mkdir(parents=True, exist_ok=True)
    base = args.logfile.stem.replace(" ", "_")

    imu_out = outdir / f"{base}_IMU{args.imu_instance}.csv"
    xkf_out = outdir / f"{base}_XKF1_c{args.xkf_core}.csv"
    arm_out = outdir / f"{base}_ARM.csv"

    imu_rows: list[dict] = []
    xkf_rows: list[dict] = []
    arm_rows: list[tuple[int, int]] = []

    for name, row in iter_message_rows(args.logfile):
        if name == "IMU" and len(row) > 8:
            try:
                if int(float(row[2])) != args.imu_instance:
                    continue
            except (ValueError, IndexError):
                continue
            t = int(float(row[1]))
            imu_rows.append(
                {
                    "TimeUS": t,
                    "GyrX": float(row[3]),
                    "GyrY": float(row[4]),
                    "GyrZ": float(row[5]),
                    "AccX": float(row[6]),
                    "AccY": float(row[7]),
                    "AccZ": float(row[8]),
                }
            )
        elif name == "XKF1" and len(row) > 8:
            try:
                if int(float(row[2])) != args.xkf_core:
                    continue
            except (ValueError, IndexError):
                continue
            t = int(float(row[1]))
            xkf_rows.append(
                {
                    "TimeUS": t,
                    "Roll": float(row[3]),
                    "Pitch": float(row[4]),
                    "Yaw": float(row[5]),
                    "VN": float(row[6]),
                    "VE": float(row[7]),
                    "VD": float(row[8]),
                }
            )
        elif name == "ARM" and len(row) >= 3:
            try:
                t = int(float(row[1]))
                st = int(float(row[2]))
                arm_rows.append((t, st))
            except (ValueError, IndexError):
                continue

    def write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[dict]) -> None:
        rows = list(rows)
        with path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)

    write_csv(
        imu_out,
        ["TimeUS", "GyrX", "GyrY", "GyrZ", "AccX", "AccY", "AccZ"],
        imu_rows,
    )
    write_csv(
        xkf_out,
        ["TimeUS", "Roll", "Pitch", "Yaw", "VN", "VE", "VD"],
        xkf_rows,
    )
    with arm_out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["TimeUS", "ArmState"])
        w.writerows(arm_rows)

    print(f"Wrote {len(imu_rows)} IMU[{args.imu_instance}] rows -> {imu_out}")
    print(f"Wrote {len(xkf_rows)} XKF1[C={args.xkf_core}] rows -> {xkf_out}")
    print(f"Wrote {len(arm_rows)} ARM rows -> {arm_out}")

    intervals = build_armed_intervals(arm_rows)
    if intervals:
        print("Armed time ranges (TimeUS):")
        for iv in intervals:
            end = iv.end_us if iv.end_us is not None else "EOF"
            print(f"  {iv.start_us} .. {end}")

    if args.merge and imu_rows and xkf_rows:
        merged_path = outdir / f"{base}_merged_nearest.csv"
        xkf_by_t = sorted(xkf_rows, key=lambda r: r["TimeUS"])
        t_xkf = [r["TimeUS"] for r in xkf_by_t]
        imu_by_t = sorted(imu_rows, key=lambda r: r["TimeUS"])
        out_rows: list[dict] = []
        max_gap = 50_000  # 50 ms
        m: float | None = args.mass_kg
        if m is not None and m <= 0:
            m = None

        def xkf_at(t: int) -> dict | None:
            if not xkf_by_t:
                return None
            i = bisect.bisect_left(t_xkf, t)
            candidates: list[int] = []
            if i > 0:
                candidates.append(i - 1)
            if i < len(t_xkf):
                candidates.append(i)
            if not candidates:
                return None
            best = min(candidates, key=lambda k: abs(t_xkf[k] - t))
            if abs(t_xkf[best] - t) > max_gap:
                return None
            return xkf_by_t[best]

        for im in imu_by_t:
            t = im["TimeUS"]
            if args.armed_only and not time_armed(t, intervals):
                continue
            x = xkf_at(t)
            if not x:
                continue
            vn, ve, vd = x["VN"], x["VE"], x["VD"]
            v_h = math.hypot(vn, ve)
            v_tot = math.sqrt(vn * vn + ve * ve + vd * vd)
            rec = {
                "TimeUS": t,
                "AccX": im["AccX"],
                "AccY": im["AccY"],
                "AccZ": im["AccZ"],
                "GyrX": im["GyrX"],
                "GyrY": im["GyrY"],
                "GyrZ": im["GyrZ"],
                "VN": vn,
                "VE": ve,
                "VD": vd,
                "V_horiz_m_s": v_h,
                "V_3D_m_s": v_tot,
            }
            if m is not None:
                rec["KE_horiz_J"] = 0.5 * m * v_h * v_h
                rec["KE_3D_J"] = 0.5 * m * v_tot * v_tot
            out_rows.append(rec)

        fields = [
            "TimeUS",
            "AccX",
            "AccY",
            "AccZ",
            "GyrX",
            "GyrY",
            "GyrZ",
            "VN",
            "VE",
            "VD",
            "V_horiz_m_s",
            "V_3D_m_s",
        ]
        if m is not None:
            fields.extend(["KE_horiz_J", "KE_3D_J"])
        with merged_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(out_rows)
        print(f"Wrote {len(out_rows)} merged rows -> {merged_path}")

    print(
        "\nBinary .bin on Mac: pip install pymavlink && python -m pymavlink.tools.mavlogdump "
        "your.bin -o your_export.log"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
