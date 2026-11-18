"""STL decomposition (Cleveland et al., 1990) of weekly series (RQ3)."""

from __future__ import annotations

import pandas as pd

SEASON = 52


def stl_decompose(series: pd.Series, period: int = SEASON, robust: bool = True) -> pd.DataFrame | None:
    """Return observed/trend/seasonal/remainder, or ``None`` if too short or no statsmodels.

    Needs at least two full seasons. ``robust=True`` down-weights outbreak
    spikes so they land in the remainder instead of distorting the trend.
    """
    if len(series) < 2 * period + 1:
        return None
    try:
        from statsmodels.tsa.seasonal import STL
    except ImportError:
        return None
    result = STL(series.astype(float), period=period, robust=robust).fit()
    return pd.DataFrame({"observed": series, "trend": result.trend, "seasonal": result.seasonal,
                         "remainder": result.resid})
