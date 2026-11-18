"""Lassa fever forecasting, 1-4 weeks ahead (RQ4, Section 3.5.4).

Models
------
* ``seasonal_naive``     - same week last year (the benchmark)
* ``sarima``             - SARIMA(1,0,1)(1,0,0)[52] on log(1 + cases)
* ``negative_binomial``  - count GLM on the lag features (supporting)
* ``random_forest``, ``gradient_boosting``, ``xgboost`` - the ML group in H0
* ``svr``                - support vector regression (supporting)

All ML models use the *direct* strategy: one model per horizon h, trained on
features known at the forecast origin t to predict log(1 + cases) at t+h.
Features: lags 1-4 at the origin, the value 52 weeks before the target, a
4-week rolling mean, and sine/cosine of the target's week of year. Every
feature uses data up to the origin only, so nothing leaks from the future.

Optional dependencies (statsmodels, xgboost) are imported lazily; a model
whose library is missing is skipped with a log message, not an error.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.model_selection import GridSearchCV, TimeSeriesSplit
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR

from src.analytics.series import expm1

log = logging.getLogger(__name__)

SEASON = 52


@dataclass
class Prediction:
    horizon: int
    mean: float
    lower_80: Optional[float] = None
    upper_80: Optional[float] = None
    lower_95: Optional[float] = None
    upper_95: Optional[float] = None


# --------------------------------------------------------------------------- #
#  Features
# --------------------------------------------------------------------------- #

def _week_of_year(index: pd.DatetimeIndex) -> np.ndarray:
    return index.isocalendar().week.to_numpy(dtype=float)


def feature_frame(z: pd.Series, h: int) -> pd.DataFrame:
    """Features at each origin t for predicting z[t+h]; column ``target`` is z[t+h]."""
    df = pd.DataFrame(index=z.index)
    for lag in range(4):
        df[f"lag_{lag + 1}"] = z.shift(lag)
    df["seasonal_lag"] = z.shift(SEASON - h)  # value 52 weeks before the target
    df["roll_mean_4"] = z.rolling(4).mean()
    target_index = z.index + pd.Timedelta(weeks=h)
    woy = _week_of_year(target_index)
    df["sin_woy"] = np.sin(2 * math.pi * woy / 52.18)
    df["cos_woy"] = np.cos(2 * math.pi * woy / 52.18)
    df["target"] = z.shift(-h)
    return df


FEATURES = ["lag_1", "lag_2", "lag_3", "lag_4", "seasonal_lag", "roll_mean_4", "sin_woy", "cos_woy"]


# --------------------------------------------------------------------------- #
#  Model wrappers. ``forecast(y, H)`` gets counts up to the origin.
# --------------------------------------------------------------------------- #

class BaseForecaster:
    name = "base"

    def forecast(self, y: pd.Series, horizons: int) -> list[Prediction]:  # pragma: no cover
        raise NotImplementedError


class SeasonalNaive(BaseForecaster):
    name = "seasonal_naive"

    def forecast(self, y: pd.Series, horizons: int) -> list[Prediction]:
        out = []
        for h in range(1, horizons + 1):
            idx = len(y) - SEASON + h - 1
            value = float(y.iloc[idx]) if 0 <= idx < len(y) else float(y.iloc[-1])
            out.append(Prediction(h, max(value, 0.0)))
        return out


class DirectML(BaseForecaster):
    """Direct multi-horizon wrapper around a scikit-learn style regressor."""

    def __init__(self, name: str, make: Callable[[], object], grid: dict | None = None, seed: int = 42) -> None:
        self.name = name
        self._make = make
        self._grid = grid or {}
        self._best: dict = {}
        self._tuned = False
        self.seed = seed

    def tune(self, y: pd.Series) -> None:
        """Small grid search with time-ordered splits, on training data only."""
        if not self._grid or self._tuned:
            self._tuned = True
            return
        frame = feature_frame(np.log1p(y), 1).dropna()
        if len(frame) < 60:
            self._tuned = True
            return
        search = GridSearchCV(self._make(), self._grid, cv=TimeSeriesSplit(n_splits=3),
                              scoring="neg_mean_absolute_error", n_jobs=1)
        search.fit(frame[FEATURES], frame["target"])
        self._best = search.best_params_
        self._tuned = True
        log.info("tuned %s: %s", self.name, self._best)

    def _model(self):
        model = self._make()
        if self._best:
            model.set_params(**self._best)
        return model

    def forecast(self, y: pd.Series, horizons: int) -> list[Prediction]:
        z = np.log1p(y.clip(lower=0))
        out = []
        for h in range(1, horizons + 1):
            frame = feature_frame(z, h)
            train = frame.dropna()
            last = frame.iloc[[-1]][FEATURES]
            if len(train) < 30 or last.isna().any(axis=None):
                out.append(SeasonalNaive().forecast(y, horizons)[h - 1])
                continue
            model = self._model()
            model.fit(train[FEATURES], train["target"])
            pred = float(model.predict(last)[0])
            out.append(Prediction(h, float(expm1(pred))))
        return out


class NegativeBinomialGLM(BaseForecaster):
    """Count regression on the same features (supporting comparison)."""

    name = "negative_binomial"

    def forecast(self, y: pd.Series, horizons: int) -> list[Prediction]:
        import statsmodels.api as sm  # lazy

        z = np.log1p(y.clip(lower=0))
        out = []
        for h in range(1, horizons + 1):
            frame = feature_frame(z, h)
            frame["count_target"] = y.shift(-h)
            train = frame.dropna()
            last = frame.iloc[[-1]][FEATURES]
            if len(train) < 30 or last.isna().any(axis=None):
                out.append(SeasonalNaive().forecast(y, horizons)[h - 1])
                continue
            X = sm.add_constant(train[FEATURES], has_constant="add")
            try:
                model = sm.GLM(train["count_target"], X, family=sm.families.NegativeBinomial(alpha=1.0)).fit()
                pred = float(model.predict(sm.add_constant(last, has_constant="add"))[0])
            except Exception as exc:  # convergence problems on tiny or flat series
                log.warning("negative binomial failed at h=%d: %s", h, exc)
                pred = SeasonalNaive().forecast(y, horizons)[h - 1].mean
            out.append(Prediction(h, max(pred, 0.0)))
        return out


class Sarima(BaseForecaster):
    """SARIMA on log(1 + cases), re-estimated every ``refit_every`` origins.

    Between re-estimations the last parameters are applied to the longer
    series (Kalman filter only), which keeps the rolling evaluation fast.
    """

    name = "sarima"

    def __init__(self, order=(1, 0, 1), seasonal_order=(1, 0, 0, SEASON), refit_every: int = 13) -> None:
        self.order = order
        self.seasonal_order = seasonal_order
        self.refit_every = refit_every
        self._params = None
        self._since_fit = 0

    def _results(self, z: pd.Series):
        from statsmodels.tsa.statespace.sarimax import SARIMAX  # lazy

        model = SARIMAX(z.to_numpy(), order=self.order, seasonal_order=self.seasonal_order, trend="c",
                        enforce_stationarity=False, enforce_invertibility=False)
        if self._params is None or self._since_fit >= self.refit_every:
            res = model.fit(disp=False, maxiter=200)
            self._params = res.params
            self._since_fit = 0
        else:
            res = model.filter(self._params)
        self._since_fit += 1
        return res

    def forecast(self, y: pd.Series, horizons: int) -> list[Prediction]:
        z = np.log1p(y.clip(lower=0))
        res = self._results(z)
        fc = res.get_forecast(horizons)
        mean = np.asarray(fc.predicted_mean)
        ci80 = np.asarray(fc.conf_int(alpha=0.20))
        ci95 = np.asarray(fc.conf_int(alpha=0.05))
        return [
            Prediction(h + 1, float(expm1(mean[h])), float(expm1(ci80[h, 0])), float(expm1(ci80[h, 1])),
                       float(expm1(ci95[h, 0])), float(expm1(ci95[h, 1])))
            for h in range(horizons)
        ]

    def one_step_in_sample(self, y: pd.Series) -> pd.Series:
        """One-step-ahead predictions over the whole series (for residual z-scores)."""
        z = np.log1p(y.clip(lower=0))
        self._params = None
        res = self._results(z)
        pred = np.asarray(res.get_prediction(start=0, dynamic=False).predicted_mean)
        return pd.Series(expm1(pred), index=y.index)


def available_models(seed: int = 42, sarima_refit_every: int = 13) -> list[BaseForecaster]:
    """Every model whose library is installed, benchmark first."""
    models: list[BaseForecaster] = [SeasonalNaive()]
    try:
        import statsmodels  # noqa: F401
        models.append(Sarima(refit_every=sarima_refit_every))
        models.append(NegativeBinomialGLM())
    except ImportError:
        log.warning("statsmodels not installed: SARIMA and negative binomial skipped")
    models.append(DirectML("random_forest",
                           lambda: RandomForestRegressor(n_estimators=300, min_samples_leaf=2, random_state=seed, n_jobs=1),
                           {"max_depth": [None, 6], "min_samples_leaf": [1, 3]}))
    models.append(DirectML("gradient_boosting",
                           lambda: GradientBoostingRegressor(random_state=seed),
                           {"n_estimators": [100, 300], "max_depth": [2, 3], "learning_rate": [0.05]}))
    try:
        from xgboost import XGBRegressor  # noqa: F401

        models.append(DirectML("xgboost",
                               lambda: XGBRegressor(n_estimators=300, learning_rate=0.05, max_depth=3, subsample=0.9,
                                                    reg_lambda=1.0, random_state=seed, n_jobs=1, verbosity=0),
                               {"max_depth": [2, 4], "n_estimators": [200, 400]}))
    except ImportError:
        log.warning("xgboost not installed: XGBoost skipped")
    models.append(DirectML("svr", lambda: make_pipeline(StandardScaler(), SVR(C=1.0, epsilon=0.1)),
                           {"svr__C": [0.3, 1.0, 3.0]}))
    return models


# --------------------------------------------------------------------------- #
#  Rolling-origin evaluation
# --------------------------------------------------------------------------- #

@dataclass
class BacktestResult:
    forecasts: pd.DataFrame  # model_name, origin, target, horizon, predicted, actual
    errors: dict = field(default_factory=dict)


def rolling_origin(y: pd.Series, models: list[BaseForecaster], horizons: int = 4, test_weeks: int = 52) -> BacktestResult:
    """Expanding-window evaluation: every model sees exactly the same origins.

    Origins run from ``len(y) - test_weeks - horizons`` to ``len(y) - 2`` so
    each target falls in the last ``test_weeks + horizons`` observed weeks.
    """
    n = len(y)
    first_origin = max(SEASON * 2, n - test_weeks - horizons)
    if first_origin >= n - 1:
        raise ValueError(f"series too short for evaluation ({n} weeks)")
    for m in models:
        if isinstance(m, DirectML):
            m.tune(y.iloc[: first_origin + 1])
    rows = []
    errors: dict = {}
    for origin in range(first_origin, n - 1):
        train = y.iloc[: origin + 1]
        for model in models:
            try:
                preds = model.forecast(train, horizons)
            except Exception as exc:  # one failing model must not stop the others
                errors.setdefault(model.name, str(exc))
                continue
            for p in preds:
                target = origin + p.horizon
                if target >= n:
                    continue
                rows.append({"model_name": model.name, "origin": y.index[origin], "target": y.index[target],
                             "horizon": p.horizon, "predicted": p.mean, "actual": float(y.iloc[target])})
    for name, err in errors.items():
        log.warning("model %s failed during backtest: %s", name, err)
    return BacktestResult(pd.DataFrame(rows), errors)


def live_forecast(y: pd.Series, models: list[BaseForecaster], horizons: int, backtest: pd.DataFrame | None = None) -> pd.DataFrame:
    """Forecast the next ``horizons`` weeks from the last observed week.

    Models without their own intervals get empirical ones from the 10/90 and
    2.5/97.5 percentiles of their backtest errors at the same horizon.
    """
    rows = []
    for model in models:
        try:
            preds = model.forecast(y, horizons)
        except Exception as exc:
            log.warning("live forecast failed for %s: %s", model.name, exc)
            continue
        for p in preds:
            lo80, hi80, lo95, hi95 = p.lower_80, p.upper_80, p.lower_95, p.upper_95
            if lo80 is None and backtest is not None and not backtest.empty:
                err = backtest[(backtest.model_name == model.name) & (backtest.horizon == p.horizon)]
                if len(err) >= 10:
                    e = (err["actual"] - err["predicted"]).to_numpy()
                    q = np.quantile(e, [0.1, 0.9, 0.025, 0.975])
                    lo80, hi80, lo95, hi95 = (max(0.0, p.mean + q[0]), p.mean + q[1],
                                              max(0.0, p.mean + q[2]), p.mean + q[3])
            rows.append({"model_name": model.name, "origin": y.index[-1],
                         "target": y.index[-1] + pd.Timedelta(weeks=p.horizon), "horizon": p.horizon,
                         "predicted": p.mean, "lower_80": lo80, "upper_80": hi80, "lower_95": lo95, "upper_95": hi95})
    return pd.DataFrame(rows)
