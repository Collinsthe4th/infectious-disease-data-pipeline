"""Home page: national overview of the latest week for every priority disease.

Run with:  streamlit run dashboard/app.py
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

import data
import ui

ui.page("Infectious Disease Surveillance - Nigeria")
st.caption("Automated pipeline over NCDC weekly reports · seven priority diseases · "
           "the four most recent weeks are provisional and may still be revised.")

latest = data.national_latest()
if latest.empty:
    st.info("No data yet. Load the demo with `docker compose run --rm app demo` or run the pipeline.")
    st.stop()

st.subheader("Latest week, national")
cols = st.columns(4)
for i, (disease, group) in enumerate(latest.groupby("disease_name", sort=True)):
    group = group.sort_values("week_id", ascending=False)
    now = group.iloc[0]
    prev = group.iloc[1] if len(group) > 1 else None
    measure = "confirmed" if pd.notna(now["confirmed"]) and now["confirmed"] > 0 else "suspected"
    delta = None if prev is None or pd.isna(prev[measure]) else int(now[measure] - prev[measure])
    with cols[i % 4]:
        st.metric(f"{disease} ({measure})", f"{int(now[measure]) if pd.notna(now[measure]) else '–'}",
                  delta=delta, delta_color="inverse",
                  help=f"Epi week {now['epi_year']}-W{now['epi_week']:02d}"
                       f"{' · provisional' if now['is_provisional'] else ''}")

st.subheader("Weekly trend, last two years")
code, name = ui.disease_picker()
national = data.weekly(code, "NG")
if not national.empty:
    recent = national[national["week_start"] >= national["week_start"].max() - pd.Timedelta(weeks=104)]
    long = recent.melt(id_vars=["week_start", "is_provisional"], value_vars=["suspected", "confirmed", "deaths"],
                       var_name="measure", value_name="cases")
    fig = px.line(long, x="week_start", y="cases", color="measure", markers=False,
                  labels={"week_start": "Epidemiological week", "cases": "Cases per week"})
    prov = recent[recent["is_provisional"]]
    if not prov.empty:
        fig.add_vrect(x0=prov["week_start"].min(), x1=prov["week_start"].max(), fillcolor="grey", opacity=0.12,
                      annotation_text="provisional", line_width=0)
    st.plotly_chart(fig, use_container_width=True)

alerts = data.alerts(4)
active = alerts[alerts["is_alert"]] if not alerts.empty else alerts
st.subheader("Outbreak alerts in the last four weeks")
if active.empty:
    st.success("No alert thresholds crossed in the last four weeks.")
else:
    st.dataframe(active[["week_start", "disease_name", "location_name", "method", "observed", "expected", "statistic"]]
                 .rename(columns={"week_start": "week"}).round(2), use_container_width=True, hide_index=True)
st.caption("Use the pages in the sidebar for trends, states, forecasts, alerts and pipeline health.")
