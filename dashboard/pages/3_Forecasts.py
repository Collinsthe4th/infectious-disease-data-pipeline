"""Lassa fever forecasts 1-4 weeks ahead and the model comparison (RQ4)."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import data
import ui

ui.page("Forecasts", "🔮")
run_id = data.latest_forecast_run()
if not run_id:
    st.info("No forecasts yet. They are produced by the analytics step (`pipeline analytics`).")
    st.stop()

live = data.forecasts(run_id, backtest=False)
backtest = data.forecasts(run_id, backtest=True)
evaln = data.evaluation(run_id)
places = sorted(live["location_name"].unique())
place = st.sidebar.selectbox("Location", places)
live = live[live["location_name"] == place]
backtest = backtest[backtest["location_name"] == place]
pcode = live["pcode"].iloc[0]
disease_name = live["disease_name"].iloc[0]

models = sorted(live["model_name"].unique())
best = None
if not evaln.empty:
    e = evaln[(evaln["pcode"] == pcode) & (evaln["horizon"] == 1)].drop_duplicates("model_name")
    if not e.empty:
        best = e.sort_values("mase").iloc[0]["model_name"]
model = st.sidebar.selectbox("Model for the fan chart", models, index=models.index(best) if best in models else 0)

disease_code = data.diseases().set_index("disease_name").loc[disease_name, "disease_code"]
hist = data.weekly(disease_code, pcode)
hist = hist[hist["week_start"] >= hist["week_start"].max() - pd.Timedelta(weeks=52)]
m = live[live["model_name"] == model].sort_values("target_week")

fig = go.Figure()
fig.add_bar(x=hist["week_start"], y=hist["confirmed"], name="observed confirmed", marker_color=ui.COLOURS["observed"], opacity=0.7)
if m["lower_95"].notna().any():
    fig.add_scatter(x=pd.concat([m["target_week"], m["target_week"][::-1]]),
                    y=pd.concat([m["upper_95"], m["lower_95"][::-1]]), fill="toself", fillcolor=ui.COLOURS["band"],
                    line={"width": 0}, name="95% interval", hoverinfo="skip")
if m["lower_80"].notna().any():
    fig.add_scatter(x=pd.concat([m["target_week"], m["target_week"][::-1]]),
                    y=pd.concat([m["upper_80"], m["lower_80"][::-1]]), fill="toself", fillcolor="rgba(230,126,34,0.32)",
                    line={"width": 0}, name="80% interval", hoverinfo="skip")
fig.add_scatter(x=m["target_week"], y=m["predicted"], name=f"{model} forecast", mode="lines+markers",
                line={"color": ui.COLOURS["forecast"], "width": 3})
fig.update_layout(height=430, legend={"orientation": "h"}, yaxis_title="Confirmed cases per week")
st.subheader(f"{disease_name}: next four weeks, {place}")
st.plotly_chart(fig, use_container_width=True)

table = live.pivot_table(index="model_name", columns="horizon", values="predicted").round(1)
table.columns = [f"h={c} ({live[live.horizon == c]['target_week'].iloc[0]:%d %b})" for c in table.columns]
st.dataframe(table, use_container_width=True)

st.subheader("Rolling-origin evaluation")
st.caption("Every model forecast the same weeks from the same origins using only data available at the time. "
           "MASE < 1 means better than the seasonal naive benchmark. DM = Diebold-Mariano test "
           "(negative statistic: the model is more accurate than the reference).")
if not evaln.empty:
    e = evaln[evaln["pcode"] == pcode]
    acc = e.drop_duplicates(["model_name", "horizon"]).pivot_table(index="model_name", columns="horizon", values="mase").round(3)
    acc.columns = [f"MASE h={c}" for c in acc.columns]
    st.dataframe(acc.sort_values(acc.columns[0]), use_container_width=True)
    ref = st.radio("DM reference model", sorted(e["dm_reference_model"].dropna().unique()), horizontal=True)
    dm = e[e["dm_reference_model"] == ref][["model_name", "horizon", "mae", "rmse", "mase", "dm_statistic", "dm_p_value"]]
    st.dataframe(dm.round(3), use_container_width=True, hide_index=True)

if not backtest.empty:
    with st.expander("Backtest forecasts vs actual"):
        h = st.select_slider("Horizon", options=sorted(backtest["horizon"].unique()))
        b = backtest[backtest["horizon"] == h]
        fig2 = go.Figure()
        actual = b.drop_duplicates("target_week")
        fig2.add_scatter(x=actual["target_week"], y=actual["actual"], name="actual", line={"color": "black", "width": 3})
        for name, g in b.groupby("model_name"):
            fig2.add_scatter(x=g["target_week"], y=g["predicted"], name=name, mode="lines")
        fig2.update_layout(height=420, legend={"orientation": "h"})
        st.plotly_chart(fig2, use_container_width=True)
