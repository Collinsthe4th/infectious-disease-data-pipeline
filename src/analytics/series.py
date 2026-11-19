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


# --------------------------------------------------------------------------- #
#  Which diseases can be forecast (shared by the pipeline and the dashboard)
# --------------------------------------------------------------------------- #

# Diseases whose forecast target is fixed by the study design. Others use whichever
# measure NCDC reports in volume (see :func:`forecast_measure`).
FIXED_FORECAST_MEASURE = {"LASSA": "confirmed"}
MIN_COVERAGE = 0.70  # share of weeks in the training window that must have a real report
MAX_STALENESS_WEEKS = 26  # no live forecast from data older than this


def forecast_measure(disease_code: str, frame: pd.DataFrame) -> str:
    """'confirmed' or 'suspected': what a disease's forecast predicts."""
    if disease_code in FIXED_FORECAST_MEASURE:
        return FIXED_FORECAST_MEASURE[disease_code]
    conf = pd.to_numeric(frame.get("confirmed"), errors="coerce") if "confirmed" in frame else pd.Series(dtype=float)
    susp = pd.to_numeric(frame.get("suspected"), errors="coerce") if "suspected" in frame else pd.Series(dtype=float)
    if conf.notna().sum() and (not susp.notna().sum() or conf.sum() >= 0.25 * susp.sum()):
        return "confirmed"
    return "suspected"


def forecast_eligibility(raw: pd.Series, horizons: int, today: pd.Timestamp | None = None) -> tuple[bool, str]:
    """Is there enough recent, regular weekly data to train and test the forecasting models?

    The models (seasonal naive, SARIMA with a 52-week season, and the machine-learning
    models with a 52-week lag) need two full years of weekly figures plus a test period.
    Returns (eligible, plain-language reason).
    """
    obs = raw.dropna()
    need = 2 * 52 + horizons + 8
    if obs.empty:
        return False, "no weekly figures"
    last = obs.index.max()
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    stale = (today - last).days // 7
    if stale > MAX_STALENESS_WEEKS:
        return False, f"no weekly report since {last:%d %b %Y}, so a forecast would not be about the coming weeks"
    window = raw[raw.index > last - pd.Timedelta(weeks=need)]
    coverage = window.notna().sum() / need
    if coverage < MIN_COVERAGE:
        return False, (f"only {int(window.notna().sum())} of the last {need} weeks have a weekly report "
                       f"({coverage:.0%}); the models need at least {MIN_COVERAGE:.0%} of about two years")
    return True, f"{int(window.notna().sum())} of the last {need} weeks reported ({coverage:.0%})"


def regular_stretch(raw: pd.Series, min_block_coverage: float = 0.5) -> pd.Series:
    """The most recent part of a series in which every 52-week block has at least half its
    weeks reported. Older, patchy years are left out of model training instead of being
    filled in with made-up values."""
    obs = raw.dropna()
    if obs.empty:
        return raw
    end = obs.index.max()
    start = end
    while True:
        block = raw[(raw.index > start - pd.Timedelta(weeks=52)) & (raw.index <= start)]
        if len(block) < 52 or block.notna().mean() < min_block_coverage:
            break
        start = start - pd.Timedelta(weeks=52)
    return raw[raw.index > start]
