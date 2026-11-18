"""Historical weekly series with STL trend and seasonal components (RQ3)."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import data
import ui

ui.page("Trends and seasonality", "📈")
code, name = ui.disease_picker()
pcode, place = ui.location_picker(code)
if not pcode:
    st.stop()
measure = st.sidebar.radio("Measure", ["confirmed", "suspected", "deaths"], horizontal=False)
df = data.weekly(code, pcode)
if df.empty:
    st.info("No data for this selection.")
    st.stop()
start, end = df["week_start"].min().date(), df["week_start"].max().date()
rng = st.sidebar.slider("Weeks shown", min_value=start, max_value=end, value=(start, end))
df = df[(df["week_start"].dt.date >= rng[0]) & (df["week_start"].dt.date <= rng[1])]

fig = go.Figure()
fig.add_bar(x=df["week_start"], y=df[measure], name=f"{measure} (weekly)", marker_color=ui.COLOURS["observed"], opacity=0.75)
stl = data.stl(code, pcode)
if not stl.empty and stl["series_measure"].iloc[0] == measure:
    s = stl[(stl["week_start"].dt.date >= rng[0]) & (stl["week_start"].dt.date <= rng[1])]
    fig.add_scatter(x=s["week_start"], y=s["trend"], name="STL trend", line={"color": ui.COLOURS["trend"], "width": 3})
fig.update_layout(height=420, legend={"orientation": "h"}, xaxis_title="Epidemiological week", yaxis_title="Cases")
st.plotly_chart(fig, use_container_width=True)

c1, c2, c3 = st.columns(3)
c1.metric("Total in range", f"{int(df[measure].sum(skipna=True)):,}")
c2.metric("Peak week", f"{int(df[measure].max(skipna=True)):,}",
          help=str(df.loc[df[measure].idxmax(), "week_start"].date()) if df[measure].notna().any() else None)
cfr = df.dropna(subset=["cfr_pct"])
c3.metric("Latest weekly CFR", f"{cfr['cfr_pct'].iloc[-1]:.1f}%" if not cfr.empty else "–",
          help="Naive CFR (deaths / cases x 100); recent weeks are provisional.")

if not stl.empty:
    with st.expander("STL decomposition (trend, seasonal, remainder)"):
        fig2 = make_subplots(rows=3, cols=1, shared_xaxes=True, subplot_titles=("Trend", "Seasonal", "Remainder"))
        fig2.add_scatter(x=stl["week_start"], y=stl["trend"], row=1, col=1, showlegend=False)
        fig2.add_scatter(x=stl["week_start"], y=stl["seasonal"], row=2, col=1, showlegend=False)
        fig2.add_bar(x=stl["week_start"], y=stl["remainder"], row=3, col=1, showlegend=False)
        fig2.update_layout(height=600)
        st.plotly_chart(fig2, use_container_width=True)
        st.caption(f"Decomposed series: weekly {stl['series_measure'].iloc[0]} cases, period 52 weeks, robust fitting.")

with st.expander("Data table"):
    cols = ["epi_year", "epi_week", "week_start", "suspected", "confirmed", "deaths", "cum_suspected",
            "cum_confirmed", "cum_deaths", "cfr_pct", "incidence_per_100k", "is_provisional", "quality_flags", "report_code"]
    st.dataframe(df[cols].sort_values("week_start", ascending=False), use_container_width=True, hide_index=True)
    st.download_button("Download CSV", df[cols].to_csv(index=False), file_name=f"{code}_{pcode}.csv")
