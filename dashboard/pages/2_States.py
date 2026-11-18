"""State-level comparison: totals, incidence and a state x week heat map."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

import data
import ui

ui.page("States", "🗺️")
code, name = ui.disease_picker()
df = data.weekly(code)
states = df[df["admin_level"] == 1] if not df.empty else df
if states.empty:
    st.info(f"No state-level figures for {name}. Many NCDC reports give only national totals for some diseases.")
    st.stop()

weeks = st.sidebar.slider("Last N weeks", 4, 156, 52, step=4)
cut = states["week_start"].max() - pd.Timedelta(weeks=weeks)
recent = states[states["week_start"] > cut]
measure = st.sidebar.radio("Measure", ["confirmed", "suspected", "deaths"])

totals = (recent.groupby(["location_name", "geo_zone"], dropna=False)[measure].sum(min_count=1)
          .reset_index().sort_values(measure, ascending=False))
pop = recent.groupby("location_name")["population"].max()
totals["per_100k"] = totals.apply(lambda r: (r[measure] * 100000 / pop[r["location_name"]])
                                  if pd.notna(pop.get(r["location_name"])) and pop.get(r["location_name"]) else None, axis=1)

left, right = st.columns([3, 2])
with left:
    st.subheader(f"{measure.title()} cases by state, last {weeks} weeks")
    fig = px.bar(totals, x=measure, y="location_name", color="geo_zone", orientation="h",
                 labels={"location_name": "", measure: "cases"}, height=max(400, 22 * len(totals)))
    fig.update_layout(yaxis={"categoryorder": "total ascending"}, legend_title_text="Zone")
    st.plotly_chart(fig, use_container_width=True)
with right:
    st.subheader("Table")
    st.dataframe(totals.rename(columns={"location_name": "state", "per_100k": "per 100,000"}).pipe(ui.rounded),
                 use_container_width=True, hide_index=True)
    if totals["per_100k"].isna().all():
        st.caption("Incidence per 100,000 needs state populations: run `pipeline refresh-population` then `pipeline init-db`.")

st.subheader("Weekly heat map")
pivot = recent.pivot_table(index="location_name", columns="week_start", values=measure, aggfunc="sum")
pivot = pivot.loc[pivot.sum(axis=1).sort_values(ascending=False).index]
fig = px.imshow(pivot, aspect="auto", color_continuous_scale="OrRd", labels={"color": measure, "x": "week", "y": ""})
fig.update_layout(height=max(350, 22 * len(pivot)))
st.plotly_chart(fig, use_container_width=True)
st.caption("Blank cells are weeks with no report for that state: they are gaps, not zeros. "
           "A choropleth map can be added once the HDX boundary GeoJSON is placed in data/reference/.")
