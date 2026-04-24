#!/usr/bin/env python3
"""
IMU AccX/AccY + EKF horizontal speed from merged CSV.

Focus: *deceleration* — strong negative IMU acceleration on each body axis, and
negative d(V_horiz)/dt (EKF ground speed falling), which is usually clearer for
wall hits than raw |a| or jerk. Jerk (d²x/dt²) is easy to mis-read; see module
docstring below.

Robot mass is fixed for titles/KE readout: see ROBOT_MASS_KG.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

from plot_time_trim import manual_time_mask, trim_time_series

# Same as parse_ardu_dataflash_log.ROBOT_MASS_KG — keep in sync
ROBOT_MASS_KG = 11.95


def _resolve_merged_csv(path: Path | None) -> Path:
    if path is not None:
        return path
    cwd = Path.cwd()
    matches = list(cwd.glob("*_merged_nearest.csv"))
    if not matches:
        raise FileNotFoundError(
            "No merged CSV path given and no *_merged_nearest.csv in cwd."
        )
    if len(matches) > 1:
        raise FileNotFoundError(
            f"Multiple *_merged_nearest.csv; specify file explicitly: {matches!r}"
        )
    return matches[0]


def _pick_most_negative_events(
    t_s: np.ndarray,
    signal: np.ndarray,
    n: int,
    min_spacing_s: float,
) -> np.ndarray:
    """Indices of up to n most negative samples (e.g. dV/dt) with time spacing."""
    if n <= 0 or len(t_s) == 0:
        return np.array([], dtype=int)
    order = np.argsort(signal)  # ascending → most negative first
    picked: list[int] = []
    for i in order:
        v = signal[i]
        if v != v:  # NaN
            continue
        if len(picked) >= n:
            break
        if all(abs(t_s[i] - t_s[j]) >= min_spacing_s for j in picked):
            picked.append(int(i))
    return np.array(sorted(picked), dtype=int)


def _shade_runs(
    ax,
    t: np.ndarray,
    mask: np.ndarray,
    *,
    color: str,
    alpha: float,
) -> None:
    n = len(t)
    if n == 0:
        return
    i = 0
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < n and mask[j]:
            j += 1
        t_lo = float(t[i])
        t_hi = float(t[j - 1])
        if j < n:
            t_hi = 0.5 * (t[j - 1] + t[j])
        elif j >= 2:
            t_hi = float(t[j - 1]) + 0.5 * (t[j - 1] - t[j - 2])
        ax.axvspan(t_lo, t_hi, color=color, alpha=alpha, linewidth=0)
        i = j


def _rolling_min_np(
    t: np.ndarray, y: np.ndarray, window_s: float
) -> np.ndarray:
    """Min of y over each trailing window [t - window_s, t]."""
    n = len(t)
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        lo = int(np.searchsorted(t, t[i] - window_s, side="left"))
        out[i] = float(np.min(y[lo : i + 1]))
    return out


def _process_decel_axis(
    acc: np.ndarray,
    t_s: np.ndarray,
    win_s: float,
    *,
    spike_bottom_pct: float,
    sustained_bottom_pct: float,
) -> dict:
    """Shade *negative* IMU acceleration: spike + sustained roll-min."""
    rolling_min = _rolling_min_np(t_s, acc, win_s)
    finite_rm = rolling_min[np.isfinite(rolling_min)]
    r_lo = float(
        np.nanpercentile(
            finite_rm, sustained_bottom_pct
        )  # e.g. bottom 10% of roll-min
    )
    a_spike = float(np.nanpercentile(acc, spike_bottom_pct))  # e.g. 1% most negative
    mask_s = np.isfinite(rolling_min) & (rolling_min <= r_lo)
    mask_p = acc <= a_spike
    return {
        "rolling_min": rolling_min,
        "min_acc": float(np.min(acc)),
        "max_acc": float(np.max(acc)),
        "mask_sust": mask_s,
        "mask_spike": mask_p,
        "a_spike": a_spike,
        "r_lo": r_lo,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="AccX/AccY deceleration + EKF V_horiz and d(V_horiz)/dt"
    )
    p.add_argument(
        "csv",
        type=Path,
        nargs="?",
        default=None,
        help="Path to merged_nearest.csv (default: single *_merged_nearest.csv in cwd)",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output PNG (default: <csv_stem>_acc_xy_analysis.png)",
    )
    p.add_argument(
        "--rolling-ms",
        type=float,
        default=300.0,
        help="Window for rolling min Acc (deceleration) in ms (default: 300)",
    )
    p.add_argument(
        "--decel-spike-pct",
        type=float,
        default=1.0,
        help="Shade where Acc is below this percentile (most negative, default: 1)",
    )
    p.add_argument(
        "--decel-sustained-pct",
        type=float,
        default=10.0,
        help="Sustained: shade rolling min Acc below this %%ile of roll-min (default: 10)",
    )
    p.add_argument(
        "--dvh-shade-pct",
        type=float,
        default=5.0,
        help="Shade d(V_horiz)/dt when below this percentile (most neg., default: 5)",
    )
    p.add_argument(
        "--top-decel-events",
        type=int,
        default=10,
        help="Mark most negative d(V_horiz)/dt (default: 10)",
    )
    p.add_argument(
        "--min-peak-spacing-ms",
        type=float,
        default=100.0,
        help="Min spacing for marked dV_horiz/dt events (ms, default: 100)",
    )
    p.add_argument(
        "--no-auto-trim",
        action="store_true",
        help="Use full time range in the log (default: auto-trim to main activity window)",
    )
    p.add_argument(
        "--trim-frac",
        type=float,
        default=0.1,
        help="a_xy=sqrt(AccX^2+AccY^2): active if >= trim-frac*max; keep largest run by energy (default: 0.1)",
    )
    p.add_argument(
        "--trim-pad-s",
        type=float,
        default=0.65,
        help="Pad before/after auto window (s) (default: 0.65)",
    )
    p.add_argument(
        "--t-min",
        type=float,
        default=None,
        help="Override: time from first sample in log (s)",
    )
    p.add_argument(
        "--t-max",
        type=float,
        default=None,
        help="Override: time from first sample in log (s)",
    )
    args = p.parse_args()

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    path = _resolve_merged_csv(args.csv)
    if not path.is_file():
        print(f"Not found: {path}", file=sys.stderr)
        return 1

    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            print("Empty CSV", file=sys.stderr)
            return 1
        need = ("TimeUS", "AccX", "AccY")
        for c in need:
            if c not in reader.fieldnames:
                print(f"CSV must contain {need}", file=sys.stderr)
                return 1
        vh_ok = "V_horiz_m_s" in reader.fieldnames
        vn_ok = "VN" in reader.fieldnames and "VE" in reader.fieldnames
        if not vh_ok and not vn_ok:
            print("CSV must include V_horiz_m_s or both VN and VE for speed/decel", file=sys.stderr)
            return 1
        rows = list(reader)
    time_us = np.array([int(r["TimeUS"]) for r in rows], dtype=np.int64)
    ax_ = np.array([float(r["AccX"]) for r in rows], dtype=np.float64)
    ay_ = np.array([float(r["AccY"]) for r in rows], dtype=np.float64)
    if vh_ok:
        v_h = np.array([float(r["V_horiz_m_s"]) for r in rows], dtype=np.float64)
    else:
        v_h = np.hypot(
            np.array([float(r["VN"]) for r in rows], dtype=np.float64),
            np.array([float(r["VE"]) for r in rows], dtype=np.float64),
        )
    t0 = int(time_us[0])
    t_s = (time_us.astype(np.float64) - t0) * 1e-6
    n = len(ax_)
    if n < 3:
        print("Not enough rows", file=sys.stderr)
        return 1

    trim_note = "full time range"
    a_xy_pre = np.hypot(ax_, ay_)
    if args.t_min is not None or args.t_max is not None:
        lo = 0.0 if args.t_min is None else args.t_min
        hi = float(t_s[-1]) if args.t_max is None else args.t_max
        t_s, ax_, ay_, v_h = manual_time_mask(t_s, lo, hi, ax_, ay_, v_h)  # type: ignore[assignment]
        trim_note = f"manual [{lo:.2f}, {hi:.2f}] s from log t=0"
    elif not args.no_auto_trim:
        t_s, ax_, ay_, v_h = trim_time_series(
            t_s,
            a_xy_pre,
            ax_,
            ay_,
            v_h,
            trim_frac=args.trim_frac,
            pad_s=args.trim_pad_s,
        )
        trim_note = f"auto activity (a_xy>={args.trim_frac:g}*max) +{args.trim_pad_s:g}s pad"
    n = len(ax_)
    if n < 3:
        print("Not enough rows after time trim", file=sys.stderr)
        return 1

    win_s = args.rolling_ms / 1000.0
    px = _process_decel_axis(
        ax_, t_s, win_s, spike_bottom_pct=args.decel_spike_pct, sustained_bottom_pct=args.decel_sustained_pct
    )
    py = _process_decel_axis(
        ay_, t_s, win_s, spike_bottom_pct=args.decel_spike_pct, sustained_bottom_pct=args.decel_sustained_pct
    )

    dvh_dt = np.gradient(v_h, t_s, edge_order=2)
    min_s = args.min_peak_spacing_ms / 1000.0
    decel_idx = _pick_most_negative_events(
        t_s, dvh_dt, int(args.top_decel_events), min_s
    )
    dvh_dvh_lo = float(np.nanpercentile(dvh_dt, args.dvh_shade_pct))
    mask_dvh = dvh_dt <= dvh_dvh_lo
    m = ROBOT_MASS_KG
    ke = 0.5 * m * v_h * v_h

    out = args.output
    if out is None:
        out = path.parent / f"{path.stem}_acc_xy_analysis.png"

    fig, axes = plt.subplots(4, 1, figsize=(12, 10.5), sharex=True)
    ax0, ax1, ax2, ax3 = axes

    for ax, acc, pack, ylabel, c_line in (
        (ax0, ax_, px, "AccX (m/s²)", "C0"),
        (ax1, ay_, py, "AccY (m/s²)", "C2"),
    ):
        yl, yh = float(np.min(acc)), float(np.max(acc))
        pad = 0.08 * (yh - yl + 1e-9)
        ax.set_ylim(yl - pad, yh + pad)
        _shade_runs(ax, t_s, pack["mask_sust"], color="teal", alpha=0.2)
        _shade_runs(ax, t_s, pack["mask_spike"], color="mediumpurple", alpha=0.22)
        ax.plot(t_s, acc, color=c_line, lw=0.7)
        tag = ylabel[:4]
        leg_txt = [
            f"Teal: roll min {tag} {args.rolling_ms:.0f} ms ≤ p{args.decel_sustained_pct:.0f} of roll-min (≤{pack['r_lo']:.2f} m/s²)",
            f"Purple: {tag} ≤ p{args.decel_spike_pct:.1f} of samples ({pack['a_spike']:.2f} m/s²); min={pack['min_acc']:.2f} max={pack['max_acc']:.2f}",
        ]
        ax.set_ylabel(ylabel)
        ax.set_title(f"{tag} — IMU (negative = decel if +axis is forward / sign convention ok)")
        ax.text(
            0.01,
            0.02,
            "\n".join(leg_txt),
            transform=ax.transAxes,
            fontsize=6.5,
            va="bottom",
            family="monospace",
            color="#333",
        )
        if ax is ax0:
            ax.legend(
                [
                    Line2D([0], [0], color="teal", alpha=0.5, lw=4),
                    Line2D([0], [0], color="mediumpurple", alpha=0.5, lw=4),
                ],
                ["sustained decel (roll-min)", "spike decel (percentile)"],
                loc="upper right",
                fontsize=7,
            )

    ax2_twin = ax2.twinx()
    (ln_v,) = ax2.plot(t_s, v_h, color="C0", lw=0.8, label="V_horiz (m/s)")
    (ln_ke,) = ax2_twin.plot(t_s, ke, color="C3", lw=0.5, alpha=0.7, label=f"KE_h (J), m={m} kg")
    ax2.set_ylabel("V_horiz (m/s)")
    ax2_twin.set_ylabel("KE horizontal (J)")
    ax2.set_title("EKF horizontal speed and kinetic energy (NED plane)")
    ax2.legend(handles=[ln_v, ln_ke], loc="upper right", fontsize=7)

    _shade_runs(ax3, t_s, mask_dvh, color="coral", alpha=0.25)
    ax3.plot(t_s, dvh_dt, color="C1", lw=0.6, label="d(V_horiz)/dt")
    ax3.axhline(0, color="k", lw=0.6, alpha=0.4)
    for k in decel_idx:
        ax3.axvline(t_s[k], color="darkred", alpha=0.5, ls="--", lw=0.85)
    ax3.scatter(
        t_s[decel_idx], dvh_dt[decel_idx], color="darkred", s=18, zorder=5
    )
    ax3.set_ylabel("m/s²")
    lo, hi = float(np.min(dvh_dt)), float(np.max(dvh_dt))
    margin = 0.08 * (hi - lo + 1e-9)
    ax3.set_ylim(lo - margin, hi + max(margin, 0.2))
    ax3.set_title("Rate of change of EKF ground speed (negative = slowing / wall decel proxy)")
    ax3.text(
        0.99,
        0.03,
        f"min d(V_h)/dt = {float(np.min(dvh_dt)):.2f} m/s²; purple shading: d(V_h)/dt ≤ p{args.dvh_shade_pct:.0f}",
        transform=ax3.transAxes,
        ha="right",
        va="bottom",
        fontsize=7,
    )
    ax3.legend(
        [Line2D([0], [0], color="C1", lw=1), Line2D([0], [0], color="darkred", ls="--", lw=0.9)],
        ["d(V_horiz)/dt", "largest decel (neg.)"],
        loc="lower right",
        fontsize=7,
    )

    use_trim = (not args.no_auto_trim) or (
        args.t_min is not None or args.t_max is not None
    )
    ax3.set_xlabel(
        "Time (s) from start of window (activity-based trim, not ARM)"
        if use_trim
        else "Time (s) from first sample in log"
    )
    fig.suptitle(
        f"{path.name}\n{trim_note}  |  m = {m} kg — body IMU; EKF d(V_h)/dt lags",
        fontsize=8.1,
        y=1.02,
    )
    fig.tight_layout()
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Wrote {out}  ({trim_note}, window length {t_s[-1] - t_s[0]:.2f} s)")
    print(
        f"IMU: min(AccX)={px['min_acc']:.3f} max(AccX)={px['max_acc']:.3f} m/s²; "
        f"min(AccY)={py['min_acc']:.3f} max(AccY)={py['max_acc']:.3f} m/s²"
    )
    print(
        f"EKF: min d(V_horiz)/dt = {float(np.min(dvh_dt)):.3f} m/s²  "
        f"(most negative = strongest deceleration of ground speed in N-E plane)"
    )
    if decel_idx.size:
        print(
            "Strongest d(V_h)/dt event times (s):",
            ", ".join(f"{t_s[i]:.4f}" for i in decel_idx),
        )
    print(
        "\n--- Why d(V_h)/dt instead of jerk on IMU? ---\n"
        "Jerk = d(Acc)/dt: highlights *changes* in sensed specific force, so vibration\n"
        "and filter lag create spikes that are not a single 'impact' story. For a wall hit,\n"
        "d(V_horiz)/dt is the *scalar* rate of change of EKF ground speed in the N-E plane\n"
        "(negative = slowing). It lags a few 10s of ms and can miss sub-sample impulses but\n"
        "matches 'losing forward speed' more directly than differentiating IMU a second time.\n"
        "Complement: min Acc on the axis of travel (or IMU minus gravity in body) for peaks."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
