"""Compare states: totals, cases per 100,000 people and a week-by-week heat map."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

import data
import ui

ui.page("By state", "🗺️", "Which states are reporting the most cases.")
code, name = ui.disease_picker()
df = data.weekly(code)
states = df[df["admin_level"] == 1] if not df.empty else df
if states.empty:
    st.info(f"There are no state-by-state figures for {name} yet. Many NCDC reports give only a national total, "
            "and the pipeline only stores state figures when the report's table clearly says whether they are "
            "weekly or running totals.")
    st.stop()

available = [c for c in ("confirmed", "suspected", "deaths") if pd.to_numeric(states[c], errors="coerce").notna().any()]
if not available:
    st.info("Only running totals are available for states of this disease.")
    st.stop()
measure = st.sidebar.radio("Show", available, format_func=ui.label)
weeks = st.sidebar.slider("Last how many weeks", 4, 156, 52, step=4)
recent = states[states["week_start"] > states["week_start"].max() - pd.Timedelta(weeks=weeks)]

totals = (recent.groupby(["location_name", "geo_zone"], dropna=False)[measure].sum(min_count=1)
          .reset_index().dropna(subset=[measure]).sort_values(measure, ascending=False))
pop = recent.groupby("location_name")["population"].max()
totals["per_100k"] = [
    (v * 100000 / pop[n]) if pd.notna(pop.get(n)) and pop.get(n) else None
    for n, v in zip(totals["location_name"], totals[measure])
]

left, right = st.columns([3, 2])
with left:
    st.subheader(f"{ui.label(measure)}, last {weeks} weeks")
    fig = px.bar(totals, x=measure, y="location_name", color="geo_zone", orientation="h",
                 labels={"location_name": "", measure: "Number of people", "geo_zone": "Zone"},
                 height=max(400, 24 * len(totals)))
    fig.update_layout(yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, use_container_width=True)
with right:
    st.subheader("Table")
    table = totals.rename(columns={"location_name": "State", "geo_zone": "Zone", measure: ui.label(measure),
                                   "per_100k": "Per 100,000 people"})
    st.dataframe(ui.rounded(table, 1), use_container_width=True, hide_index=True)
    if totals["per_100k"].isna().all():
        st.caption("'Per 100,000 people' appears once state populations are loaded.")

st.subheader("Week by week")
pivot = recent.pivot_table(index="location_name", columns="week_start", values=measure, aggfunc="sum")
if not pivot.empty:
    pivot = pivot.loc[pivot.sum(axis=1).sort_values(ascending=False).index]
    fig = px.imshow(pivot, aspect="auto", color_continuous_scale="OrRd",
                    labels={"color": "People", "x": "Week", "y": ""})
    fig.update_layout(height=max(350, 24 * len(pivot)))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Darker = more cases. Blank squares mean no report for that state that week (not zero).")
