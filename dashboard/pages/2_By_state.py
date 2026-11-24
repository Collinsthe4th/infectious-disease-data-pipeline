"""States: a map of Nigeria shaded by cases per 100,000 people, a ranking, and trends over time."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import data
import ui

GEOJSON = Path(__file__).resolve().parents[2] / "data" / "reference" / "nga_admin1.geojson"

ui.page("By state", "🗺️", "Where cases are being reported, adjusted for how many people live in each state.")
code, name = ui.disease_picker()
df = data.weekly(code)
states = df[df["admin_level"] == 1].copy() if not df.empty else df
if states.empty:
    st.info(f"There are no state-by-state figures for {name} yet. NCDC's {name} reports give only a national "
            "total in a form the pipeline can read reliably, so no state figures are shown rather than guesses.")
    st.stop()

# ----------------------------------------------------------------------------- choose period
years = sorted(states["epi_year"].unique(), reverse=True)
year = st.sidebar.selectbox("Year", years, index=0)
states_box = st.sidebar.container()  # the state picker is filled in below, once the ranking is known
measure = st.sidebar.radio("Cases", ["cum_confirmed", "cum_suspected", "cum_deaths"],
                           format_func=lambda c: {"cum_confirmed": "Confirmed", "cum_suspected": "Suspected",
                                                  "cum_deaths": "Deaths"}[c])
yr = states[states["epi_year"] == year]
latest_week = yr["week_start"].max()
# The newest running total for each state this year (NCDC's Table 3 "Cumulative" column).
latest = (yr.dropna(subset=[measure]).sort_values("week_start").groupby("pcode").tail(1)
          [["pcode", "location_name", "geo_zone", "population", "week_start", measure]])
if latest.empty:
    st.info(f"No {ui.label(measure).lower()} figures by state for {year}.")
    st.stop()
latest["per_100k"] = latest[measure] * 100000 / latest["population"]
week_label = f"week {int(yr.loc[yr['week_start'] == latest_week, 'epi_week'].iloc[0])}, {year}"
what = {"cum_confirmed": "confirmed cases", "cum_suspected": "suspected cases", "cum_deaths": "deaths"}[measure]

# ----------------------------------------------------------------------------- map
st.subheader(f"{name}: {what} per 100,000 people, {year} so far (up to {week_label})")
if not GEOJSON.exists():
    st.warning("The map outline of Nigeria has not been downloaded yet (run the 'Update reference data' workflow).")
elif latest["population"].isna().all():
    st.warning("State populations are not loaded yet, so the map cannot show cases per 100,000 people.")
else:
    geo = json.loads(GEOJSON.read_text(encoding="utf-8"))
    every = pd.DataFrame([{"pcode": f["properties"]["pcode"], "State": f["properties"]["shapeName"]}
                          for f in geo["features"]])
    shown = every.merge(latest, on="pcode", how="left")
    shown["Cases"] = shown[measure]
    shown["Per 100,000"] = shown["per_100k"].round(2)
    # Every state drawn first in light grey, so the whole country is always visible ...
    fig = go.Figure(go.Choropleth(
        geojson=geo, locations=every["pcode"], featureidkey="properties.pcode", z=[0] * len(every),
        colorscale=[[0, "#d9d9d9"], [1, "#d9d9d9"]], showscale=False, text=every["State"],
        hovertemplate="%{text}<br>No cases listed<extra></extra>",
        marker={"line": {"color": "#7a7a7a", "width": 0.8}}))
    # ... then the states with figures, shaded by cases per 100,000 people.
    have = shown.dropna(subset=["per_100k"])
    fig.add_trace(go.Choropleth(
        geojson=geo, locations=have["pcode"], featureidkey="properties.pcode", z=have["per_100k"],
        colorscale="OrRd", zmin=0, zmax=max(float(have["per_100k"].max()), 0.01) if not have.empty else 1,
        customdata=have[["State", "Cases"]], marker={"line": {"color": "#7a7a7a", "width": 0.8}},
        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]:,} cases<br>%{z:.2f} per 100,000<extra></extra>",
        colorbar={"title": "Per 100,000", "thickness": 14}))
    fig.update_geos(fitbounds="locations", visible=False, bgcolor="rgba(0,0,0,0)")
    fig.update_layout(height=560, margin={"l": 0, "r": 0, "t": 0, "b": 0}, paper_bgcolor="rgba(0,0,0,0)",
                      geo={"bgcolor": "rgba(0,0,0,0)"})
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Darker = more cases for the size of the population. Grey = no cases listed for that state in the report. "
               "Population: 2022 estimates (HDX / National Population Commission). Map: geoBoundaries (CC BY 4.0).")

# ----------------------------------------------------------------------------- ranking
left, right = st.columns([3, 2])
ranked = latest.sort_values("per_100k", ascending=False)
with left:
    st.subheader("Highest rates")
    fig = px.bar(ranked.head(15), x="per_100k", y="location_name", orientation="h", color="geo_zone",
                 labels={"per_100k": "Per 100,000 people", "location_name": "", "geo_zone": "Zone"},
                 hover_data={measure: ":,"})
    ui.label_bars(fig, "%{x:.2f}")
    fig.update_layout(yaxis={"categoryorder": "total ascending"}, height=460)
    st.plotly_chart(fig, use_container_width=True)
with right:
    st.subheader("All states")
    table = ranked.rename(columns={"location_name": "State", measure: ui.label(measure),
                                   "per_100k": "Per 100,000 people"})[["State", ui.label(measure), "Per 100,000 people"]]
    st.dataframe(ui.rounded(table, 2), use_container_width=True, hide_index=True, height=460)

# ----------------------------------------------------------------------------- over time
st.subheader("Week by week")
weekly_col = {"cum_confirmed": "confirmed", "cum_suspected": "suspected", "cum_deaths": "deaths"}[measure]
top = ranked["pcode"].head(5).tolist()
names = dict(zip(states["pcode"], states["location_name"]))
pick = states_box.multiselect("States for the weekly chart", sorted(names, key=names.get), default=top,
                            format_func=names.get, help="Starts with the 5 states with the highest rates.")
series = yr[yr["pcode"].isin(pick)].dropna(subset=[weekly_col])
if series.empty:
    st.caption("No weekly figures for the chosen states this year.")
else:
    fig = px.line(series, x="week_start", y=weekly_col, color="location_name", markers=True,
                  labels={"week_start": "Week", weekly_col: f"New {what} that week", "location_name": "State"})
    ends = series.sort_values("week_start").groupby("location_name").tail(1)
    ui.label_points(fig, ends["week_start"], ends[weekly_col])
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Each point is one week's new cases as printed in that week's NCDC report.")

st.caption("Source: Table 3 of NCDC's weekly situation reports. That table is a picture inside the PDF, so it is "
           "read with OCR and only used when every column adds up to the table's own total and the national "
           "confirmed total matches Table 1; reports that fail the check are left out.")
