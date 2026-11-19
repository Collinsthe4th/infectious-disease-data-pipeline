"""Cached read-only queries for the dashboard (curated + analytics + ops schemas)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.storage.db import get_engine  # noqa: E402

TTL = int(os.environ.get("DASHBOARD_CACHE_SECONDS", "300"))


@st.cache_resource
def engine():
    """Database connection. On Streamlit Cloud put DATABASE_URL in the app's Secrets."""
    url = os.environ.get("DATABASE_URL")
    try:
        url = st.secrets.get("DATABASE_URL", url)
    except Exception:  # no secrets file locally
        pass
    return get_engine(url)


def query(sql: str, **params) -> pd.DataFrame:
    with engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


@st.cache_data(ttl=TTL)
def diseases() -> pd.DataFrame:
    return query("SELECT disease_id, disease_code, disease_name, cfr_denominator FROM curated.dim_disease ORDER BY disease_name")


@st.cache_data(ttl=TTL)
def locations_with_data(disease_code: str) -> pd.DataFrame:
    return query(
        """SELECT DISTINCT pcode, location_name, admin_level FROM curated.v_weekly_cases
            WHERE disease_code = :d ORDER BY admin_level, location_name""", d=disease_code)


@st.cache_data(ttl=TTL)
def weekly(disease_code: str, pcode: str | None = None) -> pd.DataFrame:
    sql = "SELECT * FROM curated.v_weekly_cases WHERE disease_code = :d"
    params = {"d": disease_code}
    if pcode:
        sql += " AND pcode = :p"
        params["p"] = pcode
    df = query(sql + " ORDER BY week_start", **params)
    if not df.empty:
        df["week_start"] = pd.to_datetime(df["week_start"])
    return df


@st.cache_data(ttl=TTL)
def national_latest() -> pd.DataFrame:
    """Latest week per disease at national level, with the previous week for comparison."""
    return query(
        """WITH ranked AS (
               SELECT v.*, row_number() OVER (PARTITION BY disease_code ORDER BY week_id DESC) AS rn
                 FROM curated.v_weekly_cases v WHERE admin_level = 0)
           SELECT * FROM ranked WHERE rn <= 2 ORDER BY disease_name, week_id DESC""")


@st.cache_data(ttl=TTL)
def national_weekly_all() -> pd.DataFrame:
    """National weekly rows for every disease (for the scorecard)."""
    df = query("""SELECT disease_code, disease_name, week_start, epi_year, epi_week, suspected, confirmed, deaths,
                         cum_suspected, cum_confirmed, cum_deaths, is_provisional
                    FROM curated.v_weekly_cases WHERE admin_level = 0 ORDER BY disease_name, week_start""")
    if not df.empty:
        df["week_start"] = pd.to_datetime(df["week_start"])
    return df


@st.cache_data(ttl=TTL)
def national_alerts(weeks: int = 12) -> pd.DataFrame:
    df = query("""SELECT d.disease_code, w.week_start, a.method, a.is_alert, a.statistic, a.threshold
                    FROM analytics.alert a JOIN curated.dim_disease d USING (disease_id)
                    JOIN curated.dim_location l USING (location_id) JOIN curated.dim_epi_week w USING (week_id)
                   WHERE l.pcode = 'NG' AND w.week_start >= CURRENT_DATE - (:n * 7)""", n=weeks)
    if not df.empty:
        df["week_start"] = pd.to_datetime(df["week_start"])
    return df


@st.cache_data(ttl=TTL)
def has_synthetic() -> bool:
    return bool(query("SELECT EXISTS (SELECT 1 FROM curated.dim_source_report WHERE is_synthetic) AS s")["s"].iloc[0])


@st.cache_data(ttl=TTL)
def stl(disease_code: str, pcode: str) -> pd.DataFrame:
    df = query(
        """SELECT w.week_start, s.observed, s.trend, s.seasonal, s.remainder, s.is_interpolated, s.series_measure
             FROM analytics.stl_component s
             JOIN curated.dim_disease d USING (disease_id)
             JOIN curated.dim_location l USING (location_id)
             JOIN curated.dim_epi_week w USING (week_id)
            WHERE d.disease_code = :d AND l.pcode = :p ORDER BY w.week_start""", d=disease_code, p=pcode)
    if not df.empty:
        df["week_start"] = pd.to_datetime(df["week_start"])
    return df


@st.cache_data(ttl=TTL)
def alerts(since_weeks: int = 12) -> pd.DataFrame:
    df = query(
        """SELECT a.method, d.disease_name, d.disease_code, l.pcode,
                  COALESCE(l.lga_name, l.state_name, l.country_name) AS location_name, l.admin_level,
                  w.week_id, w.week_start, a.observed, a.expected, a.statistic, a.threshold, a.is_alert
             FROM analytics.alert a
             JOIN curated.dim_disease d USING (disease_id)
             JOIN curated.dim_location l USING (location_id)
             JOIN curated.dim_epi_week w USING (week_id)
            WHERE w.week_start >= (SELECT max(week_start) FROM curated.v_weekly_cases) - (:n * 7)
            ORDER BY w.week_start DESC, a.statistic DESC""", n=since_weeks)
    if not df.empty:
        df["week_start"] = pd.to_datetime(df["week_start"])
    return df


@st.cache_data(ttl=TTL)
def alert_series(disease_code: str, pcode: str) -> pd.DataFrame:
    df = query(
        """SELECT a.method, w.week_start, a.observed, a.expected, a.statistic, a.threshold, a.is_alert
             FROM analytics.alert a
             JOIN curated.dim_disease d USING (disease_id)
             JOIN curated.dim_location l USING (location_id)
             JOIN curated.dim_epi_week w USING (week_id)
            WHERE d.disease_code = :d AND l.pcode = :p ORDER BY w.week_start""", d=disease_code, p=pcode)
    if not df.empty:
        df["week_start"] = pd.to_datetime(df["week_start"])
    return df


@st.cache_data(ttl=TTL)
def latest_forecast_run() -> str | None:
    df = query("SELECT run_id FROM analytics.forecast ORDER BY created_at DESC LIMIT 1")
    return None if df.empty else str(df["run_id"].iloc[0])


@st.cache_data(ttl=TTL)
def forecasts(run_id: str, backtest: bool) -> pd.DataFrame:
    df = query(
        """SELECT f.model_name, d.disease_code, d.disease_name, l.pcode, COALESCE(l.state_name, l.country_name) AS location_name,
                  o.week_start AS origin_week, t.week_start AS target_week, f.horizon, f.predicted, f.actual,
                  f.lower_80, f.upper_80, f.lower_95, f.upper_95
             FROM analytics.forecast f
             JOIN curated.dim_disease d USING (disease_id)
             JOIN curated.dim_location l USING (location_id)
             JOIN curated.dim_epi_week o ON o.week_id = f.origin_week_id
             JOIN curated.dim_epi_week t ON t.week_id = f.target_week_id
            WHERE f.run_id = CAST(:r AS uuid) AND f.is_backtest = :b
            ORDER BY f.model_name, t.week_start""", r=run_id, b=backtest)
    for c in ("origin_week", "target_week"):
        if not df.empty:
            df[c] = pd.to_datetime(df[c])
    return df


@st.cache_data(ttl=TTL)
def evaluation(run_id: str) -> pd.DataFrame:
    return query(
        """SELECT e.model_name, d.disease_code, l.pcode, e.horizon, e.n_origins, e.mae, e.rmse, e.mase,
                  e.dm_reference_model, e.dm_statistic, e.dm_p_value
             FROM analytics.model_evaluation e JOIN curated.dim_location l USING (location_id)
             JOIN curated.dim_disease d USING (disease_id)
            WHERE e.run_id = CAST(:r AS uuid) ORDER BY d.disease_code, l.pcode, e.horizon, e.mase""", r=run_id)


def live_forecast(disease_code: str, pcode: str = "NG") -> tuple[pd.DataFrame | None, str | None]:
    """The next-weeks forecast from the most accurate model for one disease, and that model's name."""
    run_id = latest_forecast_run()
    if not run_id:
        return None, None
    live = forecasts(run_id, backtest=False)
    live = live[(live["disease_code"] == disease_code) & (live["pcode"] == pcode)] if not live.empty else live
    if live.empty:
        return None, None
    ev = evaluation(run_id)
    ev = ev[(ev["disease_code"] == disease_code) & (ev["pcode"] == pcode) & (ev["horizon"] == 1)] if not ev.empty else ev
    best = ev.sort_values("mase").iloc[0]["model_name"] if not ev.empty else live["model_name"].iloc[0]
    return live[live["model_name"] == best].sort_values("target_week"), best


@st.cache_data(ttl=TTL)
def runs(limit: int = 50) -> pd.DataFrame:
    return query(
        """SELECT run_id::text AS run_id, run_type, trigger_source, status, started_at, ended_at,
                  EXTRACT(EPOCH FROM (ended_at - started_at))::int AS duration_s,
                  files_discovered, files_new, files_skipped_duplicate, files_manual_queue,
                  rows_extracted, rows_passed, rows_warned, rows_quarantined, revisions_detected, error_message
             FROM ops.pipeline_run_log ORDER BY started_at DESC LIMIT :n""", n=limit)


@st.cache_data(ttl=TTL)
def quarantine(limit: int = 200) -> pd.DataFrame:
    return query(
        """SELECT q.quarantined_at, q.review_status, q.scope, r.rule_code, q.failure_reason,
                  d.file_name, q.source_page, q.payload::text AS payload
             FROM ops.quarantine q
             LEFT JOIN ops.validation_rule r USING (rule_id)
             JOIN raw.documents d USING (document_id)
            ORDER BY q.quarantined_at DESC LIMIT :n""", n=limit)


@st.cache_data(ttl=TTL)
def flag_counts() -> pd.DataFrame:
    return query("SELECT flag_type, count(*) AS n FROM ops.data_quality_flag GROUP BY 1 ORDER BY 2 DESC")


@st.cache_data(ttl=TTL)
def revisions(limit: int = 500) -> pd.DataFrame:
    return query(
        """SELECT h.detected_at, d.disease_name, COALESCE(l.state_name, l.country_name) AS location_name,
                  w.epi_year, w.epi_week, h.field_name, h.old_value, h.new_value, h.abs_change, h.rel_change,
                  h.revision_lag_weeks, o.report_code AS old_report, n.report_code AS new_report
             FROM curated.revision_history h
             JOIN curated.dim_disease d USING (disease_id)
             JOIN curated.dim_location l USING (location_id)
             JOIN curated.dim_epi_week w USING (week_id)
             JOIN curated.dim_source_report o ON o.report_id = h.old_report_id
             JOIN curated.dim_source_report n ON n.report_id = h.new_report_id
            ORDER BY h.detected_at DESC LIMIT :n""", n=limit)


@st.cache_data(ttl=TTL)
def documents_by_status() -> pd.DataFrame:
    return query("SELECT processing_status, count(*) AS n FROM raw.documents GROUP BY 1 ORDER BY 2 DESC")
