"""Analytics job (Layer 6): rebuild series, STL, alerts, forecasts, evaluation.

Runs after each load and writes to the ``analytics`` schema:

* ``stl_component``  - latest decomposition for every series with >= 2 years
* ``alert``          - EARS C1-C3 for every series, residual z for national series
* ``forecast``       - rolling-origin backtest (``is_backtest``) and live 1-4 week forecasts
* ``model_evaluation`` - MAE, RMSE, MASE and Diebold-Mariano per model and horizon
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from src.analytics.anomaly import all_ears, residual_zscores, signals_frame
from src.analytics.decomposition import stl_decompose
from src.analytics.evaluation import mase_scale, summarise
from src.analytics.forecasting import Sarima, available_models, live_forecast, rolling_origin
from src.analytics.series import (
    forecast_eligibility,
    forecast_measure,
    model_ready,
    regular_stretch,
    to_weekly_series,
)
from src.common.config import Settings
from src.storage import repository as repo

log = logging.getLogger(__name__)


@dataclass
class AnalyticsSummary:
    series: int = 0
    stl_series: int = 0
    alerts_written: int = 0
    active_alerts: int = 0
    forecasts_written: int = 0
    evaluations_written: int = 0
    skipped: list[str] = field(default_factory=list)


def _week_id(ts: pd.Timestamp) -> int:
    iso = ts.isocalendar()
    return int(iso[0]) * 100 + int(iso[1])


def run_analytics(engine: Engine, run_id: uuid.UUID, settings: Settings, *, forecast: bool = True) -> AnalyticsSummary:
    summary = AnalyticsSummary()
    with engine.connect() as conn:
        keys = repo.series_keys(conn, min_points=8)
        frames = {(k["disease_code"], k["pcode"]): repo.weekly_cases_frame(conn, disease_code=k["disease_code"], pcode=k["pcode"])
                  for k in keys}
    ids = {(k["disease_code"], k["pcode"]): (k["disease_id"], k["location_id"], k["admin_level"]) for k in keys}
    summary.series = len(keys)

    thresholds = {"EARS_C1": settings.ears_threshold, "EARS_C2": settings.ears_threshold,
                  "EARS_C3": settings.ears_threshold, "SARIMA_RESID_Z": settings.residual_z_threshold}

    for key, frame in frames.items():
        disease_id, location_id, level = ids[key]
        measure = "confirmed" if frame["confirmed"].notna().sum() >= frame["suspected"].notna().sum() else "suspected"
        raw = to_weekly_series(frame, measure)
        if raw.notna().sum() < 8:
            continue
        filled, filled_mask = model_ready(raw)

        # --- STL -------------------------------------------------------------
        stl = stl_decompose(filled)
        if stl is not None:
            _write_stl(engine, run_id, disease_id, location_id, measure, stl, filled_mask)
            summary.stl_series += 1

        # --- alerts ----------------------------------------------------------
        results = all_ears(raw.fillna(filled))
        if level == 0 and len(filled) >= 2 * 52 + 10:
            try:
                pred = Sarima(refit_every=10_000).one_step_in_sample(filled)
                results["SARIMA_RESID_Z"] = residual_zscores(filled, pred)
            except Exception as exc:  # missing statsmodels or a failed fit
                summary.skipped.append(f"residual z for {key}: {exc}")
        signals = signals_frame(results, thresholds)
        signals = signals[raw.reindex(pd.DatetimeIndex(signals["week_start"])).notna().to_numpy()]  # observed weeks only
        written, active = _write_alerts(engine, run_id, disease_id, location_id, signals)
        summary.alerts_written += written
        summary.active_alerts += active

    if forecast:
        wanted = set(settings.forecast_diseases)
        for (disease_code, pcode), frame in frames.items():
            if pcode not in settings.forecast_locations or ("ALL" not in wanted and disease_code not in wanted):
                continue
            disease_id, location_id, _ = ids[(disease_code, pcode)]
            measure = forecast_measure(disease_code, frame)
            raw = to_weekly_series(frame, measure)
            ok, why = forecast_eligibility(raw, settings.forecast_horizons,
                                           max_staleness_weeks=settings.forecast_max_staleness_weeks)
            if not ok:
                summary.skipped.append(f"no forecast for {disease_code} {pcode}: {why}")
                continue
            filled, _ = model_ready(regular_stretch(raw))
            if len(filled) < 2 * 52 + settings.forecast_horizons + 8:
                summary.skipped.append(f"no forecast for {disease_code} {pcode}: regular stretch only {len(filled)} weeks")
                continue
            f_written, e_written = _forecast_series(engine, run_id, settings, disease_id, location_id, filled)
            log.info("forecast %s %s (%s): %s", disease_code, pcode, measure, why)
            summary.forecasts_written += f_written
            summary.evaluations_written += e_written

    log.info("analytics done", extra={"data": summary.__dict__})
    return summary


def _forecast_series(engine, run_id, settings, disease_id, location_id, y: pd.Series) -> tuple[int, int]:
    models = available_models(seed=settings.random_seed, sarima_refit_every=settings.sarima_refit_every)
    test_weeks = min(settings.evaluation_weeks, len(y) - 2 * 52 - settings.forecast_horizons)
    backtest = rolling_origin(y, models, horizons=settings.forecast_horizons, test_weeks=test_weeks)
    bt = backtest.forecasts
    bt["origin_week_id"] = bt["origin"].map(_week_id)
    bt["target_week_id"] = bt["target"].map(_week_id)
    first_origin = bt["origin"].min()
    scale = mase_scale(y[y.index <= first_origin])
    evaluation = summarise(bt, scale)

    live = live_forecast(y, models, settings.forecast_horizons, bt)
    live["origin_week_id"] = live["origin"].map(_week_id)
    live["target_week_id"] = live["target"].map(_week_id)

    rows = []
    for frame, is_backtest in ((bt, True), (live, False)):
        for r in frame.to_dict(orient="records"):
            rows.append({
                "run_id": run_id, "model_name": r["model_name"], "disease_id": disease_id, "location_id": location_id,
                "origin": r["origin_week_id"], "target": r["target_week_id"], "h": int(r["horizon"]),
                "pred": max(0.0, float(r["predicted"])), "actual": _num(r.get("actual")),
                "lo80": _num(r.get("lower_80")), "hi80": _num(r.get("upper_80")),
                "lo95": _num(r.get("lower_95")), "hi95": _num(r.get("upper_95")), "bt": is_backtest,
            })
    with engine.begin() as conn:
        _ensure_weeks(conn, [r["target"] for r in rows])
        if rows:
            conn.execute(text(
                """INSERT INTO analytics.forecast
                       (run_id, model_name, disease_id, location_id, origin_week_id, target_week_id, horizon,
                        predicted, actual, lower_80, upper_80, lower_95, upper_95, is_backtest)
                   VALUES (:run_id, :model_name, :disease_id, :location_id, :origin, :target, :h,
                           :pred, :actual, :lo80, :hi80, :lo95, :hi95, :bt)
                   ON CONFLICT ON CONSTRAINT uq_forecast DO NOTHING"""), rows)
        ev_rows = [{
            "run_id": run_id, "model_name": r["model_name"], "disease_id": disease_id, "location_id": location_id,
            "h": int(r["horizon"]), "start": int(r["test_start_week_id"]), "end": int(r["test_end_week_id"]),
            "n": int(r["n_origins"]), "mae": _num(r["mae"]), "rmse": _num(r["rmse"]), "mase": _num(r["mase"]),
            "ref": r["dm_reference_model"], "dm": _num(r["dm_statistic"]), "p": _num(r["dm_p_value"]),
        } for r in evaluation.to_dict(orient="records")]
        if ev_rows:
            conn.execute(text(
                """INSERT INTO analytics.model_evaluation
                       (run_id, model_name, disease_id, location_id, horizon, test_start_week_id, test_end_week_id,
                        n_origins, mae, rmse, mase, dm_reference_model, dm_statistic, dm_p_value)
                   VALUES (:run_id, :model_name, :disease_id, :location_id, :h, :start, :end,
                           :n, :mae, :rmse, :mase, :ref, :dm, :p)"""), ev_rows)
    return len(rows), len(ev_rows)


def _write_stl(engine, run_id, disease_id, location_id, measure, stl: pd.DataFrame, filled_mask: pd.Series) -> None:
    rows = [{
        "run_id": run_id, "d": disease_id, "l": location_id, "w": _week_id(ts), "m": measure,
        "obs": _num(r.observed), "trend": _num(r.trend), "seas": _num(r.seasonal), "rem": _num(r.remainder),
        "interp": bool(filled_mask.get(ts, False)),
    } for ts, r in stl.iterrows()]
    with engine.begin() as conn:
        conn.execute(text("""DELETE FROM analytics.stl_component
                              WHERE disease_id = :d AND location_id = :l AND series_measure = :m"""),
                     {"d": disease_id, "l": location_id, "m": measure})
        conn.execute(text(
            """INSERT INTO analytics.stl_component
                   (run_id, disease_id, location_id, week_id, series_measure, observed, trend, seasonal,
                    remainder, is_interpolated)
               VALUES (:run_id, :d, :l, :w, :m, :obs, :trend, :seas, :rem, :interp)"""), rows)


def _write_alerts(engine, run_id, disease_id, location_id, signals: pd.DataFrame) -> tuple[int, int]:
    if signals.empty:
        return 0, 0
    rows = [{
        "run_id": run_id, "method": r["method"], "d": disease_id, "l": location_id, "w": _week_id(r["week_start"]),
        "obs": max(0.0, float(r["observed"])), "exp": _num(r["expected"]), "stat": float(r["statistic"]),
        "thr": float(r["threshold"]),
    } for r in signals.to_dict(orient="records")]
    with engine.begin() as conn:
        conn.execute(text(
            """INSERT INTO analytics.alert
                   (run_id, method, disease_id, location_id, week_id, observed, expected, statistic, threshold)
               VALUES (:run_id, :method, :d, :l, :w, :obs, :exp, :stat, :thr)
               ON CONFLICT (method, disease_id, location_id, week_id) DO UPDATE
                  SET run_id = EXCLUDED.run_id, observed = EXCLUDED.observed, expected = EXCLUDED.expected,
                      statistic = EXCLUDED.statistic, threshold = EXCLUDED.threshold"""), rows)
    active = sum(1 for r in rows if r["stat"] > r["thr"])
    return len(rows), active


def _ensure_weeks(conn, week_ids: list[int]) -> None:
    """Forecast targets can fall beyond the seeded calendar; extend it if needed."""
    if not week_ids:
        return
    latest = max(week_ids)
    exists = conn.execute(text("SELECT 1 FROM curated.dim_epi_week WHERE week_id = :w"), {"w": latest}).first()
    if not exists:
        conn.execute(text("SELECT curated.populate_epi_weeks(CURRENT_DATE - 7, CURRENT_DATE + 400)"))


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(f) else f
