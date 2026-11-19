"""Overview: latest week for every disease, and the chance of an outbreak for the selected one.

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
import importlib  # noqa: E402

import src.analytics.severity as severity  # noqa: E402

# Streamlit Cloud keeps old copies of modules from outside the dashboard folder in memory after
# an update, which caused "ImportError" once new names were added. Reload to always use the latest code.
severity = importlib.reload(severity)
HIGH_CHANCE, MODERATE_CHANCE, LOW_CHANCE, NO_DATA = (severity.HIGH_CHANCE, severity.MODERATE_CHANCE,
                                                     severity.LOW_CHANCE, severity.NO_DATA)
outbreak_chance = severity.outbreak_chance

ui.page("Infectious Disease Surveillance — Nigeria",
        subtitle="Real figures from NCDC situation reports, updated automatically every week. "
                 "The four most recent weeks may still change.")

weekly = data.national_weekly_all()
if weekly.empty:
    st.info("No data has been loaded yet.")
    st.stop()

# ----------------------------------------------------------------------------- latest week, every disease
st.subheader("Latest reported week, all of Nigeria")
cols = st.columns(4)
for i, (disease, group) in enumerate(weekly.groupby("disease_name", sort=True)):
    col, _ = ui.best_series(group)
    with cols[i % 4]:
        if col is None:
            st.metric(disease, "–", help="No figures published yet.")
            continue
        g = group.dropna(subset=[col]).sort_values("week_start")
        now = g.iloc[-1]
        prev = g.iloc[-2] if len(g) > 1 else None
        delta = None
        if prev is not None and not col.startswith("cum_") and (now["week_start"] - prev["week_start"]).days == 7:
            delta = int(now[col] - prev[col])
        st.metric(f"{disease} — {ui.label(col).lower()}", f"{int(now[col]):,}", delta=delta, delta_color="inverse",
                  help=f"Week starting {now['week_start']:%d %b %Y}" + (" · may still change" if now["is_provisional"] else "")
                  + (" · NCDC now publishes only running totals for this disease" if col.startswith("cum_") else ""))
        st.caption(f"week of {now['week_start']:%d %b %Y}")

# ----------------------------------------------------------------------------- outbreak chance, selected disease
st.divider()
code, name = ui.disease_picker()
nat = weekly[weekly["disease_code"] == code]
alerts = data.national_alerts(12)
alerts = alerts[alerts["disease_code"] == code] if not alerts.empty else alerts

forecast = None
run_id = data.latest_forecast_run()
if run_id:
    live = data.forecasts(run_id, backtest=False)
    live = live[(live["pcode"] == "NG") & (live["disease_name"] == name)] if not live.empty else live
    if not live.empty:
        evaln = data.evaluation(run_id)
        e1 = evaln[(evaln["pcode"] == "NG") & (evaln["horizon"] == 1)].drop_duplicates("model_name") if not evaln.empty else evaln
        best = e1.sort_values("mase").iloc[0]["model_name"] if not e1.empty else live["model_name"].iloc[0]
        forecast = live[live["model_name"] == best]

chance = outbreak_chance(nat, alerts, forecast)
STYLE = {HIGH_CHANCE: ("🔴", "#fdecea", "#b71c1c"), MODERATE_CHANCE: ("🟠", "#fff4e5", "#b45309"),
         LOW_CHANCE: ("🟢", "#edf7ed", "#1b5e20"), NO_DATA: ("⚪", "#f2f2f2", "#555")}
icon, bg, fg = STYLE[chance.level]
why = "; ".join(chance.reasons)
st.markdown(
    f"""<div style="background:{bg};border-left:8px solid {fg};border-radius:8px;padding:14px 18px;margin:6px 0 4px">
    <div style="font-size:0.9rem;color:#444">Chance of an outbreak — {name}</div>
    <div style="font-size:1.8rem;font-weight:700;color:{fg}">{icon} {chance.level}</div>
    <div style="font-size:0.95rem;margin-top:4px">{why[0].upper() + why[1:]}.</div></div>""",
    unsafe_allow_html=True)
st.caption(("Forecast included. " if forecast is not None else "No forecast for this disease (forecasts are made for "
            "Lassa fever), so this uses the alert checks and recent weeks only. ")
           + "A research indicator to show where to look first — not an official NCDC alert.")
with st.expander("How is this decided?"):
    st.markdown("""
| Level | Rule |
|---|---|
| 🔴 **High chance** | Two or more alert checks fired in the last 2 weeks; **or** one fired and the next 4 weeks are forecast at 1.5 times the usual level for this time of year or more; **or** the forecast alone is at least double the usual level |
| 🟠 **Moderate chance** | One alert check fired; **or** the forecast is at least 1.5 times the usual level; **or** this week is already 1.5 times the usual level |
| 🟢 **Low chance** | None of the above |

*Usual level* = the average for the same weeks of the year in earlier years. The alert checks are described on the
**Outbreak alerts** page.
""")

# ----------------------------------------------------------------------------- trend, selected disease
col, note = ui.best_series(nat)
if col is not None:
    recent = nat[nat["week_start"] >= nat["week_start"].max() - pd.Timedelta(weeks=104)]
    shown = [c for c in (["suspected", "confirmed", "deaths"] if not col.startswith("cum_") else [col])
             if pd.to_numeric(recent[c], errors="coerce").notna().any()]
    long = recent.melt(id_vars=["week_start"], value_vars=shown, var_name="measure", value_name="people").dropna()
    long["measure"] = long["measure"].map(ui.label)
    fig = px.line(long, x="week_start", y="people", color="measure", markers=True,
                  labels={"week_start": "Week", "people": "Number of people", "measure": ""},
                  title=f"{name}: last two years")
    if forecast is not None:
        fig.add_scatter(x=forecast["target_week"], y=forecast["predicted"], mode="lines+markers", name="Forecast",
                        line={"dash": "dash", "color": ui.COLOURS["forecast"]})
    fig.update_layout(legend={"orientation": "h"})
    st.plotly_chart(fig, use_container_width=True)
    if note:
        st.caption(note)
