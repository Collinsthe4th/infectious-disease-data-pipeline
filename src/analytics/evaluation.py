"""Forecast accuracy (MAE, RMSE, MASE) and the Diebold-Mariano test (RQ4)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats


def mae(actual, predicted) -> float:
    a, p = np.asarray(actual, float), np.asarray(predicted, float)
    return float(np.mean(np.abs(a - p)))


def rmse(actual, predicted) -> float:
    a, p = np.asarray(actual, float), np.asarray(predicted, float)
    return float(np.sqrt(np.mean((a - p) ** 2)))


def mase_scale(train: pd.Series, season: int = 52) -> float:
    """In-sample MAE of the seasonal naive forecast (Hyndman & Koehler, 2006).

    Falls back to the one-step naive forecast when the training series is
    shorter than two seasons. Never returns zero.
    """
    y = np.asarray(train, float)
    m = season if len(y) > 2 * season else 1
    diffs = np.abs(y[m:] - y[:-m])
    scale = float(np.mean(diffs)) if diffs.size else float("nan")
    return scale if scale > 0 else 1.0


def mase(actual, predicted, scale: float) -> float:
    return mae(actual, predicted) / scale


def diebold_mariano(e1, e2, h: int = 1, power: int = 2) -> tuple[float, float]:
    """Diebold-Mariano test with the Harvey-Leybourne-Newbold correction.

    ``e1`` are the errors of the model under test, ``e2`` of the reference.
    Negative statistics mean the first model is more accurate. Returns
    ``(statistic, two-sided p-value)``; ``(nan, nan)`` if undefined.
    """
    e1, e2 = np.asarray(e1, float), np.asarray(e2, float)
    mask = np.isfinite(e1) & np.isfinite(e2)
    d = np.abs(e1[mask]) ** power - np.abs(e2[mask]) ** power
    n = d.size
    if n < 3 or np.allclose(d, 0):
        return float("nan"), float("nan")
    d_bar = d.mean()
    gamma = [np.sum((d[k:] - d_bar) * (d[: n - k] - d_bar)) / n for k in range(h)]
    var = (gamma[0] + 2 * sum(gamma[1:])) / n
    if var <= 0:
        return float("nan"), float("nan")
    dm = d_bar / math.sqrt(var)
    hln = math.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)
    stat = dm * hln
    p = 2 * stats.t.sf(abs(stat), df=n - 1)
    return float(stat), float(p)


def summarise(backtest: pd.DataFrame, scale: float, references: tuple[str, ...] = ("sarima", "seasonal_naive")) -> pd.DataFrame:
    """Accuracy per model and horizon, plus DM tests against each reference.

    ``backtest`` columns: model_name, horizon, origin_week_id, predicted, actual.
    """
    rows = []
    bt = backtest.dropna(subset=["actual", "predicted"])
    for (model, h), g in bt.groupby(["model_name", "horizon"]):
        base = {
            "model_name": model, "horizon": int(h), "n_origins": int(len(g)),
            "mae": mae(g["actual"], g["predicted"]), "rmse": rmse(g["actual"], g["predicted"]),
            "mase": mase(g["actual"], g["predicted"], scale),
            "test_start_week_id": int(g["target_week_id"].min()), "test_end_week_id": int(g["target_week_id"].max()),
        }
        compared = False
        for ref in references:
            if ref == model:
                continue
            r = bt[(bt["model_name"] == ref) & (bt["horizon"] == h)]
            if r.empty:
                continue
            joined = g.merge(r, on="origin_week_id", suffixes=("", "_ref"))
            stat, p = diebold_mariano(joined["actual"] - joined["predicted"],
                                      joined["actual_ref"] - joined["predicted_ref"], h=int(h))
            rows.append({**base, "dm_reference_model": ref, "dm_statistic": stat, "dm_p_value": p})
            compared = True
        if not compared:
            rows.append({**base, "dm_reference_model": None, "dm_statistic": None, "dm_p_value": None})
    return pd.DataFrame(rows)
