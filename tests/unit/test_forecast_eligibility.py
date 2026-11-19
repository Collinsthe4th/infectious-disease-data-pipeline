import numpy as np
import pandas as pd

from src.analytics.series import forecast_eligibility, forecast_measure, regular_stretch

IDX = pd.date_range("2021-01-04", "2026-10-26", freq="W-MON")
TODAY = pd.Timestamp("2026-11-19")


def test_regular_weekly_series_is_eligible():
    ok, why = forecast_eligibility(pd.Series(10.0, index=IDX), 4, TODAY)
    assert ok and "100%" in why


def test_stale_series_is_not_eligible():
    s = pd.Series(10.0, index=IDX)
    s[s.index > "2025-02-24"] = np.nan
    ok, why = forecast_eligibility(s, 4, TODAY)
    assert not ok and "since 24 Feb 2025" in why


def test_patchy_series_is_not_eligible():
    s = pd.Series(np.nan, index=IDX)
    s[s.index > "2026-03-01"] = 5.0  # only this year's outbreak run
    ok, why = forecast_eligibility(s, 4, TODAY)
    assert not ok and "of the last 116 weeks" in why


def test_regular_stretch_drops_patchy_early_years():
    s = pd.Series(10.0, index=IDX)
    s[s.index.year <= 2022] = np.nan
    s[(s.index.year == 2022) & (s.index.month == 6)] = 3.0  # a few stray weeks
    assert regular_stretch(s).dropna().index.min().year >= 2022
    assert regular_stretch(s).dropna().index.max() == IDX.max()


def test_forecast_measure():
    frame = pd.DataFrame({"confirmed": [1, 2], "suspected": [100, 120]})
    assert forecast_measure("LASSA", frame) == "confirmed"  # fixed by the study design
    assert forecast_measure("CHOLERA", frame) == "suspected"
    assert forecast_measure("MPOX", pd.DataFrame({"confirmed": [10, 12], "suspected": [20, 25]})) == "confirmed"
