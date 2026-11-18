from datetime import date

import pytest

from src.common import epiweek as ew


def test_week_id_round_trip():
    assert ew.week_id(2024, 5) == 202405
    assert ew.split_week_id(202405) == (2024, 5)


@pytest.mark.parametrize("year,weeks", [(2020, 53), (2021, 52), (2025, 52), (2026, 53)])
def test_weeks_in_year(year, weeks):
    assert ew.weeks_in_year(year) == weeks


def test_week_start_is_monday_and_crosses_years():
    assert ew.week_start(2026, 1) == date(2025, 12, 29)
    assert ew.week_end(2026, 44) == date(2026, 11, 1)
    assert ew.week_start(2026, 44).weekday() == 0


def test_invalid_week_raises():
    assert not ew.is_valid_week(2021, 53)
    with pytest.raises(ValueError):
        ew.week_start(2021, 53)


def test_previous_and_iter_weeks():
    assert ew.previous_week(2021, 1) == (2020, 53)
    weeks = list(ew.iter_weeks((2020, 52), (2021, 2)))
    assert weeks == [(2020, 52), (2020, 53), (2021, 1), (2021, 2)]
