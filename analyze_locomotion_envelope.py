#!/usr/bin/env python3
"""
Self-powered (locomotion) envelope from merged IMU+EKF CSV — NOT for impact / crash
survival analysis. Peaks in |a| and V here are dominated by drives, weight transfer,
vibration, and EKF state — not a single “hit” to the wall.

Use this to quantify the limits of the system under its own actuation. Compare to
estimate_impact_forces / impact test logs for external loading.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

ROBOT_MASS_KG = 11.95
G0 = 9.80665


def _resolve_merged(path: Path | None) -> Path:
    if path is not None:
        return path
    cwd = Path.cwd()
    m = list(cwd.glob("*_merged_nearest.csv"))
    if len(m) == 1:
        return m[0]
    raise FileNotFoundError("Pass merged CSV or ensure exactly one *_merged_nearest.csv in cwd.")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Max velocity and locomotion IMU accelerations (self-powered, not impact)"
    )
    p.add_argument("csv", type=Path, nargs="?", default=None)
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="PNG (default: <stem>_locomotion_envelope.png next to merged CSV)",
    )
    p.add_argument(
        "--mass-kg",
        type=float,
        default=ROBOT_MASS_KG,
        help=f"For KE annotation (default {ROBOT_MASS_KG})",
    )
    p.add_argument(
        "--file-line-start",
        type=int,
        default=None,
        metavar="N",
        help="1-based line in CSV file (line 1 = header; first data row = line 2). Use with --file-line-end.",
    )
    p.add_argument(
        "--file-line-end",
        type=int,
        default=None,
        metavar="N",
        help="1-based last line to include (inclusive). Same convention as editors (header is line 1).",
    )
    args = p.parse_args()

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = _resolve_merged(args.csv)
    if not path.is_file():
        print(f"Not found: {path}", file=sys.stderr)
        return 1

    with path.open(newline="") as f:
        r = csv.DictReader(f)
        fn = r.fieldnames or []
        for c in ("TimeUS", "AccX", "AccY", "AccZ", "VN", "VE"):
            if c not in fn:
                print(f"CSV must contain: TimeUS, AccX, AccY, AccZ, VN, VE", file=sys.stderr)
                return 1
        rows = list(r)
    n_data = len(rows)
    window_note = "full file"
    if args.file_line_start is not None or args.file_line_end is not None:
        if args.file_line_start is None or args.file_line_end is None:
            print(
                "Provide both --file-line-start and --file-line-end (1-based file lines).",
                file=sys.stderr,
            )
            return 1
        Ls, Le = args.file_line_start, args.file_line_end
        if Ls < 2 or Le < Ls:
            print(
                "Need file line start >= 2 (line 1 is header) and end >= start.",
                file=sys.stderr,
            )
            return 1
        i0 = Ls - 2
        i1_excl = Le - 1
        if i0 < 0 or i1_excl > n_data or i0 >= i1_excl:
            print(
                f"Line range {Ls}–{Le} → data [{i0}:{i1_excl}] invalid ({n_data} data rows).",
                file=sys.stderr,
            )
            return 1
        rows = rows[i0:i1_excl]
        window_note = f"file lines {Ls}–{Le} ({i1_excl - i0} data rows)"
    t0 = int(rows[0]["TimeUS"])
    t_s = (np.array([int(x["TimeUS"]) for x in rows], float) - t0) * 1e-6
    ax = np.array([float(x["AccX"]) for x in rows], float)
    ay = np.array([float(x["AccY"]) for x in rows], float)
    az = np.array([float(x["AccZ"]) for x in rows], float)
    vn = np.array([float(x["VN"]) for x in rows], float)
    ve = np.array([float(x["VE"]) for x in rows], float)
    vh = (
        np.array([float(x["V_horiz_m_s"]) for x in rows], float)
        if "V_horiz_m_s" in fn
        else np.hypot(vn, ve)
    )
    if "V_3D_m_s" in fn:
        v3 = np.array([float(x["V_3D_m_s"]) for x in rows], float)
    else:
        vd = np.array([float(x["VD"]) for x in rows], float)
        v3 = np.sqrt(vn * vn + ve * ve + vd * vd)
    a_xy = np.hypot(ax, ay)
    m = float(args.mass_kg)
    if len(t_s) < 3:
        print("Not enough rows in selection.", file=sys.stderr)
        return 1

    def pct(x, p: float) -> float:
        return float(np.nanpercentile(x, p))

    imax = int(np.nanargmax(vh))
    jmax = int(np.nanargmax(a_xy))
    kmaxx = int(np.nanargmax(np.abs(ax)))
    kmaxy = int(np.nanargmax(np.abs(ay)))

    out = args.output
    if out is None:
        out = path.parent / f"{path.stem}_locomotion_envelope.png"

    fig, axes = plt.subplots(4, 1, figsize=(12, 9.2), sharex=True)
    ax0, ax1, ax2, ax3n = axes

    ax0.fill_between(t_s, 0, vh, color="C0", alpha=0.15, linewidth=0)
    ax0.plot(t_s, vh, "C0", lw=0.9, label="V_horiz (m/s)")
    ax0.axhline(float(np.max(vh)), color="C0", ls=":", lw=0.9, alpha=0.7)
    ax0.scatter([t_s[imax]], [vh[imax]], color="C0", s=40, zorder=5, label=f"max V_h = {float(np.max(vh)):.3f} m/s")
    ax0.set_ylabel("m/s")
    ax0.set_title("EKF horizontal ground speed (NED) — self-powered / locomotion test")
    ax0.legend(loc="upper right", fontsize=8)
    vpk = float(np.max(vh))
    ke = 0.5 * m * vpk * vpk
    ax0.text(0.01, 0.98, f"KE @ max V_h ≈ {ke:.1f} J  (m = {m} kg)", transform=ax0.transAxes, va="top", fontsize=8)

    ax1.plot(t_s, vn, "C1", lw=0.7, label="VN (north)")
    ax1.plot(t_s, ve, "C2", lw=0.7, label="VE (east)")
    ax1.axhline(0, color="k", lw=0.3, alpha=0.4)
    ax1.set_ylabel("m/s")
    ax1.set_title("Horizontal velocity components (NED)")
    ax1.legend(loc="upper right", fontsize=7)

    ax2.plot(t_s, a_xy, color="C3", lw=0.8, label="|a_xy| = √(AccX²+AccY²) body (m/s²)")
    ax2.plot(t_s, ax, "C0", lw=0.45, alpha=0.5, label="AccX")
    ax2.plot(t_s, ay, "C1", lw=0.45, alpha=0.5, label="AccY")
    ax2.axhline(float(np.max(a_xy)), color="C3", ls=":", lw=0.8, alpha=0.6)
    ax2.scatter([t_s[jmax]], [a_xy[jmax]], color="C3", s=36, zorder=5, label=f"max |a_xy| = {float(np.max(a_xy)):.2f} m/s²")
    ax2.set_ylabel("m/s²")
    ax2.set_title("IMU in-plane specific force (body) — from locomotion, not a wall hit")
    ax2.legend(loc="upper right", fontsize=7, ncol=2)

    dev_z = np.abs(az - (-G0))
    ax3n.plot(t_s, az, color="C4", lw=0.7, label="AccZ (m/s²)")
    ax3n.axhline(-G0, color="k", ls="--", lw=0.6, alpha=0.5, label="−g ref")
    ax3n.set_ylabel("m/s²")
    ax3n.set_xlabel("Time (s) from first sample in log")
    ax3n.set_title("AccZ (body): includes gravity; deviation from 1g↓ shows vertical dynamics")
    kdz = int(np.nanargmax(dev_z))
    ax3n.scatter(
        [t_s[kdz]],
        [az[kdz]],
        color="red",
        s=32,
        zorder=5,
        label=f"max |a_z−(−g)|: {float(np.max(dev_z)):.2f} m/s²",
    )
    ax3n.legend(loc="upper right", fontsize=7)

    supt = (
        f"{path.name}  |  {window_note}  |  LOCOMOTION (self-powered, not impact)\n"
        f"Peak |V_h| = {float(np.max(vh)):.3f} m/s  |  Peak |V_3D| = {float(np.max(v3)):.3f} m/s  |  "
        f"Peak |a_xy| = {float(np.max(a_xy)):.2f} m/s²"
    )
    fig.suptitle(supt, fontsize=9, y=1.0)

    fig.tight_layout(rect=(0, 0.02, 1, 0.97))
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Wrote {out}\n")
    print("=== Locomotion envelope (self-powered, not impact) ===\n")
    print(f"Window: {window_note}")
    print(f"Duration: {t_s[-1] - t_s[0]:.2f} s  |  samples: {len(t_s)}\n")
    print("Velocity (EKF, NED):")
    print(f"  max V_horiz     = {float(np.max(vh)):.4f} m/s   at t = {t_s[imax]:.3f} s")
    print(f"  max |VN|        = {float(np.max(np.abs(vn))):.4f} m/s")
    print(f"  max |VE|        = {float(np.max(np.abs(ve))):.4f} m/s")
    print(f"  max V_3D        = {float(np.max(v3)):.4f} m/s")
    print(f"  p99  V_horiz    = {pct(vh, 99):.4f} m/s")
    print()
    print("Acceleration (IMU[1] body, m/s²) — dominated by drive / dynamics / tilt:")
    print(f"  max |a_xy|      = {float(np.max(a_xy)):.3f} m/s²  at t = {t_s[jmax]:.3f} s  (√(AccX²+AccY²))")
    print(f"  max |AccX|      = {float(np.max(np.abs(ax))):.3f} m/s²  at t = {t_s[kmaxx]:.3f} s")
    print(f"  max |AccY|      = {float(np.max(np.abs(ay))):.3f} m/s²  at t = {t_s[kmaxy]:.3f} s")
    print(f"  max |AccZ+{G0:.2f}|  ≈ {float(np.max(dev_z)):.3f} m/s²  (deviation from 1g down)")
    print(f"  p99  |a_xy|     = {pct(a_xy, 99):.3f} m/s²")
    print()
    print("Interpret: high |a_xy| here reflects motors, track/wheel, and attitude — not a single external impact load.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
