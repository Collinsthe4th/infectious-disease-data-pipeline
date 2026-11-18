# 0008 Forecast evaluation protocol

Status: accepted

**Context.** RQ4 compares statistical baselines with machine-learning models for 1-4 week Lassa fever forecasts. A fair test needs identical forecast origins and no information from the future.

**Decision.**
- Rolling origin with an expanding window over the last 52 weeks (configurable `EVALUATION_WEEKS`); every model forecasts from the same origins.
- Direct strategy: one ML model per horizon; target `log(1 + cases)`, back-transformed before scoring.
- Features at origin t only: lags 1-4, the value 52 weeks before the target, a 4-week rolling mean, sine/cosine of the target's week of year.
- Hyperparameters tuned once by grid search with `TimeSeriesSplit` on the data before the first origin, then kept fixed.
- SARIMA(1,0,1)(1,0,0)[52] with a constant, re-estimated every 13 origins and Kalman-filtered in between.
- Metrics MAE, RMSE, MASE (scaled by the in-sample seasonal-naive MAE); Diebold-Mariano with the Harvey-Leybourne-Newbold correction against SARIMA and the seasonal naive model.
- Live-forecast intervals: model-based for SARIMA, empirical (backtest error quantiles) for the others.

**Why.** Standard practice (Hyndman & Athanasopoulos, 2021); re-estimating SARIMA at every origin would make the evaluation take hours with little change in parameters.

**Consequences.** The exact settings (`.env`) are recorded with the results. A unit test checks that changing future values never changes an earlier forecast.
