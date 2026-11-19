"""Outbreak severity scorecard: High / Medium / Low per disease.

Built on the alert methods of Section 3.5.4 (EARS C1-C3 and the SARIMA
residual z-score) plus two plain comparisons, so every level can be
explained in one sentence:

HIGH    two or more different alert checks fired in the last 2 reported weeks,
        or one fired while cases rose >= 50% against the previous 4-week
        average (with at least 10 cases this week)
MEDIUM  one alert check fired in the last 2 reported weeks,
        or this week's cases are >= 1.5 x the usual level for this time of
        year (same weeks in earlier years), with at least 5 cases
LOW     none of the above
NO DATA the latest weekly figure is more than ``stale_weeks`` old

This is a research classification to prioritise attention, not an official
NCDC alert level.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

HIGH, MEDIUM, LOW, NO_DATA = "High", "Medium", "Low", "No recent weekly data"

METHOD_LABELS = {
    "EARS_C1": "sudden jump vs the last 7 weeks",
    "EARS_C2": "jump vs weeks 3-9 before",
    "EARS_C3": "steady build-up over 3 weeks",
    "SARIMA_RESID_Z": "higher than the seasonal forecast",
}


@dataclass(frozen=True)
class Severity:
    level: str
    reason: str
    latest_week: date | None
    latest_cases: float | None
    measure: str
    change_vs_4wk_pct: float | None
    vs_usual_ratio: float | None
    alerts_fired: tuple[str, ...]


def choose_measure(frame: pd.DataFrame) -> str | None:
    """'confirmed' when the disease reports confirmed cases in volume, else 'suspected'.

    Cholera, measles, CSM and diphtheria are mostly counted as suspected cases;
    Lassa fever and mpox by laboratory-confirmed cases. Returns None if neither
    has any weekly value.
    """
    conf = pd.to_numeric(frame.get("confirmed"), errors="coerce")
    susp = pd.to_numeric(frame.get("suspected"), errors="coerce")
    has_conf = conf.notna().sum() if conf is not None else 0
    has_susp = susp.notna().sum() if susp is not None else 0
    if has_conf == 0 and has_susp == 0:
        return None
    if has_conf and (not has_susp or conf.sum() >= 0.25 * susp.sum()):
        return "confirmed"
    return "suspected"


def classify(
    weekly: pd.DataFrame,
    alerts: pd.DataFrame,
    *,
    today: date | None = None,
    stale_weeks: int = 6,
) -> Severity:
    """Classify one disease from its national weekly series and its alert rows.

    ``weekly``: columns week_start, suspected, confirmed (one row per week).
    ``alerts``: columns week_start, method, is_alert (national rows for this disease).
    """
    today = today or date.today()
    measure = choose_measure(weekly) if not weekly.empty else None
    if measure is None:
        return Severity(NO_DATA, "No weekly case counts have been published for this disease.",
                        None, None, "none", None, None, ())

    s = (weekly.assign(week_start=pd.to_datetime(weekly["week_start"]))
         .set_index("week_start")[measure].astype(float).dropna().sort_index())
    if s.empty:
        return Severity(NO_DATA, "No weekly case counts have been published for this disease.",
                        None, None, measure, None, None, ())
    latest_week = s.index[-1].date()
    latest = float(s.iloc[-1])
    if latest_week < today - timedelta(weeks=stale_weeks):
        return Severity(NO_DATA, f"The latest weekly figure is from {latest_week:%d %b %Y}; "
                                 "recent reports give monthly or year-to-date totals only.",
                        latest_week, latest, measure, None, None, ())

    prev4 = s[(s.index < s.index[-1]) & (s.index >= s.index[-1] - pd.Timedelta(weeks=4))]
    change = None
    if len(prev4) >= 2 and prev4.mean() > 0:
        change = 100.0 * (latest / prev4.mean() - 1.0)

    woy = s.index.isocalendar().week.astype(int)
    this_woy = int(woy.iloc[-1])
    near = ((woy - this_woy + 26) % 52 - 26).abs() <= 1
    earlier = s[near & (s.index.year < s.index[-1].year)]
    usual = float(earlier.mean()) if len(earlier) >= 3 else None
    vs_usual = (latest / usual) if usual and usual > 0 else None

    recent_weeks = set(s.index[-2:])
    fired: tuple[str, ...] = ()
    if not alerts.empty:
        a = alerts.assign(week_start=pd.to_datetime(alerts["week_start"]))
        a = a[a["week_start"].isin(recent_weeks) & a["is_alert"].astype(bool)]
        fired = tuple(sorted(a["method"].unique()))

    rising = change is not None and change >= 50 and latest >= 10
    above_usual = vs_usual is not None and vs_usual >= 1.5 and latest >= 5
    labels = [METHOD_LABELS.get(m, m) for m in fired]

    if len(fired) >= 2:
        level, reason = HIGH, f"{len(fired)} alert checks fired: " + "; ".join(labels) + "."
    elif len(fired) == 1 and rising:
        level, reason = HIGH, f"Alert check fired ({labels[0]}) and cases rose {change:.0f}% on the previous 4 weeks."
    elif len(fired) == 1:
        level, reason = MEDIUM, f"One alert check fired: {labels[0]}."
    elif above_usual:
        level, reason = MEDIUM, f"Cases are {vs_usual:.1f} times the usual level for this time of year."
    else:
        detail = []
        if change is not None:
            detail.append(f"{'up' if change >= 0 else 'down'} {abs(change):.0f}% on the previous 4 weeks")
        if vs_usual is not None:
            detail.append(f"{vs_usual:.1f} times the usual level for this time of year")
        level, reason = LOW, "No alert checks fired" + (f"; cases {', '.join(detail)}." if detail else ".")
    return Severity(level, reason, latest_week, latest, measure,
                    None if change is None or not np.isfinite(change) else change, vs_usual, fired)


# --------------------------------------------------------------------------- #
#  "Chance of an outbreak" badge for one selected disease (alerts + forecast)
# --------------------------------------------------------------------------- #

HIGH_CHANCE, MODERATE_CHANCE, LOW_CHANCE = "High chance", "Moderate chance", "Low chance"


@dataclass(frozen=True)
class OutbreakChance:
    level: str
    reasons: tuple[str, ...]
    forecast_vs_usual: float | None
    base: Severity
    forecast_mean: float | None = None  # average forecast per week (forecast measure)
    forecast_usual: float | None = None  # usual level for those weeks in earlier years


def usual_level(series: pd.Series, weeks: pd.DatetimeIndex) -> float | None:
    """Average of the same weeks of the year (±1 week) in earlier years."""
    if series.empty:
        return None
    woy = series.index.isocalendar().week.astype(int)
    vals = []
    for w in weeks:
        target = int(w.isocalendar()[1])
        near = ((woy - target + 26) % 52 - 26).abs() <= 1
        earlier = series[near & (series.index.year < w.year)]
        if len(earlier) >= 3:
            vals.append(float(earlier.mean()))
    return float(np.mean(vals)) if vals else None


def outbreak_chance(weekly: pd.DataFrame, alerts: pd.DataFrame, forecast: pd.DataFrame | None = None,
                    *, today: date | None = None, forecast_measure: str = "confirmed") -> OutbreakChance:
    """Combine this week's alert checks with the 4-week forecast.

    HIGH     2+ alert checks fired in the last 2 weeks; or 1 fired and the next
             4 weeks are forecast at >= 1.5 x the usual level for the season;
             or the forecast alone is >= 2 x usual
    MODERATE 1 alert check fired; or the forecast is >= 1.5 x usual; or this
             week is already >= 1.5 x usual
    LOW      none of the above
    ``forecast``: columns target_week, predicted (one model, next weeks), predicting
    ``forecast_measure`` (Lassa forecasts are of confirmed cases). The usual level is
    always taken from that same measure, so like is compared with like.
    """
    base = classify(weekly, alerts, today=today)
    if base.level == NO_DATA:
        return OutbreakChance(NO_DATA, (base.reason,), None, base, None, None)

    ratio = forecast_mean = forecast_usual = None
    if forecast is not None and not forecast.empty and forecast_measure in weekly:
        s = (weekly.assign(week_start=pd.to_datetime(weekly["week_start"])).set_index("week_start")[forecast_measure]
             .astype(float).dropna().sort_index())
        targets = pd.DatetimeIndex(pd.to_datetime(forecast["target_week"]))
        usual = usual_level(s, targets)
        forecast_mean = float(forecast["predicted"].astype(float).mean())
        if usual and usual > 0:
            forecast_usual = usual
            ratio = forecast_mean / usual

    n_alerts = len(base.alerts_fired)
    reasons: list[str] = []
    if n_alerts:
        reasons.append(f"{n_alerts} alert check{'s' if n_alerts > 1 else ''} fired in the last 2 weeks ("
                       + "; ".join(METHOD_LABELS.get(m, m) for m in base.alerts_fired) + ")")
    if ratio is not None:
        reasons.append(f"forecast for the next 4 weeks: about {forecast_mean:.0f} {forecast_measure} cases a week, "
                       f"{ratio:.1f} times the usual {forecast_usual:.0f} for this time of year")
    if base.vs_usual_ratio is not None and base.vs_usual_ratio >= 1.5:
        reasons.append(f"this week is {base.vs_usual_ratio:.1f} times the usual level")
    if base.change_vs_4wk_pct is not None and abs(base.change_vs_4wk_pct) >= 25:
        reasons.append(f"cases are {'up' if base.change_vs_4wk_pct > 0 else 'down'} "
                       f"{abs(base.change_vs_4wk_pct):.0f}% on the previous 4 weeks")

    fc_high = ratio is not None and ratio >= 1.5
    if n_alerts >= 2 or (n_alerts == 1 and fc_high) or (ratio is not None and ratio >= 2):
        level = HIGH_CHANCE
    elif n_alerts == 1 or fc_high or (base.vs_usual_ratio is not None and base.vs_usual_ratio >= 1.5 and (base.latest_cases or 0) >= 5):
        level = MODERATE_CHANCE
    else:
        level = LOW_CHANCE
    if not reasons:
        reasons.append("no alert checks fired and cases are within the usual range")
    return OutbreakChance(level, tuple(reasons), ratio, base, forecast_mean, forecast_usual)
