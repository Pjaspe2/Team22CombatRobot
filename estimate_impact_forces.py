#!/usr/bin/env python3
"""
From merged_nearest.csv: find strong deceleration events, estimate contact time on
each hit, and report peak / average impact force magnitudes.

Models (simple, stated in output):
  F_peak ≈ m * a_xy_peak   with a_xy = sqrt(AccX^2 + AccY^2) in the contact window
  F_avg  ≈ m * max(Δv_endpoints, Δv_int) / Δt
           Δv_int = ∫ d(V_h)/dt·dt  on the FWHM window (trapezoid; matches EKF change)

Contact time Δt: width of the IMU horizontal |a| pulse at FWHM (fraction of peak
in the local window), around the sample of max a_xy near each event seed.

Limitations: IMU is body frame (yaw/spin couples axes); EKF V_horiz lags; FWHM is
a proxy, not a measured load-cell trace. Use for relative comparison across runs.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROBOT_MASS_KG = 11.95


def _resolve_merged_csv(path: Path | None) -> Path:
    if path is not None:
        return path
    cwd = Path.cwd()
    matches = list(cwd.glob("*_merged_nearest.csv"))
    if not matches:
        raise FileNotFoundError(
            "No CSV path and no *_merged_nearest.csv in cwd."
        )
    if len(matches) > 1:
        raise FileNotFoundError(
            f"Multiple merged CSVs; specify path: {matches!r}"
        )
    return matches[0]


def _pick_most_negative_events(
    t_s: np.ndarray,
    signal: np.ndarray,
    n: int,
    min_spacing_s: float,
) -> np.ndarray:
    if n <= 0 or len(t_s) == 0:
        return np.array([], dtype=int)
    order = np.argsort(signal)
    picked: list[int] = []
    for i in order:
        v = signal[i]
        if v != v:
            continue
        if len(picked) >= n:
            break
        if all(abs(t_s[i] - t_s[j]) >= min_spacing_s for j in picked):
            picked.append(int(i))
    return np.array(sorted(picked), dtype=int)


def _contiguous_above(
    y: np.ndarray, i_center: int, thr: float
) -> tuple[int, int]:
    """Largest inclusive [lo, hi] containing i_center with y >= thr (clipped)."""
    n = len(y)
    lo, hi = i_center, i_center
    while lo > 0 and y[lo - 1] >= thr:
        lo -= 1
    while hi < n - 1 and y[hi + 1] >= thr:
        hi += 1
    return lo, hi


def _trapz_dvh(
    t: np.ndarray, dvh: np.ndarray, i_lo: int, i_hi: int
) -> float:
    """∫ dvh·dt on [i_lo, i_hi] (negative when slowing)."""
    sl = slice(i_lo, i_hi + 1)
    tseg = t[sl]
    dseg = dvh[sl]
    if len(tseg) < 2:
        return 0.0
    if hasattr(np, "trapezoid"):
        return float(np.trapezoid(dseg, tseg))
    return float(np.trapz(dseg, tseg))


@dataclass
class ImpactEvent:
    t_center_s: float
    i_seed: int
    i_peak: int
    i_lo: int
    i_hi: int
    dt_contact_s: float
    a_xy_peak_m_s2: float
    dv_h_m_s: float
    dv_h_from_integral_m_s: float
    f_peak_n: float
    f_avg_n: float
    v_before: float
    v_after: float


def analyze_events(
    t_s: np.ndarray,
    v_h: np.ndarray,
    ax: np.ndarray,
    ay: np.ndarray,
    dvh_dt: np.ndarray,
    *,
    m_kg: float,
    window_pre_s: float,
    window_post_s: float,
    fwhm_fraction: float,
    max_events: int,
    min_event_spacing_s: float,
) -> list[ImpactEvent]:
    a_xy = np.sqrt(ax * ax + ay * ay)
    seeds = _pick_most_negative_events(
        t_s, dvh_dt, max_events, min_event_spacing_s
    )
    out: list[ImpactEvent] = []
    n = len(t_s)
    for i_seed in seeds:
        t0 = t_s[i_seed]
        w_lo = int(np.searchsorted(t_s, t0 - window_pre_s, side="left"))
        w_hi = int(np.searchsorted(t_s, t0 + window_post_s, side="right"))
        w_hi = min(w_hi, n)
        w_lo = max(0, w_lo)
        if w_hi - w_lo < 5:
            continue
        sl = slice(w_lo, w_hi)
        a_w = a_xy[sl]
        rel = int(np.argmax(a_w))
        i_peak = w_lo + rel
        peak = float(a_xy[i_peak])
        if peak < 1e-6:
            continue
        thr = fwhm_fraction * peak
        i_lo, i_hi = _contiguous_above(a_xy, i_peak, thr)
        dt = float(t_s[i_hi] - t_s[i_lo])
        if dt <= 0:
            dt = float(np.median(np.diff(t_s[max(0, i_lo - 2) : i_hi + 3]))) or 0.001

        v_b = float(v_h[i_lo])
        v_a = float(v_h[i_hi])
        # Endpoint speed loss (EKF may lag; compare to integral)
        dv_ep = max(0.0, v_b - v_a)
        dv_int = _trapz_dvh(t_s, dvh_dt, i_lo, i_hi)
        # integrated dV_h: negative = net slowdown over window
        dv_from_int = max(0.0, -min(0.0, dv_int))

        f_peak = m_kg * peak
        # Use the larger of the two delta-V estimates (conservative for reporting loss)
        dv_use = max(dv_ep, dv_from_int)
        f_avg = m_kg * (dv_use / dt) if dt > 1e-9 else float("nan")

        out.append(
            ImpactEvent(
                t_center_s=float(t_s[i_peak]),
                i_seed=int(i_seed),
                i_peak=int(i_peak),
                i_lo=int(i_lo),
                i_hi=int(i_hi),
                dt_contact_s=dt,
                a_xy_peak_m_s2=peak,
                dv_h_m_s=dv_ep,
                dv_h_from_integral_m_s=dv_from_int,
                f_peak_n=f_peak,
                f_avg_n=f_avg,
                v_before=v_b,
                v_after=v_a,
            )
        )
    # Same physical hit if multiple d(V_h)/dt minima sit in one window
    by_key: dict[tuple[int, int, int], ImpactEvent] = {}
    for ev in out:
        k = (ev.i_peak, ev.i_lo, ev.i_hi)
        if k not in by_key or ev.f_peak_n > by_key[k].f_peak_n:
            by_key[k] = ev
    return sorted(by_key.values(), key=lambda e: e.t_center_s)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Peak decel events, contact time (FWHM), impact force estimates"
    )
    p.add_argument("csv", type=Path, nargs="?", default=None)
    p.add_argument("-o", "--output-csv", type=Path, default=None)
    p.add_argument("--mass-kg", type=float, default=ROBOT_MASS_KG)
    p.add_argument(
        "--window-pre-s",
        type=float,
        default=0.25,
        help="Seconds before d(V_h)/dt seed to search (default 0.25)",
    )
    p.add_argument(
        "--window-post-s",
        type=float,
        default=0.25,
        help="Seconds after seed (default 0.25)",
    )
    p.add_argument(
        "--fwhm-fraction",
        type=float,
        default=0.5,
        help="Fraction of peak sqrt(AccX^2+AccY^2) for pulse width (default 0.5 = FWHM)",
    )
    p.add_argument("--max-events", type=int, default=15)
    p.add_argument(
        "--min-event-spacing-s",
        type=float,
        default=0.08,
        help="Min time between seeds (dVh/dt minima) in s (default 0.08)",
    )
    args = p.parse_args()

    path = _resolve_merged_csv(args.csv)
    if not path.is_file():
        print(f"Not found: {path}", file=sys.stderr)
        return 1

    with path.open(newline="") as f:
        r = csv.DictReader(f)
        fn = r.fieldnames or []
        if not fn:
            print("Empty CSV", file=sys.stderr)
            return 1
        need = ("TimeUS", "AccX", "AccY")
        for c in need:
            if c not in fn:
                print(f"CSV must contain {need}", file=sys.stderr)
                return 1
        rows = list(r)
    time_us = np.array([int(x["TimeUS"]) for x in rows], dtype=np.int64)
    ax = np.array([float(x["AccX"]) for x in rows], dtype=np.float64)
    ay = np.array([float(x["AccY"]) for x in rows], dtype=np.float64)
    if "V_horiz_m_s" in fn:
        v_h = np.array([float(x["V_horiz_m_s"]) for x in rows], dtype=np.float64)
    elif "VN" in fn and "VE" in fn:
        v_h = np.hypot(
            np.array([float(x["VN"]) for x in rows], dtype=np.float64),
            np.array([float(x["VE"]) for x in rows], dtype=np.float64),
        )
    else:
        print("Need V_horiz_m_s or VN+VE", file=sys.stderr)
        return 1

    t0 = int(time_us[0])
    t_s = (time_us.astype(np.float64) - t0) * 1e-6
    dvh_dt = np.gradient(v_h, t_s, edge_order=2)

    m = float(args.mass_kg)
    events = analyze_events(
        t_s,
        v_h,
        ax,
        ay,
        dvh_dt,
        m_kg=m,
        window_pre_s=args.window_pre_s,
        window_post_s=args.window_post_s,
        fwhm_fraction=args.fwhm_fraction,
        max_events=args.max_events,
        min_event_spacing_s=args.min_event_spacing_s,
    )

    out_csv = args.output_csv
    if out_csv is None:
        out_csv = path.parent / f"{path.stem}_impact_forces.csv"

    fieldnames = [
        "t_center_s",
        "dt_contact_s",
        "a_xy_peak_m_s2",
        "dv_h_endpoints_m_s",
        "dv_h_integral_m_s",
        "F_peak_N",
        "F_avg_from_impulse_N",
        "v_before_m_s",
        "v_after_m_s",
        "i_seed",
    ]
    with out_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for ev in events:
            w.writerow(
                {
                    "t_center_s": f"{ev.t_center_s:.6f}",
                    "dt_contact_s": f"{ev.dt_contact_s:.6f}",
                    "a_xy_peak_m_s2": f"{ev.a_xy_peak_m_s2:.4f}",
                    "dv_h_endpoints_m_s": f"{ev.dv_h_m_s:.4f}",
                    "dv_h_integral_m_s": f"{ev.dv_h_from_integral_m_s:.4f}",
                    "F_peak_N": f"{ev.f_peak_n:.1f}",
                    "F_avg_from_impulse_N": f"{ev.f_avg_n:.1f}"
                    if ev.f_avg_n == ev.f_avg_n
                    else "",
                    "v_before_m_s": f"{ev.v_before:.4f}",
                    "v_after_m_s": f"{ev.v_after:.4f}",
                    "i_seed": ev.i_seed,
                }
            )

    print(f"Wrote {out_csv} ({len(events)} events)\n")
    print(
        f"m = {m} kg  |  contact Δt = FWHM of sqrt(AccX^2+AccY^2) in [{args.fwhm_fraction}× peak]\n"
        "F_peak = m·a_xy_peak  |  F_avg = m·max(dV_ep, dV_int)/dt  (dV_int = ∫d(V_h)/dt·dt on window)\n"
    )
    print(
        f"{'t_c(s)':>10} {'dt(ms)':>8} {'a_xypk':>8} "
        f"{'dV_ep':>7} {'dVint':>7} {'F_pk(N)':>9} {'F_av(N)':>9} {'v0':>6} {'v1':>6}"
    )
    for ev in events:
        fav = f"{ev.f_avg_n:.0f}" if ev.f_avg_n == ev.f_avg_n else "nan"
        print(
            f"{ev.t_center_s:10.3f} {1000*ev.dt_contact_s:8.1f} "
            f"{ev.a_xy_peak_m_s2:8.1f} {ev.dv_h_m_s:7.3f} {ev.dv_h_from_integral_m_s:7.3f} "
            f"{ev.f_peak_n:9.0f} {fav:>9} {ev.v_before:6.2f} {ev.v_after:6.2f}"
        )
    print(
        "\nNotes:\n"
        "- Peaks in d(V_horiz)/dt (EKF) seed each row; a_xy uses IMU in the same window.\n"
        "- If the robot yaws or the wall is not in the N-E plane, V_horiz and body Acc mis-align.\n"
        "- F_avg from ΔV/Δt can differ from m·(mean a) if the pulse is not rectangular.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
