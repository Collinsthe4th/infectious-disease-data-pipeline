import numpy as np
import pandas as pd
import pytest

from src.analytics.anomaly import ears_c1, ears_c2, ears_c3, residual_zscores, signals_frame
from src.analytics.evaluation import diebold_mariano, mae, mase, mase_scale, rmse, summarise
from src.analytics.forecasting import DirectML, SeasonalNaive, feature_frame, rolling_origin
from src.analytics.series import model_ready, to_weekly_series


def weekly(values, start="2021-01-04"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="W-MON"), dtype=float)


def test_ears_c1_known_value():
    s = weekly([2, 2, 2, 2, 2, 2, 4, 12])
    c1 = ears_c1(s)
    base = np.array([2, 2, 2, 2, 2, 2, 4], float)
    expected = (12 - base.mean()) / max(base.std(ddof=1), 1.0)
    assert c1["statistic"].iloc[-1] == pytest.approx(expected)


def test_ears_c2_uses_guard_band_and_c3_accumulates():
    s = weekly([1] * 12 + [10, 12])
    c2 = ears_c2(s)
    assert c2["expected"].iloc[-1] == pytest.approx(1.0)  # weeks t-9..t-3 are all 1
    c3 = ears_c3(s)
    assert c3["statistic"].iloc[-1] >= c2["statistic"].iloc[-1] - 1


def test_ears_floor_avoids_infinite_statistic():
    assert np.isfinite(ears_c1(weekly([0] * 7 + [1]))["statistic"].iloc[-1])


def test_residual_z_uses_only_past_residuals():
    obs = weekly(list(np.random.default_rng(0).poisson(10, 80)) + [40])
    z = residual_zscores(obs, weekly([10.0] * 81), burn_in=52)
    assert z["statistic"].iloc[-1] > 5
    assert z["statistic"].iloc[:52].isna().all()


def test_signals_frame_drops_undefined_weeks():
    out = signals_frame({"EARS_C1": ears_c1(weekly(range(12)))}, 3.0)
    assert len(out) == 5 and set(out.columns) >= {"week_start", "method", "statistic", "threshold"}


def test_metrics():
    assert mae([1, 2], [2, 4]) == 1.5
    assert rmse([0, 0], [3, 4]) == pytest.approx(np.sqrt(12.5))
    assert mase([10, 10], [12, 8], 2.0) == 1.0
    assert mase_scale(weekly([5] * 10)) == 1.0  # zero scale falls back to 1, never divides by zero


def test_diebold_mariano_direction():
    rng = np.random.default_rng(1)
    good, bad = rng.normal(0, 1, 60), rng.normal(0, 3, 60)
    stat, p = diebold_mariano(good, bad, h=1)
    assert stat < 0 and p < 0.05
    assert np.isnan(diebold_mariano(good, good)[0])


def test_series_has_explicit_gaps():
    frame = pd.DataFrame({"week_start": pd.to_datetime(["2024-01-01", "2024-01-15"]), "confirmed": [3, 5]})
    s = to_weekly_series(frame)
    assert len(s) == 3 and np.isnan(s.iloc[1])
    filled, mask = model_ready(s)
    assert filled.iloc[1] == 4 and mask.iloc[1]


def test_seasonal_naive_is_same_week_last_year():
    y = weekly(range(120))
    preds = SeasonalNaive().forecast(y, 4)
    assert [p.mean for p in preds] == [120 - 52 + h - 1 for h in range(1, 5)]


def test_features_never_look_ahead():
    z = weekly(np.arange(120, dtype=float))
    f = feature_frame(z, 2)
    row = f.iloc[60]
    assert row["lag_1"] == 60 and row["target"] == 62 and row["seasonal_lag"] == 62 - 52


def _seasonal_series(n=170, seed=3):
    rng = np.random.default_rng(seed)
    woy = np.arange(n) % 52
    lam = 5 + 30 * np.exp(-((woy - 6) ** 2) / 20)
    return weekly(rng.poisson(lam))


@pytest.mark.slow
def test_rolling_origin_no_leakage_and_same_origins():
    y = _seasonal_series()
    models = [SeasonalNaive(), DirectML("random_forest", lambda: __import__("sklearn.ensemble", fromlist=["x"])
                                        .RandomForestRegressor(n_estimators=30, random_state=0))]
    bt = rolling_origin(y, models, horizons=2, test_weeks=4).forecasts
    assert set(bt.groupby("model_name")["origin"].nunique()) == {bt["origin"].nunique()}
    assert (bt["target"] > bt["origin"]).all()
    # changing the future must not change a forecast made before it
    y2 = y.copy()
    y2.iloc[-1] = 9999
    bt2 = rolling_origin(y2, models, horizons=2, test_weeks=4).forecasts
    first = bt["origin"].min()
    a = bt[(bt.origin == first)].sort_values(["model_name", "horizon"])["predicted"].to_numpy()
    b = bt2[(bt2.origin == first)].sort_values(["model_name", "horizon"])["predicted"].to_numpy()
    assert np.allclose(a, b)


def test_summarise_includes_dm_against_references():
    rows = []
    for origin in range(10):
        for model, err in (("seasonal_naive", 3.0), ("random_forest", 1.0)):
            rows.append({"model_name": model, "horizon": 1, "origin_week_id": 202401 + origin,
                         "target_week_id": 202402 + origin, "actual": 10.0,
                         "predicted": 10.0 + err * (1 if origin % 2 else -1) + origin * 0.01})
    s = summarise(pd.DataFrame(rows), scale=2.0)
    rf = s[(s.model_name == "random_forest") & (s.dm_reference_model == "seasonal_naive")].iloc[0]
    assert rf["mae"] < 3 and rf["dm_statistic"] < 0
