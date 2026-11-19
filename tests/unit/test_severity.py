from datetime import date

import pandas as pd

from src.analytics.severity import HIGH, LOW, MEDIUM, NO_DATA, choose_measure, classify

TODAY = date(2026, 11, 19)


def weekly(values, end="2026-11-09", measure="confirmed"):
    idx = pd.date_range(end=end, periods=len(values), freq="W-MON")
    return pd.DataFrame({"week_start": idx, measure: values, "suspected" if measure == "confirmed" else "confirmed": None})


def alerts(rows):
    return pd.DataFrame(rows, columns=["week_start", "method", "is_alert"])


def test_low_when_quiet():
    s = classify(weekly([10] * 60), alerts([]), today=TODAY)
    assert s.level == LOW and "No alert" in s.reason


def test_high_when_two_methods_fire():
    w = weekly([10] * 59 + [40])
    a = alerts([(w.week_start.iloc[-1], "EARS_C1", True), (w.week_start.iloc[-1], "EARS_C3", True)])
    s = classify(w, a, today=TODAY)
    assert s.level == HIGH and s.alerts_fired == ("EARS_C1", "EARS_C3")


def test_high_when_one_alert_and_sharp_rise():
    w = weekly([10] * 59 + [30])
    s = classify(w, alerts([(w.week_start.iloc[-1], "EARS_C1", True)]), today=TODAY)
    assert s.level == HIGH and s.change_vs_4wk_pct == 200


def test_medium_one_alert_without_rise():
    w = weekly([10] * 60)
    s = classify(w, alerts([(w.week_start.iloc[-2], "SARIMA_RESID_Z", True)]), today=TODAY)
    assert s.level == MEDIUM


def test_old_alerts_and_non_firing_rows_ignored():
    w = weekly([10] * 60)
    a = alerts([(w.week_start.iloc[-5], "EARS_C1", True), (w.week_start.iloc[-1], "EARS_C2", False)])
    assert classify(w, a, today=TODAY).level == LOW


def test_medium_when_far_above_usual_for_season():
    vals = [5] * 156
    vals[-1] = 20  # same week in 2 earlier years was 5
    s = classify(weekly(vals), alerts([]), today=TODAY)
    assert s.level == MEDIUM and s.vs_usual_ratio == 4


def test_no_recent_data_for_stale_or_empty_series():
    assert classify(weekly([5] * 20, end="2026-06-01"), alerts([]), today=TODAY).level == NO_DATA
    empty = pd.DataFrame({"week_start": pd.date_range(end="2026-11-09", periods=3, freq="W-MON"),
                          "confirmed": [None] * 3, "suspected": [None] * 3})
    assert classify(empty, alerts([]), today=TODAY).level == NO_DATA


def test_choose_measure():
    assert choose_measure(pd.DataFrame({"confirmed": [1, 2], "suspected": [100, 200]})) == "suspected"
    assert choose_measure(pd.DataFrame({"confirmed": [30, 20], "suspected": [100, 80]})) == "confirmed"
    assert choose_measure(pd.DataFrame({"confirmed": [None], "suspected": [None]})) is None


def test_outbreak_chance_uses_forecast():
    from src.analytics.severity import HIGH_CHANCE, LOW_CHANCE, MODERATE_CHANCE, outbreak_chance

    w = weekly([10] * 156)
    next_weeks = pd.date_range(start=w.week_start.iloc[-1] + pd.Timedelta(weeks=1), periods=4, freq="W-MON")
    calm = pd.DataFrame({"target_week": next_weeks, "predicted": [10] * 4})
    rising = pd.DataFrame({"target_week": next_weeks, "predicted": [16] * 4})
    surge = pd.DataFrame({"target_week": next_weeks, "predicted": [25] * 4})
    assert outbreak_chance(w, alerts([]), calm, today=TODAY).level == LOW_CHANCE
    assert outbreak_chance(w, alerts([]), rising, today=TODAY).level == MODERATE_CHANCE
    assert outbreak_chance(w, alerts([]), surge, today=TODAY).level == HIGH_CHANCE
    one = alerts([(w.week_start.iloc[-1], "EARS_C1", True)])
    assert outbreak_chance(w, one, rising, today=TODAY).level == HIGH_CHANCE
    assert outbreak_chance(w, one, None, today=TODAY).level == MODERATE_CHANCE
