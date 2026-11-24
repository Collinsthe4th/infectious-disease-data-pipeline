"""Weekly cases over time, with the long-term trend and the yearly seasonal pattern (RQ3)."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import data
import ui

ui.page("Trends over time", "📈", "How cases have changed week by week, and the usual yearly pattern.")
code, name = ui.disease_picker()
pcode, place = ui.location_picker(code)
if not pcode:
    st.info(f"No figures for {name} have been loaded yet.")
    st.stop()

df = data.weekly(code, pcode)
available = [c for c in ("confirmed", "suspected", "deaths") if pd.to_numeric(df[c], errors="coerce").notna().any()]
default_col, note = ui.best_series(df)
if default_col is None:
    st.info(note)
    st.stop()
if available:
    measure = st.sidebar.radio("Show", available, index=available.index(default_col) if default_col in available else 0,
                               format_func=ui.label)
else:
    measure = default_col

start, end = df["week_start"].min().date(), df["week_start"].max().date()
if start < end:
    rng = st.sidebar.slider("Period", min_value=start, max_value=end, value=(start, end), format="MMM YYYY")
    df = df[(df["week_start"].dt.date >= rng[0]) & (df["week_start"].dt.date <= rng[1])]

values = pd.to_numeric(df[measure], errors="coerce")
fig = go.Figure()
if measure.startswith("cum_"):
    fig.add_scatter(x=df["week_start"], y=values, mode="lines+markers", name=ui.label(measure),
                    line={"color": ui.COLOURS["observed"]})
else:
    fig.add_bar(x=df["week_start"], y=values, name=ui.label(measure), marker_color=ui.COLOURS["observed"], opacity=0.75)
    stl = data.stl(code, pcode)
    if not stl.empty and stl["series_measure"].iloc[0] == measure:
        s = stl[stl["week_start"].between(df["week_start"].min(), df["week_start"].max())]
        fig.add_scatter(x=s["week_start"], y=s["trend"], name="Long-term trend",
                        line={"color": ui.COLOURS["trend"], "width": 3})
xs, ys = ui.key_points(df["week_start"], values)
if measure.startswith("cum_"):
    xs, ys = xs[:1], ys[:1]  # a running total: only the latest value is worth labelling
ui.label_points(fig, xs, ys)
fig.update_layout(height=420, legend={"orientation": "h"}, xaxis_title="Week", yaxis_title="Number of people")
st.subheader(f"{name} — {place}")
st.plotly_chart(fig, use_container_width=True)
if note and measure.startswith("cum_"):
    st.caption(note)

c1, c2, c3 = st.columns(3)
if values.notna().any():
    if not measure.startswith("cum_"):
        c1.metric("Total in this period", f"{int(values.sum()):,}")
        peak_idx = values.idxmax()
        c2.metric("Busiest week", f"{int(values.max()):,}", help=f"Week of {df.loc[peak_idx, 'week_start']:%d %b %Y}")
    else:
        c1.metric("Latest year-to-date total", f"{int(values.dropna().iloc[-1]):,}")
cfr = df.dropna(subset=["cfr_pct"])
c3.metric("Latest death rate", f"{cfr['cfr_pct'].iloc[-1]:.1f}%" if not cfr.empty else "–",
          help="Deaths divided by cases, as a percentage. Recent weeks can still change.")

stl = data.stl(code, pcode)
if not stl.empty and not measure.startswith("cum_"):
    with st.expander("Yearly pattern (seasonal decomposition)"):
        fig2 = make_subplots(rows=3, cols=1, shared_xaxes=True,
                             subplot_titles=("Long-term trend", "Usual yearly pattern", "What's left over (unusual weeks)"))
        fig2.add_scatter(x=stl["week_start"], y=stl["trend"], row=1, col=1, showlegend=False)
        fig2.add_scatter(x=stl["week_start"], y=stl["seasonal"], row=2, col=1, showlegend=False)
        fig2.add_bar(x=stl["week_start"], y=stl["remainder"], row=3, col=1, showlegend=False)
        fig2.update_layout(height=600)
        st.plotly_chart(fig2, use_container_width=True)
        st.caption("The weekly series split into a slow trend, a pattern that repeats every year, and the rest "
                   "(STL decomposition). Big bars in the bottom panel are unusual weeks.")

with st.expander("See the numbers"):
    cols = ["week_start", "epi_year", "epi_week", "suspected", "confirmed", "deaths", "cum_suspected",
            "cum_confirmed", "cum_deaths", "cfr_pct", "incidence_per_100k", "is_provisional", "report_code"]
    table = df[cols].sort_values("week_start", ascending=False).copy()
    table["week_start"] = table["week_start"].dt.date
    st.dataframe(ui.friendly(table), use_container_width=True, hide_index=True)
    st.download_button("Download as CSV", df[cols].to_csv(index=False), file_name=f"{code}_{pcode}.csv")
