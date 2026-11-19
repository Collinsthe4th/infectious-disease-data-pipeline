"""Overview: outbreak scorecard for every priority disease, then the latest trend.

Run with:  streamlit run dashboard/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import data  # noqa: E402
import ui  # noqa: E402
from src.analytics.severity import HIGH, LOW, MEDIUM, NO_DATA, classify  # noqa: E402

ui.page("Infectious Disease Surveillance — Nigeria",
        subtitle="Built automatically from NCDC situation reports. The four most recent weeks are provisional "
                 "and may still change.")

weekly = data.national_weekly_all()
if weekly.empty:
    st.info("No data has been loaded yet.")
    st.stop()
alerts = data.national_alerts(12)

# ----------------------------------------------------------------------------- scorecard
STYLE = {HIGH: ("🔴", "#fdecea"), MEDIUM: ("🟠", "#fff4e5"), LOW: ("🟢", "#edf7ed"), NO_DATA: ("⚪", "#f2f2f2")}
ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2, NO_DATA: 3}

cards = []
for code, group in weekly.groupby("disease_code"):
    sev = classify(group, alerts[alerts["disease_code"] == code] if not alerts.empty else alerts)
    cards.append((group["disease_name"].iloc[0], sev))
cards.sort(key=lambda c: (ORDER[c[1].level], c[0]))

st.subheader("Outbreak scorecard")
counts = pd.Series([c[1].level for c in cards]).value_counts()
st.markdown(" · ".join(f"{STYLE[lvl][0]} **{counts.get(lvl, 0)} {lvl.lower()}**"
                       for lvl in (HIGH, MEDIUM, LOW, NO_DATA) if counts.get(lvl, 0)))

cols = st.columns(4)
for i, (name, sev) in enumerate(cards):
    icon, bg = STYLE[sev.level]
    cases = "–" if sev.latest_cases is None else f"{sev.latest_cases:,.0f}"
    week = "" if sev.latest_week is None else f"week of {sev.latest_week:%d %b %Y}"
    measure = {"confirmed": "confirmed cases", "suspected": "suspected cases"}.get(sev.measure, "")
    with cols[i % 4]:
        st.markdown(
            f"""<div style="background:{bg};border-radius:10px;padding:12px 14px;margin-bottom:12px;min-height:170px">
            <div style="font-size:0.95rem;font-weight:600">{name}</div>
            <div style="font-size:1.35rem;font-weight:700;margin:4px 0">{icon} {sev.level}</div>
            <div style="font-size:0.85rem">{cases} {measure} <span style="color:#666">{week}</span></div>
            <div style="font-size:0.8rem;color:#444;margin-top:6px">{sev.reason}</div></div>""",
            unsafe_allow_html=True)

with st.expander("How is the level decided?"):
    st.markdown(
        """
Each disease is checked on its **latest national weekly figures**:

| Level | Rule |
|---|---|
| 🔴 **High** | Two or more different alert checks fired in the last 2 reported weeks, **or** one fired while cases rose by at least 50% compared with the previous 4 weeks (and there were at least 10 cases) |
| 🟠 **Medium** | One alert check fired in the last 2 reported weeks, **or** cases are at least 1.5 times the usual level for this time of year (at least 5 cases) |
| 🟢 **Low** | None of the above |
| ⚪ **No recent weekly data** | The newest weekly figure is more than 6 weeks old (some diseases are now reported monthly) |

The four alert checks: *sudden jump* compared with the last 7 weeks, *jump* compared with weeks 3–9 before,
*steady build-up* over 3 weeks (the CDC EARS C1, C2 and C3 methods), and *higher than the seasonal forecast*
(SARIMA). This is a research tool to show where to look first — **not an official NCDC alert level**.
""")

# ----------------------------------------------------------------------------- trend
st.subheader("Weekly cases, last two years")
code, name = ui.disease_picker()
nat = weekly[weekly["disease_code"] == code]
col, note = ui.best_series(nat)
if col is None:
    st.info(note)
else:
    recent = nat[nat["week_start"] >= nat["week_start"].max() - pd.Timedelta(weeks=104)]
    shown = [c for c in (["suspected", "confirmed", "deaths"] if not col.startswith("cum_") else [col])
             if pd.to_numeric(recent[c], errors="coerce").notna().any()]
    long = recent.melt(id_vars=["week_start"], value_vars=shown, var_name="measure", value_name="cases").dropna()
    long["measure"] = long["measure"].map(ui.label)
    fig = px.line(long, x="week_start", y="cases", color="measure", markers=col.startswith("cum_"),
                  labels={"week_start": "Week", "cases": "Number of people", "measure": ""})
    prov = recent[recent["is_provisional"]]
    if not prov.empty:
        fig.add_vrect(x0=prov["week_start"].min(), x1=prov["week_start"].max(), fillcolor="grey", opacity=0.12,
                      annotation_text="may still change", line_width=0)
    fig.update_layout(legend={"orientation": "h"})
    st.plotly_chart(fig, use_container_width=True)
    if note:
        st.caption(note)

st.caption("Use the pages on the left for trends, states, forecasts, alerts and how the data pipeline is running.")
