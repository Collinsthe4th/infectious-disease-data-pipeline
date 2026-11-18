"""Transformation functions (Section 3.5.3). Each does one job and is unit-tested."""

from __future__ import annotations

from typing import Callable, Mapping, Optional

import numpy as np
import pandas as pd

from src.common.epiweek import previous_week, week_id

COUNT_FIELDS = ("suspected", "confirmed", "deaths")
CUM_FIELDS = ("cum_suspected", "cum_confirmed", "cum_deaths")
ALL_MEASURES = COUNT_FIELDS + CUM_FIELDS

PreviousLookup = Callable[[str, str, int], Optional[Mapping[str, Optional[int]]]]


def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add any missing measure columns as nullable integers."""
    out = df.copy()
    for col in ALL_MEASURES:
        if col not in out.columns:
            out[col] = pd.NA
    for col in ("cfr_reported",):
        if col not in out.columns:
            out[col] = np.nan
    if "is_derived_from_cumulative" not in out.columns:
        out["is_derived_from_cumulative"] = False
    return out


def weekly_from_cumulative(df: pd.DataFrame, previous: PreviousLookup | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """Fill missing weekly values as ``cum_t - cum_{t-1}``.

    ``cum_{t-1}`` comes from an earlier row in the same batch, else from the
    database via ``previous(disease_code, pcode, week_id)``. In week 1 the
    weekly value equals the year-to-date total. A negative difference is not
    allowed: the row is returned in ``problems`` for quarantine as a likely
    revision (Section 3.4.4, step 6).
    """
    out = ensure_columns(df).sort_values(["disease_code", "pcode", "epi_year", "epi_week"]).copy()
    problems: list[dict] = []
    last_seen: dict[tuple[str, str, int], dict] = {}
    for idx, row in out.iterrows():
        key = (row["disease_code"], row["pcode"], int(row["epi_year"]))
        prior = last_seen.get(key)
        if prior is None and previous is not None:
            prior = previous(row["disease_code"], row["pcode"], week_id(int(row["epi_year"]), int(row["epi_week"])))
        derived = False
        expected_prev = week_id(*previous_week(int(row["epi_year"]), int(row["epi_week"])))
        for weekly, cum in zip(COUNT_FIELDS, CUM_FIELDS):
            if not pd.isna(row[weekly]) or pd.isna(row[cum]):
                continue
            if int(row["epi_week"]) == 1:
                value = int(row[cum])
            elif prior is not None and not pd.isna(prior.get(cum)) and prior.get("week_id") == expected_prev:
                value = int(row[cum]) - int(prior[cum])
            else:
                continue  # no total for the week before: leave the weekly value missing
            if value < 0:
                problems.append({"index": idx, "field": weekly, "value": value,
                                 "message": f"{cum} fell from {prior[cum]} to {row[cum]}; weekly {weekly} would be {value}"})
                continue
            out.at[idx, weekly] = value
            derived = True
        if derived:
            out.at[idx, "is_derived_from_cumulative"] = True
        last_seen[key] = {c: row[c] for c in CUM_FIELDS} | {
            "week_id": week_id(int(row["epi_year"]), int(row["epi_week"]))
        }
    return out, problems


def case_fatality_rate(deaths: object, denominator: object) -> Optional[float]:
    """CFR (%) = deaths / denominator x 100, rounded to 2 dp; ``None`` if undefined."""
    if deaths is None or denominator is None or pd.isna(deaths) or pd.isna(denominator):
        return None
    if int(denominator) == 0:
        return None
    return round(100.0 * int(deaths) / int(denominator), 2)


def add_cfr(df: pd.DataFrame, denominators: Mapping[str, str]) -> pd.DataFrame:
    """Compute ``cfr_pct`` per row using each disease's denominator field."""
    out = df.copy()
    out["cfr_pct"] = [
        case_fatality_rate(row["deaths"], row["confirmed" if denominators.get(row["disease_code"], "confirmed") == "confirmed" else "suspected"])
        for _, row in out.iterrows()
    ]
    return out


def complete_weekly_index(series: pd.Series, week_starts: pd.DatetimeIndex | None = None) -> pd.Series:
    """Re-index a weekly series (indexed by week_start) to a full calendar.

    Missing weeks appear as NaN instead of disappearing (Section 3.5.3).
    """
    if series.empty:
        return series
    series = series.sort_index()
    full = week_starts if week_starts is not None else pd.date_range(series.index.min(), series.index.max(), freq="W-MON")
    return series.reindex(full)


def interpolate_short_gaps(series: pd.Series, max_gap: int = 2) -> tuple[pd.Series, pd.Series]:
    """Linearly fill gaps of at most ``max_gap`` weeks (for models only).

    Returns the filled series and a boolean mask of filled positions so they
    are never displayed as real data.
    """
    missing = series.isna()
    run_id = (missing != missing.shift()).cumsum()
    run_len = missing.groupby(run_id).transform("sum")
    fillable = missing & (run_len <= max_gap)
    interpolated = series.interpolate(method="linear", limit_area="inside")
    filled = series.where(~fillable, interpolated)
    return filled, fillable & filled.notna()
