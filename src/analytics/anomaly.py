"""Outbreak signals: EARS C1, C2, C3 adapted to weekly data, and SARIMA residual z-scores.

EARS (Hutwagner et al., 2003) was designed for daily counts; here one step
is one week (Section 3.5.4):

* C1 compares week t with the mean and SD of weeks t-7 ... t-1.
* C2 uses weeks t-9 ... t-3 (a two-week guard band).
* C3 = sum over t-2 ... t of max(0, C2 - 1), the CDC formulation of
  "adding up the C2 values of the last three weeks".

The baseline SD is floored at ``min_sd`` (default 1 case) so that a single
case after a run of zeros does not produce an infinite statistic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _ears(series: pd.Series, lag_start: int, window: int, min_sd: float) -> pd.DataFrame:
    values = series.astype(float)
    baseline = values.shift(lag_start).rolling(window, min_periods=window)
    mean = baseline.mean()
    sd = baseline.std(ddof=1).clip(lower=min_sd)
    stat = (values - mean) / sd
    return pd.DataFrame({"observed": values, "expected": mean, "statistic": stat})


def ears_c1(series: pd.Series, min_sd: float = 1.0) -> pd.DataFrame:
    return _ears(series, lag_start=1, window=7, min_sd=min_sd)


def ears_c2(series: pd.Series, min_sd: float = 1.0) -> pd.DataFrame:
    return _ears(series, lag_start=3, window=7, min_sd=min_sd)


def ears_c3(series: pd.Series, min_sd: float = 1.0) -> pd.DataFrame:
    c2 = ears_c2(series, min_sd)
    excess = (c2["statistic"] - 1).clip(lower=0)
    stat = excess + excess.shift(1) + excess.shift(2)
    return pd.DataFrame({"observed": c2["observed"], "expected": c2["expected"], "statistic": stat})


def residual_zscores(observed: pd.Series, predicted: pd.Series, burn_in: int = 52) -> pd.DataFrame:
    """z = (observed - one-step SARIMA prediction) / SD of past residuals.

    The SD uses only residuals before week t (expanding window after a
    burn-in), so the statistic never uses information from the future.
    """
    resid = observed.astype(float) - predicted.astype(float)
    sd = resid.shift(1).expanding(min_periods=burn_in).std(ddof=1).clip(lower=1e-6)
    return pd.DataFrame({"observed": observed, "expected": predicted, "statistic": resid / sd})


def all_ears(series: pd.Series, min_sd: float = 1.0) -> dict[str, pd.DataFrame]:
    return {"EARS_C1": ears_c1(series, min_sd), "EARS_C2": ears_c2(series, min_sd), "EARS_C3": ears_c3(series, min_sd)}


def signals_frame(results: dict[str, pd.DataFrame], threshold: float | dict[str, float]) -> pd.DataFrame:
    """Long frame of method/week/statistic/threshold, without undefined weeks."""
    parts = []
    for method, frame in results.items():
        thr = threshold[method] if isinstance(threshold, dict) else threshold
        f = frame.dropna(subset=["statistic", "observed"]).copy()
        f = f[np.isfinite(f["statistic"])]
        f["method"] = method
        f["threshold"] = thr
        parts.append(f)
    if not parts:
        return pd.DataFrame(columns=["method", "observed", "expected", "statistic", "threshold"])
    out = pd.concat(parts)
    out.index.name = "week_start"
    return out.reset_index()
