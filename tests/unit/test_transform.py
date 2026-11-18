import pandas as pd

from src.extraction.transform import case_fatality_rate, interpolate_short_gaps, weekly_from_cumulative


def _rows(*items):
    return pd.DataFrame([dict(disease_code="LASSA", pcode="NG", epi_year=y, epi_week=w, cum_confirmed=c,
                              confirmed=None) for y, w, c in items])


def test_weekly_from_cumulative_in_batch_and_week_one():
    out, problems = weekly_from_cumulative(_rows((2024, 1, 5), (2024, 2, 9), (2024, 3, 12)))
    assert list(out["confirmed"]) == [5, 4, 3]
    assert out["is_derived_from_cumulative"].all() and not problems


def test_gap_leaves_weekly_missing():
    out, _ = weekly_from_cumulative(_rows((2024, 2, 9), (2024, 4, 15)))
    assert out["confirmed"].isna().all()


def test_previous_week_from_database():
    lookup = lambda d, p, w: {"cum_confirmed": 20, "cum_suspected": None, "cum_deaths": None, "week_id": 202409}  # noqa: E731
    out, _ = weekly_from_cumulative(_rows((2024, 10, 26)), lookup)
    assert out["confirmed"].iloc[0] == 6


def test_falling_cumulative_is_reported_not_loaded():
    out, problems = weekly_from_cumulative(_rows((2024, 2, 9), (2024, 3, 7)))
    assert pd.isna(out.iloc[1]["confirmed"])
    assert problems and problems[0]["value"] == -2


def test_cfr():
    assert case_fatality_rate(4, 13) == 30.77
    assert case_fatality_rate(0, 0) is None
    assert case_fatality_rate(None, 10) is None


def test_interpolation_only_short_gaps_and_marked():
    idx = pd.date_range("2024-01-01", periods=8, freq="W-MON")
    s = pd.Series([1, None, 3, None, None, None, 7, 8], index=idx, dtype=float)
    filled, mask = interpolate_short_gaps(s, max_gap=2)
    assert filled.iloc[1] == 2 and mask.iloc[1]
    assert filled.iloc[3:6].isna().all() and not mask.iloc[3:6].any()
