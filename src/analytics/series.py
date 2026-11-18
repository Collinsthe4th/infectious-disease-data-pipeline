"""Weekly series preparation for the analytics layer."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.extraction.transform import interpolate_short_gaps


def to_weekly_series(frame: pd.DataFrame, measure: str = "confirmed") -> pd.Series:
    """Series indexed by ``week_start`` (Mondays) on a complete calendar.

    Missing weeks are NaN, never zero, so gaps stay visible (Section 3.5.3).
    """
    if frame.empty:
        return pd.Series(dtype=float)
    s = pd.Series(pd.to_numeric(frame[measure], errors="coerce").to_numpy(dtype=float),
                  index=pd.DatetimeIndex(pd.to_datetime(frame["week_start"])), name=measure)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    full = pd.date_range(s.index.min(), s.index.max(), freq="W-MON")
    return s.reindex(full)


def model_ready(series: pd.Series, max_gap: int = 2) -> tuple[pd.Series, pd.Series]:
    """Fill short gaps for modelling and report which weeks were filled.

    Gaps of one or two weeks are interpolated; longer gaps (rare) are filled
    with the previous value so models can run. Filled weeks are returned as a
    mask and are never shown as observed data.
    """
    filled, mask = interpolate_short_gaps(series, max_gap=max_gap)
    long_gap = filled.isna()
    filled = filled.ffill().bfill().fillna(0.0)
    return filled.clip(lower=0), (mask | long_gap)


def log1p(series: pd.Series) -> pd.Series:
    return np.log1p(series.clip(lower=0))


def expm1(values):
    return np.clip(np.expm1(values), 0, None)
