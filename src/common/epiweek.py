"""ISO 8601 epidemiological-week helpers.

NCDC reports use Monday-Sunday weeks numbered as in ISO 8601, so Python's
``date.isocalendar()`` is the reference implementation. ``week_id`` is
``epi_year * 100 + epi_week`` (e.g. 202405), matching ``curated.dim_epi_week``.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterator


def week_id(epi_year: int, epi_week: int) -> int:
    """Return the surrogate key for an ISO year/week pair."""
    if not 1 <= epi_week <= 53:
        raise ValueError(f"epi_week must be 1-53, got {epi_week}")
    return epi_year * 100 + epi_week


def split_week_id(wid: int) -> tuple[int, int]:
    """Inverse of :func:`week_id`."""
    return wid // 100, wid % 100


def weeks_in_year(epi_year: int) -> int:
    """52 or 53: Dec 28 always falls in the last ISO week of its year."""
    return date(epi_year, 12, 28).isocalendar()[1]


def is_valid_week(epi_year: int, epi_week: int) -> bool:
    return 1 <= epi_week <= weeks_in_year(epi_year)


def week_start(epi_year: int, epi_week: int) -> date:
    """Monday of the ISO week."""
    if not is_valid_week(epi_year, epi_week):
        raise ValueError(f"{epi_year}-W{epi_week:02d} does not exist")
    return date.fromisocalendar(epi_year, epi_week, 1)


def week_end(epi_year: int, epi_week: int) -> date:
    """Sunday of the ISO week."""
    return week_start(epi_year, epi_week) + timedelta(days=6)


def week_of(d: date) -> tuple[int, int]:
    """ISO (year, week) containing ``d``."""
    iso = d.isocalendar()
    return iso[0], iso[1]


def shift_week(epi_year: int, epi_week: int, weeks: int) -> tuple[int, int]:
    """Move ``weeks`` forward (or back, if negative) from a given week."""
    return week_of(week_start(epi_year, epi_week) + timedelta(weeks=weeks))


def previous_week(epi_year: int, epi_week: int) -> tuple[int, int]:
    return shift_week(epi_year, epi_week, -1)


def iter_weeks(start: tuple[int, int], end: tuple[int, int]) -> Iterator[tuple[int, int]]:
    """Yield every ISO week from ``start`` to ``end`` inclusive."""
    current = week_start(*start)
    stop = week_start(*end)
    while current <= stop:
        yield week_of(current)
        current += timedelta(weeks=1)
