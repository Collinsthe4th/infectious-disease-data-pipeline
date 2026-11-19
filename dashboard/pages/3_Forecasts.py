"""Forecasts for the next four weeks, for every disease with enough data, and how accurate each method has been (RQ4)."""

from __future__ import annotations

import importlib

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import data
import src.analytics.series as series_mod
import ui

series_mod = importlib.reload(series_mod)  # Streamlit Cloud can keep an old copy after an update

ui.page("Forecasts", "🔮", "Expected cases for the next four weeks, and which forecasting method has been most "
        "accurate so far. Forecasts are research estimates, not official projections.")
disease_code, disease_name = ui.disease_picker()
hist_all = data.weekly(disease_code, "NG")
measure = series_mod.forecast_measure(disease_code, hist_all) if not hist_all.empty else "confirmed"

run_id = data.latest_forecast_run()
live = data.forecasts(run_id, backtest=False) if run_id else pd.DataFrame()
live = live[live["disease_code"] == disease_code] if not live.empty else live
if live.empty:
    ok, why = (series_mod.forecast_eligibility(series_mod.to_weekly_series(hist_all, measure), 4)
               if not hist_all.empty else (False, "no weekly figures have been loaded"))
    st.info(f"**No forecast for {disease_name} yet.** The seven forecasting methods need about two years of "
            f"recent, regular weekly reports to learn from and to be tested on. For {disease_name}: {why}. "
            "A forecast is made automatically once enough reports exist.")
    if not hist_all.empty:
        col, _ = ui.best_series(hist_all)
        if col:
            st.bar_chart(hist_all.set_index("week_start")[col], height=260)
            st.caption(f"{ui.label(col)} reported each week, all of Nigeria.")
    st.stop()

backtest = data.forecasts(run_id, backtest=True)
backtest = backtest[backtest["disease_code"] == disease_code]
evaln = data.evaluation(run_id)
evaln = evaln[evaln["disease_code"] == disease_code] if not evaln.empty else evaln

places = sorted(live["location_name"].unique())
place = st.sidebar.selectbox("Place", places) if len(places) > 1 else places[0]
live = live[live["location_name"] == place]
backtest = backtest[backtest["location_name"] == place]
pcode = live["pcode"].iloc[0]
what = "confirmed" if measure == "confirmed" else "suspected"

models = sorted(live["model_name"].unique())
best = None
e1 = evaln[(evaln["pcode"] == pcode) & (evaln["horizon"] == 1)].drop_duplicates("model_name") if not evaln.empty else evaln
if not e1.empty:
    best = e1.sort_values("mase").iloc[0]["model_name"]
model = st.sidebar.selectbox("Forecasting method", models, index=models.index(best) if best in models else 0,
                             format_func=lambda m: ui.MODEL_LABELS.get(m, m))

hist = data.weekly(disease_code, pcode)
hist = hist[hist["week_start"] >= hist["week_start"].max() - pd.Timedelta(weeks=52)]
m = live[live["model_name"] == model].sort_values("target_week")

fig = go.Figure()
fig.add_bar(x=hist["week_start"], y=hist[measure], name=f"{what.capitalize()} cases (reported)",
            marker_color=ui.COLOURS["observed"], opacity=0.7)
if m["lower_95"].notna().any():
    fig.add_scatter(x=pd.concat([m["target_week"], m["target_week"][::-1]]),
                    y=pd.concat([m["upper_95"], m["lower_95"][::-1]]), fill="toself", fillcolor=ui.COLOURS["band"],
                    line={"width": 0}, name="Likely range (95%)", hoverinfo="skip")
fig.add_scatter(x=m["target_week"], y=m["predicted"], name="Forecast", mode="lines+markers",
                line={"color": ui.COLOURS["forecast"], "width": 3})
fig.update_layout(height=430, legend={"orientation": "h"}, yaxis_title=f"{what.capitalize()} cases per week", xaxis_title="Week")
st.subheader(f"{disease_name}: next four weeks — {place}")
st.plotly_chart(fig, use_container_width=True)

cols = st.columns(len(m)) if len(m) else []
for c, (_, r) in zip(cols, m.iterrows()):
    rng = "" if pd.isna(r["lower_95"]) else f"likely {r['lower_95']:.0f}–{r['upper_95']:.0f}"
    c.metric(f"Week of {r['target_week']:%d %b}", f"{r['predicted']:.0f} cases", help=rng or None)
if best:
    st.caption(f"Most accurate method so far for next-week forecasts: **{ui.MODEL_LABELS.get(best, best)}**.")

st.subheader("How accurate has each method been?")
st.caption("Each method was tested on past weeks using only the data that existed at the time. "
           "The score compares its typical error with the simple 'same week last year' guess: "
           "**below 1 means more accurate than that guess**, above 1 means less accurate.")
if not evaln.empty:
    e = evaln[evaln["pcode"] == pcode].drop_duplicates(["model_name", "horizon"])
    acc = e.pivot_table(index="model_name", columns="horizon", values="mase").round(2)
    acc.columns = [f"{c} week{'s' if c > 1 else ''} ahead" for c in acc.columns]
    acc = acc.sort_values(acc.columns[0])
    acc.index = [ui.MODEL_LABELS.get(i, i) for i in acc.index]
    st.dataframe(acc, use_container_width=True)
    with st.expander("Is the difference real? (statistical test)"):
        refs = sorted(evaln["dm_reference_model"].dropna().unique())
        if refs:
            ref = st.radio("Compare against", refs, horizontal=True, format_func=lambda m: ui.MODEL_LABELS.get(m, m))
            dm = evaln[(evaln["pcode"] == pcode) & (evaln["dm_reference_model"] == ref)].copy()
            dm["Method"] = dm["model_name"].map(lambda x: ui.MODEL_LABELS.get(x, x))
            dm["Weeks ahead"] = dm["horizon"]
            dm["Average error (cases)"] = dm["mae"].round(1)
            dm["Clearly different?"] = dm["dm_p_value"].map(
                lambda p: "–" if pd.isna(p) else ("Yes" if p < 0.05 else "No"))
            dm["p-value"] = dm["dm_p_value"].round(3)
            st.dataframe(dm[["Method", "Weeks ahead", "Average error (cases)", "Clearly different?", "p-value"]],
                         use_container_width=True, hide_index=True)
            st.caption("Diebold–Mariano test. 'Yes' means the accuracy difference is unlikely to be luck (p < 0.05).")

if not backtest.empty:
    with st.expander("Past forecasts compared with what actually happened"):
        h = st.select_slider("Weeks ahead", options=sorted(backtest["horizon"].unique()))
        b = backtest[backtest["horizon"] == h]
        fig2 = go.Figure()
        actual = b.drop_duplicates("target_week")
        fig2.add_scatter(x=actual["target_week"], y=actual["actual"], name="What happened",
                         line={"color": "black", "width": 3})
        for nm, g in b.groupby("model_name"):
            fig2.add_scatter(x=g["target_week"], y=g["predicted"], name=ui.MODEL_LABELS.get(nm, nm), mode="lines")
        fig2.update_layout(height=420, legend={"orientation": "h"})
        st.plotly_chart(fig2, use_container_width=True)
