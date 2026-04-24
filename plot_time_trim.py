"""
Crop time series to the largest "active" region for clearer plots (no ARM needed).

 activity(t) is compared to trim_frac * max(activity); the longest contiguous
region where activity >= that threshold wins (by sum of a² if ties). Padded
and clamped to [t[0], t[-1]].
"""

from __future__ import annotations

import numpy as np


def _largest_contiguous_by_energy(
    mask: np.ndarray, activity: np.ndarray
) -> slice | None:
    """Contiguous run of True with highest sum of activity**2 (the main burst)."""
    n = len(mask)
    if n == 0:
        return None
    a = np.asarray(activity, dtype=np.float64)
    best: tuple[int, int, float] = (0, 0, -1.0)  # lo, excl_hi, energy
    i = 0
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < n and mask[j]:
            j += 1
        e = float(np.nansum(a[i:j] * a[i:j]))
        if e > best[2]:
            best = (i, j, e)
        i = j
    if best[1] <= best[0]:
        return None
    return slice(best[0], best[1])


def trim_time_series(
    t_s: np.ndarray,
    activity: np.ndarray,
    *other: np.ndarray,
    trim_frac: float = 0.1,
    pad_s: float = 0.6,
    min_width_s: float = 0.4,
) -> tuple[np.ndarray, ...]:
    """
    Return (t_s', *others') where t_s' is re-zeroed to start at 0 in the window.
    If no activity, returns inputs unchanged.
    """
    t_s = np.asarray(t_s, dtype=np.float64)
    act = np.abs(np.asarray(activity, dtype=np.float64))
    mx = float(np.nanmax(act))
    if not np.isfinite(mx) or mx <= 0:
        return (t_s, *other)
    thr = max(trim_frac * mx, 0.0)
    mask = act >= thr
    sl = _largest_contiguous_by_energy(mask, act)
    if sl is None or sl.stop - sl.start < 2:
        return (t_s, *other)
    i0, i1 = sl.start, sl.stop - 1
    t_lo = float(t_s[i0] - pad_s)
    t_hi = float(t_s[i1] + pad_s)
    t_lo = max(t_lo, float(t_s[0]))
    t_hi = min(t_hi, float(t_s[-1]))
    if t_hi - t_lo < min_width_s:
        mid = 0.5 * (t_lo + t_hi)
        t_lo = max(float(t_s[0]), mid - 0.5 * min_width_s)
        t_hi = min(float(t_s[-1]), mid + 0.5 * min_width_s)
    keep = (t_s >= t_lo) & (t_s <= t_hi)
    if np.count_nonzero(keep) < 3:
        return (t_s, *other)
    t_out = t_s[keep] - t_s[keep][0]
    rest = tuple(np.asarray(x)[keep] for x in other)
    return (t_out, *rest)


def manual_time_mask(
    t_s: np.ndarray,
    t_min: float,
    t_max: float,
    *other: np.ndarray,
) -> tuple[np.ndarray, ...]:
    keep = (t_s >= t_min) & (t_s <= t_max)
    if np.count_nonzero(keep) < 2:
        return (t_s, *other)
    t_out = t_s[keep] - t_s[keep][0]
    rest = tuple(np.asarray(x)[keep] for x in other)
    return (t_out, *rest)
