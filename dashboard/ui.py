"""Shared page furniture: plain-language labels, the synthetic-data banner, pickers."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import data

COLOURS = {"observed": "#1f4e79", "trend": "#c0392b", "forecast": "#e67e22", "alert": "#c0392b",
           "band": "rgba(230,126,34,0.18)"}

# Everything shown on screen uses these words instead of database column names.
LABELS = {
    "suspected": "Suspected cases", "confirmed": "Confirmed cases", "deaths": "Deaths",
    "cum_suspected": "Suspected cases so far this year", "cum_confirmed": "Confirmed cases so far this year",
    "cum_deaths": "Deaths so far this year", "cfr_pct": "Death rate (%)", "cfr_reported": "Death rate in report (%)",
    "incidence_per_100k": "Cases per 100,000 people", "is_provisional": "May still change",
    "quality_flags": "Data warnings", "report_code": "Source report", "week_start": "Week starting",
    "epi_year": "Year", "epi_week": "Week", "location_name": "Place", "disease_name": "Disease",
    "geo_zone": "Zone", "observed": "Cases that week", "expected": "Usual level", "statistic": "Alert score",
}
METHOD_LABELS = {
    "EARS_C1": "Sudden jump (vs last 7 weeks)",
    "EARS_C2": "Jump (vs weeks 3–9 before)",
    "EARS_C3": "Steady build-up (3 weeks)",
    "SARIMA_RESID_Z": "Above seasonal forecast",
}
MODEL_LABELS = {
    "seasonal_naive": "Same week last year (benchmark)", "sarima": "SARIMA (statistical)",
    "negative_binomial": "Negative binomial (statistical)", "random_forest": "Random Forest (machine learning)",
    "gradient_boosting": "Gradient Boosting (machine learning)", "xgboost": "XGBoost (machine learning)",
    "svr": "Support vector regression (machine learning)",
}
FLAG_LABELS = {
    "R05_CFR_MISMATCH": "Death rate differs from the report", "R06_CUM_DECREASE": "Year-to-date total went down",
    "R07_STATE_SUM": "States don't add up to the national total", "R11_TIMELINESS": "Report published late",
    "R12_COMPLETENESS": "Week missing from the series",
}
RULE_LABELS = {
    "R01_SCHEMA": "Table unreadable", "R02_NON_NEGATIVE": "Not a valid count", "R03_CONFIRMED_LE_SUSP":
    "More confirmed than suspected", "R04_DEATHS_LE_CASES": "More deaths than cases", "R08_EPI_WEEK": "Wrong or impossible week",
    "R09_LOCATION": "Unknown state name", "R10_DUPLICATE": "Same week listed twice",
}


def label(name: str) -> str:
    return LABELS.get(name, name.replace("_", " ").capitalize())


def rounded(df, decimals: int = 2):
    """Round numeric columns only (dates and text are left untouched)."""
    num = df.select_dtypes("number").columns
    return df.assign(**{c: df[c].round(decimals) for c in num})


def friendly(df: pd.DataFrame) -> pd.DataFrame:
    """Rename columns to plain words for display."""
    return df.rename(columns={c: label(c) for c in df.columns})


def page(title: str, icon: str = "🦠", subtitle: str | None = None) -> None:
    st.set_page_config(page_title=f"{title} · Nigeria Disease Surveillance", page_icon=icon, layout="wide")
    st.title(title)
    if subtitle:
        st.caption(subtitle)
    try:
        if data.has_synthetic():
            st.warning("This database contains **made-up demo data**, not real NCDC figures. Do not quote these numbers.",
                       icon="⚠️")
    except Exception as exc:  # database not reachable
        st.error(f"Cannot reach the database: {exc}")
        st.stop()


def disease_picker(key: str = "disease", default: str = "LASSA") -> tuple[str, str]:
    ds = data.diseases()
    names = ds["disease_name"].tolist()
    codes = ds["disease_code"].tolist()
    idx = codes.index(default) if default in codes else 0
    name = st.sidebar.selectbox("Disease", names, index=idx, key=key)
    return codes[names.index(name)], name


def location_picker(disease_code: str, key: str = "location") -> tuple[str | None, str | None]:
    locs = data.locations_with_data(disease_code)
    if locs.empty:
        return None, None
    labels = [("All of Nigeria" if lvl == 0 else name) for name, lvl in zip(locs["location_name"], locs["admin_level"])]
    choice = st.sidebar.selectbox("Place", labels, key=key)
    row = locs.iloc[labels.index(choice)]
    return row["pcode"], choice


def best_series(df: pd.DataFrame, prefer: str | None = None) -> tuple[str | None, str]:
    """Which column to chart: a weekly count if any exist, else a year-to-date total.

    Returns (column, note). Some NCDC reports (e.g. measles, yellow fever since
    2024) are monthly and only give year-to-date totals.
    """
    order = [prefer] if prefer else []
    order += ["confirmed", "suspected", "deaths"]
    for col in order:
        if col and col in df and pd.to_numeric(df[col], errors="coerce").notna().any():
            if col == "confirmed" and "suspected" in df and prefer is None:
                conf = pd.to_numeric(df["confirmed"], errors="coerce").sum()
                susp = pd.to_numeric(df["suspected"], errors="coerce").sum()
                if susp and conf < 0.25 * susp:
                    continue  # this disease is counted mainly as suspected cases
            return col, ""
    for col in ("cum_confirmed", "cum_suspected", "cum_deaths"):
        if col in df and pd.to_numeric(df[col], errors="coerce").notna().any():
            return col, ("NCDC publishes only running totals for this year (monthly reports), "
                         "so the chart shows the year-to-date total instead of weekly cases.")
    return None, "No figures have been published for this selection yet."
