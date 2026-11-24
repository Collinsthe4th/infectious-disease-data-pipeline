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
import src.analytics.series as series_mod  # noqa: E402

series_mod = importlib.reload(series_mod)

ui.page("Infectious Disease Surveillance — Nigeria",
        subtitle="Real figures from NCDC situation reports, updated automatically every week. "
                 "The four most recent weeks may still change.")

weekly = data.national_weekly_all()
if weekly.empty:
    st.info("No data has been loaded yet.")
    st.stop()

# ----------------------------------------------------------------------------- risk across all diseases
TONE = {HIGH_CHANCE: "#c62828", MODERATE_CHANCE: "#c2570c", LOW_CHANCE: "#2e7d32", NO_DATA: "#8b8d98"}
SHORT = {HIGH_CHANCE: "High", MODERATE_CHANCE: "Moderate", LOW_CHANCE: "Low", NO_DATA: "No recent data"}
all_alerts = data.national_alerts(12)


def assess(code: str):
    """Outbreak chance for one disease (national), with its forecast when there is one."""
    nat_d = weekly[weekly["disease_code"] == code]
    al = all_alerts[all_alerts["disease_code"] == code] if not all_alerts.empty else all_alerts
    fc, _ = data.live_forecast(code)
    measure = series_mod.forecast_measure(code, nat_d)
    return outbreak_chance(nat_d, al, fc, forecast_measure=measure), fc, measure


assessed = {c: assess(c) for c in sorted(weekly["disease_code"].unique())}
names_by_code = dict(zip(weekly["disease_code"], weekly["disease_name"]))
order = {HIGH_CHANCE: 0, MODERATE_CHANCE: 1, LOW_CHANCE: 2, NO_DATA: 3}
tiles = []
for c, (ch, fc, _) in sorted(assessed.items(), key=lambda kv: (order[kv[1][0].level], names_by_code[kv[0]])):
    last = weekly[(weekly["disease_code"] == c)].dropna(subset=["suspected", "confirmed", "deaths"], how="all")
    sub = (f"last weekly report {last['week_start'].max():%d %b %Y}" if not last.empty else "running totals only")
    tiles.append(f'<div class="rs-tile" style="--t:{TONE[ch.level]}"><div class="rs-name">{names_by_code[c]}</div>'
                 f'<div class="rs-level">{SHORT[ch.level]}</div><div class="rs-sub">{sub}'
                 f'{" · forecast" if fc is not None else ""}</div></div>')
st.subheader("Chance of an outbreak: all seven diseases")
st.markdown("""<style>
.rs{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin:4px 0 4px}
.rs-tile{border:3px solid var(--t);border-radius:10px;padding:9px 12px;
  background:color-mix(in srgb,var(--t) 8%,transparent)}
.rs-name{font-size:.85rem;font-weight:600}
.rs-level{font-size:1.15rem;font-weight:750;color:var(--t)}
.rs-sub{font-size:.72rem;opacity:.7}
</style><div class="rs">""" + "".join(tiles) + "</div>", unsafe_allow_html=True)
st.caption("Based on the alert checks, recent weeks compared with the usual level, and the forecast where there is "
           "one. 'No recent data' means NCDC has not published weekly figures for that disease recently. "
           "Choose a disease in the sidebar for the details below.")

# ----------------------------------------------------------------------------- this week's reports
def sparkline(values: list[float], colour: str, width: int = 120, height: int = 34) -> str:
    """A tiny inline SVG line chart."""
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    step = width / (len(values) - 1)
    pts = " ".join(f"{i * step:.1f},{height - 3 - (v - lo) / span * (height - 6):.1f}" for i, v in enumerate(values))
    last_x, last_y = pts.split(" ")[-1].split(",")
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<polyline fill="none" stroke="{colour}" stroke-width="2" points="{pts}"/>'
            f'<circle cx="{last_x}" cy="{last_y}" r="3" fill="{colour}"/></svg>')


CARD_CSS = """<style>
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px;margin:6px 0 10px}
.card{border:3px solid rgba(128,128,128,.35);border-radius:14px;padding:14px 16px;background:rgba(128,128,128,.07)}
.card .name{font-size:.95rem;font-weight:600}
.card .row{display:flex;justify-content:space-between;align-items:flex-end;margin-top:4px}
.card .num{font-size:2rem;font-weight:700;line-height:1.1}
.card .what{font-size:.8rem;opacity:.75}
.card .chg{font-size:.85rem;font-weight:600;margin-top:2px}
.card .more{font-size:.78rem;opacity:.75;margin-top:6px}
.card.muted{opacity:.8;border-style:dashed}
.up{color:#e5484d}.down{color:#30a46c}.flat{color:#8b8d98}
</style>"""

has_figure = weekly.dropna(subset=["suspected", "confirmed", "deaths"], how="all")
current = has_figure["week_start"].max() if not has_figure.empty else weekly["week_start"].max()
cw = weekly[weekly["week_start"] == current]
ew, ey = int(cw["epi_week"].iloc[0]), int(cw["epi_year"].iloc[0])
st.subheader(f"This week's reports — week {ew}, {ey}")
st.caption(f"Week of {current:%d %b} – {current + pd.Timedelta(days=6):%d %b %Y}, the newest week NCDC has published "
           "(reports come out a few weeks after the week they describe). Figures for all of Nigeria; "
           "the line shows the last 12 reported weeks.")

cards = []
for disease, group in weekly[weekly["disease_name"].isin(cw["disease_name"])].groupby("disease_name", sort=True):
    col, _ = ui.best_series(group)
    if col is None or col.startswith("cum_"):
        continue
    g = group.dropna(subset=[col]).sort_values("week_start")
    if g.empty or g["week_start"].iloc[-1] != current:
        continue
    now = g.iloc[-1]
    prev = g.iloc[-2] if len(g) > 1 and (now["week_start"] - g.iloc[-2]["week_start"]).days == 7 else None
    if prev is None:
        cls, chg = "flat", "no report the week before"
    else:
        d = int(now[col] - prev[col])
        cls = "up" if d > 0 else "down" if d < 0 else "flat"
        arrow = "▲" if d > 0 else "▼" if d < 0 else "■"
        chg = f"{arrow} {abs(d):,} vs previous week" if d else "■ same as previous week"
    colour = {"up": "#e5484d", "down": "#30a46c", "flat": "#8b8d98"}[cls]
    others = " · ".join(f"{ui.label(c)} {int(now[c]):,}" for c in ("suspected", "confirmed", "deaths")
                        if c != col and pd.notna(now.get(c)))
    cards.append(
        f'<div class="card"><div class="name">{disease}</div>'
        f'<div class="row"><div><div class="num">{int(now[col]):,}</div><div class="what">{ui.label(col).lower()}</div>'
        f'<div class="chg {cls}">{chg}</div></div>{sparkline(g[col].tail(12).astype(float).tolist(), colour)}</div>'
        f'<div class="more">{others}{" · may still change" if now["is_provisional"] else ""}</div></div>')

if cards:
    st.markdown(CARD_CSS + '<div class="cards">' + "".join(cards) + "</div>", unsafe_allow_html=True)
else:
    st.info("No weekly figures were published for this week.")
others = []
for disease, group in weekly.groupby("disease_name", sort=True):
    col, _ = ui.best_series(group)
    if col is None:
        continue
    g = group.dropna(subset=[col]).sort_values("week_start")
    if g.empty or g["week_start"].iloc[-1] == current:
        continue
    now = g.iloc[-1]
    ago = (current - now["week_start"]).days // 7
    when = f"week of {now['week_start']:%d %b %Y} · {ago} week{'s' if ago != 1 else ''} before this week"
    others.append(
        f'<div class="card muted"><div class="name">{disease}</div>'
        f'<div class="row"><div><div class="num">{int(now[col]):,}</div><div class="what">{ui.label(col).lower()}</div>'
        f'</div>{sparkline(g[col].tail(12).astype(float).tolist(), "#8b8d98")}</div>'
        f'<div class="more">{when}</div></div>')
if others:
    st.markdown("**Latest report for the other diseases**")
    st.markdown(CARD_CSS + '<div class="cards">' + "".join(others) + "</div>", unsafe_allow_html=True)

# ----------------------------------------------------------------------------- outbreak chance, selected disease
st.divider()
code, name = ui.disease_picker()
nat = weekly[weekly["disease_code"] == code]
chance, forecast, fc_measure = assessed[code] if code in assessed else assess(code)
# Risk readout: a three-step scale with the current level lit, then the evidence as plain figures.
tone = TONE[chance.level]
steps = "".join(
    f'<div class="rk-step{" on" if lvl == chance.level else ""}" style="--c:{TONE[lvl]}">{SHORT[lvl]}</div>'
    for lvl in (LOW_CHANCE, MODERATE_CHANCE, HIGH_CHANCE))
b = chance.base


def figure(value: str, label: str) -> str:
    return f'<div class="rk-fig"><div class="v">{value}</div><div class="l">{label}</div></div>'


figs = []
if chance.forecast_mean is not None:
    figs.append(figure(f"{chance.forecast_mean:.0f}", f"{fc_measure} cases a week<br>forecast, next 4 weeks"))
if chance.forecast_usual:
    figs.append(figure(f"{chance.forecast_usual:.0f}", "usual for this<br>time of year"))
if b.vs_usual_ratio is not None:
    figs.append(figure(f"{b.vs_usual_ratio:.1f}×", "this week compared<br>with usual"))
figs.append(figure(str(len(b.alerts_fired)), "alert checks fired<br>in the last 2 weeks"))
summary = chance.reasons[0][0].upper() + chance.reasons[0][1:] if chance.level == NO_DATA else {
    HIGH_CHANCE: "Cases are, or are forecast to be, well above the usual level for this time of year.",
    MODERATE_CHANCE: "Cases are above the usual level for this time of year; worth watching closely.",
    LOW_CHANCE: "Cases are within the usual range for this time of year.",
}[chance.level]

st.markdown(f"""<style>
.rk{{--tone:{tone};border:3px solid var(--tone);border-radius:12px;overflow:hidden;margin:4px 0 6px}}
.rk-top{{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:14px;
  padding:14px 18px;background:color-mix(in srgb,var(--tone) 14%,transparent)}}
.rk-kicker{{font-size:.72rem;letter-spacing:.12em;text-transform:uppercase;opacity:.7}}
.rk-level{{font-size:1.7rem;font-weight:750;color:var(--tone);line-height:1.15}}
.rk-scale{{display:flex;gap:4px}}
.rk-step{{min-width:78px;text-align:center;font-size:.75rem;font-weight:600;padding:5px 8px;border-radius:6px;
  border:1px solid rgba(128,128,128,.35);opacity:.55}}
.rk-step.on{{background:var(--c);border-color:var(--c);color:#fff;opacity:1}}
.rk-body{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:0}}
.rk-fig{{padding:12px 18px;border-right:1px solid rgba(128,128,128,.2)}}
.rk-fig:last-child{{border-right:none}}
.rk-fig .v{{font-size:1.45rem;font-weight:700;font-variant-numeric:tabular-nums}}
.rk-fig .l{{font-size:.74rem;opacity:.7;line-height:1.3}}
.rk-note{{padding:9px 18px;font-size:.85rem;border-top:1px solid rgba(128,128,128,.2)}}
</style>
<div class="rk" role="region" aria-label="Chance of an outbreak, {name}">
  <div class="rk-top">
    <div><div class="rk-kicker">Chance of an outbreak · {name}</div>
      <div class="rk-level">{SHORT[chance.level]}{" chance" if chance.level != NO_DATA else ""}</div></div>
    <div class="rk-scale" aria-hidden="true">{steps}</div>
  </div>
  <div class="rk-body">{"".join(figs)}</div>
  <div class="rk-note">{summary}</div>
</div>""", unsafe_allow_html=True)
st.caption(("" if forecast is not None else "No forecast for this disease yet (it needs about two years of regular "
            "weekly reports; see the Forecasts page), so this uses the alert checks and recent weeks only. ")
           + "A research indicator to show where to look first — not an official NCDC alert.")
with st.expander("How is this decided?"):
    st.markdown("""
| Level | Rule |
|---|---|
| **High chance** | Two or more alert checks fired in the last 2 weeks; **or** one fired and the next 4 weeks are forecast at 1.5 times the usual level for this time of year or more; **or** the forecast alone is at least double the usual level |
| **Moderate chance** | One alert check fired; **or** the forecast is at least 1.5 times the usual level; **or** this week is already 1.5 times the usual level |
| **Low chance** | None of the above |

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
