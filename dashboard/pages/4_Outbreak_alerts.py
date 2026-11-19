"""Weeks where cases were unusually high (EARS C1-C3 and SARIMA residual z-scores, RQ3)."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

import data
import ui

ui.page("Outbreak alerts", "🚨", "Weeks where cases were unusually high compared with what is normal. "
        "An alert is a signal to look closer, not a confirmed outbreak.")
weeks = st.sidebar.slider("Look back (weeks)", 1, 52, 8)
recent = data.alerts(weeks)
active = recent[recent["is_alert"]] if not recent.empty else recent

st.subheader(f"Alerts in the last {weeks} weeks")
if active.empty:
    st.success("No unusual weeks found.")
else:
    a = active.assign(check=active["method"].map(lambda m: ui.METHOD_LABELS.get(m, m)))
    summary = (a.groupby(["disease_name", "location_name"])
               .agg(alerts=("check", "count"), checks=("check", lambda s: ", ".join(sorted(set(s)))),
                    latest=("week_start", "max"), strongest=("statistic", "max"))
               .reset_index().sort_values(["latest", "strongest"], ascending=False))
    summary["latest"] = summary["latest"].dt.date
    summary = summary.rename(columns={"disease_name": "Disease", "location_name": "Place", "alerts": "Number of alerts",
                                      "checks": "Which checks fired", "latest": "Most recent week",
                                      "strongest": "Strongest alert score"})
    st.dataframe(ui.rounded(summary, 1), use_container_width=True, hide_index=True)

st.subheader("Look at one disease")
code, name = ui.disease_picker()
pcode, place = ui.location_picker(code)
if pcode:
    s = data.alert_series(code, pcode)
    if s.empty:
        st.info("Alerts are calculated once a few weeks of weekly figures are available for this place.")
    else:
        methods = sorted(s["method"].unique())
        method = st.radio("Check", methods, horizontal=True, format_func=lambda m: ui.METHOD_LABELS.get(m, m))
        m = s[s["method"] == method]
        fig = go.Figure()
        fig.add_scatter(x=m["week_start"], y=m["statistic"], name="Alert score", line={"color": "#1f4e79"})
        fig.add_hline(y=float(m["threshold"].iloc[0]), line_dash="dash", line_color="red", annotation_text="alert line")
        flagged = m[m["is_alert"]]
        fig.add_scatter(x=flagged["week_start"], y=flagged["statistic"], mode="markers", name="Alert",
                        marker={"color": "red", "size": 9})
        fig.update_layout(height=380, legend={"orientation": "h"}, xaxis_title="Week", yaxis_title="Alert score")
        st.plotly_chart(fig, use_container_width=True)

with st.expander("What do the checks mean?"):
    st.markdown("""
- **Sudden jump (vs last 7 weeks)** — this week compared with the average of the previous 7 weeks.
- **Jump (vs weeks 3–9 before)** — the same, but ignoring the last 2 weeks so a slow rise still shows.
- **Steady build-up (3 weeks)** — adds up smaller rises over 3 weeks in a row.
- **Above seasonal forecast** — compares the week with what is normal *for that time of year*, so the usual
  dry-season Lassa peak does not trigger an alert by itself.

The first three are the CDC's EARS C1, C2 and C3 methods adapted to weeks; the last uses a SARIMA model.
An alert score above the dashed line counts as an alert.
""")
