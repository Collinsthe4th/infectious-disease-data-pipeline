"""The model comparison must give identical numbers when it is run twice on the same data."""

import dataclasses

import numpy as np
import pandas as pd
import pytest


def test_evaluate_models_is_deterministic():
    pytest.importorskip("statsmodels")
    pytest.importorskip("sklearn")
    from src.analytics.run import evaluate_models
    from src.common.config import get_settings

    settings = dataclasses.replace(get_settings(), evaluation_weeks=6, sarima_refit_every=100)
    idx = pd.date_range("2021-01-04", periods=150, freq="W-MON")
    rng = np.random.default_rng(1)
    season = 20 + 15 * np.cos(2 * np.pi * (idx.isocalendar().week.to_numpy() - 6) / 52)
    y = pd.Series(rng.poisson(season).astype(float), index=idx)
    _, first, _ = evaluate_models(y, settings)
    _, second, _ = evaluate_models(y, settings)
    cols = ["mae", "rmse", "mase"]
    pd.testing.assert_frame_equal(first[["model_name", "horizon", *cols]].reset_index(drop=True),
                                  second[["model_name", "horizon", *cols]].reset_index(drop=True))
