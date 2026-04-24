#!/usr/bin/env python3
"""
Plot north velocity (VN) from merged log CSV, highlight high-speed windows and
large |dV/dt| (candidate impact times).
"""

from __future__ import annotations

# Same as parse_ardu_dataflash_log.ROBOT_MASS_KG
ROBOT_MASS_KG = 11.95

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

from plot_time_trim import manual_time_mask, trim_time_series


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


def _pick_dv_events(
    t_s: np.ndarray,
    abs_dvdt: np.ndarray,
    n: int,
    min_spacing_s: float,
) -> np.ndarray:
    """Indices of up to n largest |dV/dt| events with min spacing in time."""
    if n <= 0 or len(t_s) == 0:
        return np.array([], dtype=int)
    order = np.argsort(-abs_dvdt)
    picked: list[int] = []
    for i in order:
        if abs_dvdt[i] != abs_dvdt[i]:  # NaN
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


def _rolling_max_abs_np(
    t: np.ndarray, y_abs: np.ndarray, window_s: float
) -> np.ndarray:
    n = len(t)
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        lo = int(np.searchsorted(t, t[i] - window_s, side="left"))
        out[i] = float(np.max(y_abs[lo : i + 1]))
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description="Plot VN, highlight high |VN| and large |dVN/dt| (impacts)"
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
        help="Output PNG (default: <csv_stem>_vn_analysis.png next to csv)",
    )
    p.add_argument(
        "--velocity-percentile",
        type=float,
        default=99.0,
        help="Shade |VN| above this percentile (default: 99)",
    )
    p.add_argument(
        "--rolling-ms",
        type=float,
        default=300.0,
        help="Rolling max window on |VN| for sustained high-speed (ms, default: 300)",
    )
    p.add_argument(
        "--sustained-velocity-pct",
        type=float,
        default=90.0,
        help="Shade where rolling max |VN| exceeds this percentile (default: 90)",
    )
    p.add_argument(
        "--dv-percentile",
        type=float,
        default=99.5,
        help="Horizontal line for |dVN/dt| at this percentile (default: 99.5)",
    )
    p.add_argument(
        "--top-dv-events",
        type=int,
        default=12,
        help="Mark this many top |dV/dt| events (min spacing, default: 12)",
    )
    p.add_argument(
        "--min-peak-spacing-ms",
        type=float,
        default=100.0,
        help="Min spacing between marked dV/dt events (ms, default: 100)",
    )
    p.add_argument(
        "--no-auto-trim",
        action="store_true",
        help="Use full time range (default: auto-trim to main |dVN/dt| activity)",
    )
    p.add_argument(
        "--trim-frac",
        type=float,
        default=0.12,
        help="active if |dVN/dt| (pre-trim) >= trim-frac * max (default: 0.12)",
    )
    p.add_argument(
        "--trim-pad-s",
        type=float,
        default=0.6,
        help="Pad before/after auto window (s) (default: 0.6)",
    )
    p.add_argument("--t-min", type=float, default=None)
    p.add_argument("--t-max", type=float, default=None)
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
        r = csv.DictReader(f)
        if not r.fieldnames or "VN" not in r.fieldnames or "TimeUS" not in r.fieldnames:
            print("CSV must contain TimeUS and VN", file=sys.stderr)
            return 1
        rows = list(r)
    t0 = int(rows[0]["TimeUS"])
    t_s = (np.array([int(x["TimeUS"]) for x in rows], dtype=np.float64) - t0) * 1e-6
    vn = np.array([float(x["VN"]) for x in rows], dtype=np.float64)
    n = len(vn)
    if n < 3:
        print("Not enough rows", file=sys.stderr)
        return 1

    trim_note = "full time range"
    dvn_pre = np.abs(np.gradient(vn, t_s, edge_order=2))
    if args.t_min is not None or args.t_max is not None:
        lo = 0.0 if args.t_min is None else args.t_min
        hi = float(t_s[-1]) if args.t_max is None else args.t_max
        t_s, vn = manual_time_mask(t_s, lo, hi, vn)  # type: ignore[assignment]
        trim_note = f"manual [{lo:.2f}, {hi:.2f}] s from log t=0"
    elif not args.no_auto_trim:
        t_s, vn = trim_time_series(
            t_s, dvn_pre, vn, trim_frac=args.trim_frac, pad_s=args.trim_pad_s
        )
        trim_note = f"auto |dVN/dt|>={args.trim_frac:g}*max +{args.trim_pad_s:g}s pad"
    n = len(vn)
    if n < 3:
        print("Not enough rows after time trim", file=sys.stderr)
        return 1
    w_s = args.rolling_ms / 1000.0
    rolling_max = _rolling_max_abs_np(t_s, np.abs(vn), w_s)

    v_abs = np.abs(vn)
    v_hi = float(np.nanpercentile(v_abs, args.velocity_percentile))
    r_hi = float(np.nanpercentile(rolling_max[np.isfinite(rolling_max)], args.sustained_velocity_pct))

    # dVN/dt: gradient handles irregular dt
    dvdt = np.gradient(vn, t_s, edge_order=2)
    abs_dvdt = np.abs(dvdt)
    dv_median = float(np.nanmedian(abs_dvdt))
    dv_line = float(np.nanpercentile(abs_dvdt, args.dv_percentile))

    min_s = args.min_peak_spacing_ms / 1000.0
    dv_event_idx = _pick_dv_events(
        t_s, abs_dvdt, int(args.top_dv_events), min_s
    )

    out = args.output
    if out is None:
        out = path.parent / f"{path.stem}_vn_analysis.png"

    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, height_ratios=[1.0, 0.55])
    ax0, ax1 = axes

    # Sustained high |VN| (green)
    mask_r = np.isfinite(rolling_max) & (rolling_max >= r_hi)
    yl0, yh0 = (float(np.min(vn)), float(np.max(vn))) if n else (0, 1)
    pad = 0.1 * (yh0 - yl0 + 1e-9)
    ax0.set_ylim(yl0 - pad, yh0 + pad)
    t_plot = t_s
    _shade_runs(ax0, t_plot, mask_r, color="green", alpha=0.18)
    # Spiky high |VN| (gold) — instantaneous |VN| above percentile
    mask_v = v_abs >= v_hi
    _shade_runs(ax0, t_plot, mask_v, color="gold", alpha=0.2)

    (ln_vn,) = ax0.plot(t_plot, vn, color="C0", lw=0.8, label="VN (m/s)")

    # Mark top dV/dt
    for k in dv_event_idx:
        ax0.axvline(t_plot[k], color="red", alpha=0.5, ls="--", lw=0.9)
    ax0.scatter(
        t_plot[dv_event_idx],
        vn[dv_event_idx],
        color="red",
        s=16,
        zorder=5,
        label="Top |dVN/dt|",
    )
    ax0.set_ylabel("VN (m/s)")
    leg0 = [
        "Green: rolling max |VN| over {:.0f} ms ≥ p{:.0f} ({:.3f} m/s)".format(
            args.rolling_ms, args.sustained_velocity_pct, r_hi
        ),
        "Gold: |VN| ≥ p{:.0f} ({:.3f} m/s)".format(args.velocity_percentile, v_hi),
    ]
    ax0.set_title("North velocity (NED) + high-speed windows")
    ax0.text(
        0.01,
        0.02,
        "\n".join(leg0),
        transform=ax0.transAxes,
        fontsize=8,
        va="bottom",
        family="monospace",
        color="#333",
    )
    ax0.legend(
        [ln_vn, Line2D([0], [0], color="red", ls="--", lw=0.9)],
        ["VN (m/s)", "top |dV/dt| mark"],
        loc="upper right",
        fontsize=8,
    )

    ax1.fill_between(t_plot, 0, abs_dvdt, color="C1", alpha=0.3, linewidth=0)
    ax1.plot(t_plot, abs_dvdt, color="C1", lw=0.6, label="|dVN/dt| (m/s²)")
    ax1.axhline(dv_line, color="k", ls=":", lw=0.8, alpha=0.6)
    for k in dv_event_idx:
        ax1.axvline(t_plot[k], color="red", alpha=0.45, ls="--", lw=0.8)
    ax1.set_ylabel("|dVN/dt| (m/s²)")
    use_trim = (not args.no_auto_trim) or (
        args.t_min is not None or args.t_max is not None
    )
    ax1.set_xlabel(
        "Time (s) from start of window (|dVN/dt| trim, not ARM)"
        if use_trim
        else "Time (s) from first sample in log"
    )
    ax1.set_ylim(0, max(float(np.nanpercentile(abs_dvdt, 99.9)) * 1.1, dv_median * 3))
    ax1.set_title("Magnitude of north acceleration from EKF (gradient of VN)")
    ax1.legend(loc="upper right", fontsize=8)
    t_txt = f"p{args.dv_percentile} |dVN/dt| = {dv_line:.3f} m/s²; median = {dv_median:.3f}"
    ax1.text(0.99, 0.97, t_txt, transform=ax1.transAxes, ha="right", va="top", fontsize=7)

    fig.suptitle(
        f"{path.name}\n{trim_note}  |  m = {ROBOT_MASS_KG} kg",
        fontsize=9.0,
        y=1.02,
    )
    fig.tight_layout()
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Wrote {out}  ({trim_note}, window {t_plot[-1] - t_plot[0]:.2f} s)")
    print(leg0[0])
    print(leg0[1])
    print(
        f"Marked {len(dv_event_idx)} dV/dt events (top magnitude, min spacing {args.min_peak_spacing_ms} ms)"
    )
    if dv_event_idx.size:
        print("Event times (s):", ", ".join(f"{t_plot[i]:.4f}" for i in dv_event_idx))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
