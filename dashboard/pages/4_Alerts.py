"""Outbreak alert flags: EARS C1-C3 and SARIMA residual z-scores (RQ3)."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

import data
import ui

ui.page("Outbreak alerts", "🚨")
weeks = st.sidebar.slider("Look back (weeks)", 1, 52, 8)
recent = data.alerts(weeks)
active = recent[recent["is_alert"]] if not recent.empty else recent

st.subheader(f"Weeks above threshold, last {weeks} weeks")
if active.empty:
    st.success("No alert thresholds crossed.")
else:
    summary = active.groupby(["disease_name", "location_name"]).agg(
        alerts=("method", "count"), methods=("method", lambda s: ", ".join(sorted(set(s)))),
        latest=("week_start", "max"), max_statistic=("statistic", "max")).reset_index()
    st.dataframe(summary.sort_values(["latest", "max_statistic"], ascending=False).round(2),
                 use_container_width=True, hide_index=True)

st.subheader("Signal for one series")
code, name = ui.disease_picker()
pcode, place = ui.location_picker(code)
if pcode:
    s = data.alert_series(code, pcode)
    if s.empty:
        st.info("No signals computed for this series yet.")
    else:
        method = st.radio("Method", sorted(s["method"].unique()), horizontal=True)
        m = s[s["method"] == method]
        fig = go.Figure()
        fig.add_scatter(x=m["week_start"], y=m["statistic"], name="statistic", line={"color": "#1f4e79"})
        fig.add_hline(y=float(m["threshold"].iloc[0]), line_dash="dash", line_color="red", annotation_text="threshold")
        flagged = m[m["is_alert"]]
        fig.add_scatter(x=flagged["week_start"], y=flagged["statistic"], mode="markers", name="alert",
                        marker={"color": "red", "size": 9})
        fig.update_layout(height=380, legend={"orientation": "h"})
        st.plotly_chart(fig, use_container_width=True)
st.caption("EARS C1: current week vs the previous 7 weeks. C2: same with a 2-week gap. C3: sum of C2 excesses over 3 weeks. "
           "Residual z: observed vs SARIMA one-step forecast, which accounts for seasonality. "
           "An alert is a signal to look closer, not a confirmed outbreak.")
