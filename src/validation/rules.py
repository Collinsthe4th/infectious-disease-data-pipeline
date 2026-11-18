"""The 12 schema-enforcement and validation rules of Table 3.4.

Each rule is a small function over a DataFrame of one report's rows. Rules
return :class:`Issue` objects; the engine decides what to do with them:
error-level issues quarantine the row (or the table), warning-level issues
load the row and record a flag.

Rule ids and codes match ``ops.validation_rule`` (seeded by schema.sql).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Mapping, Optional

import pandas as pd

from src.common.epiweek import is_valid_week, week_end, week_id, week_start

ERROR, WARNING = "error", "warning"

RULES: dict[int, tuple[str, str]] = {
    1: ("R01_SCHEMA", ERROR),
    2: ("R02_NON_NEGATIVE", ERROR),
    3: ("R03_CONFIRMED_LE_SUSP", ERROR),
    4: ("R04_DEATHS_LE_CASES", ERROR),
    5: ("R05_CFR_MISMATCH", WARNING),
    6: ("R06_CUM_DECREASE", WARNING),
    7: ("R07_STATE_SUM", WARNING),
    8: ("R08_EPI_WEEK", ERROR),
    9: ("R09_LOCATION", ERROR),
    10: ("R10_DUPLICATE", ERROR),
    11: ("R11_TIMELINESS", WARNING),
    12: ("R12_COMPLETENESS", WARNING),  # checked across series after loading (repository)
}

COUNT_FIELDS = ("suspected", "confirmed", "deaths")
CUM_FIELDS = ("cum_suspected", "cum_confirmed", "cum_deaths")
MEASURES = COUNT_FIELDS + CUM_FIELDS
REQUIRED_COLUMNS = ("disease_code", "location_raw", "epi_year", "epi_week")


@dataclass(frozen=True)
class Issue:
    rule_id: int
    message: str
    index: Optional[Any] = None  # DataFrame index of the row; None = whole table
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def code(self) -> str:
        return RULES[self.rule_id][0]

    @property
    def severity(self) -> str:
        return RULES[self.rule_id][1]


def _missing(v: Any) -> bool:
    return v is None or (isinstance(v, float) and v != v) or v is pd.NA or (not isinstance(v, str) and pd.isna(v))


# --- Rule 1 -------------------------------------------------------------------
def rule_01_schema(df: pd.DataFrame) -> list[Issue]:
    """All required columns are present, with at least one count column."""
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        return [Issue(1, f"missing required columns: {missing}")]
    if not any(c in df.columns for c in MEASURES):
        return [Issue(1, "no case or death count columns found")]
    if df.empty:
        return [Issue(1, "table has no rows")]
    return []


# --- Rule 2 -------------------------------------------------------------------
def rule_02_non_negative(df: pd.DataFrame) -> list[Issue]:
    """Counts are whole numbers of zero or more (or missing)."""
    issues = []
    for idx, row in df.iterrows():
        for col in MEASURES:
            if col not in df.columns:
                continue
            v = row[col]
            if _missing(v):
                continue
            try:
                number = float(v) if not isinstance(v, bool) else float("nan")
            except (TypeError, ValueError):
                number = float("nan")
            if number != number or not number.is_integer():
                issues.append(Issue(2, f"{col} is not a whole number: {v!r}", idx, {"field": col, "value": str(v)}))
            elif number < 0:
                issues.append(Issue(2, f"{col} is negative: {int(number)}", idx, {"field": col, "value": int(number)}))
    return issues


# --- Rule 3 -------------------------------------------------------------------
def rule_03_confirmed_le_suspected(df: pd.DataFrame) -> list[Issue]:
    issues = []
    for idx, row in df.iterrows():
        for conf, susp in (("confirmed", "suspected"), ("cum_confirmed", "cum_suspected")):
            c, s = row.get(conf), row.get(susp)
            if not _missing(c) and not _missing(s) and int(c) > int(s):
                issues.append(Issue(3, f"{conf} ({c}) > {susp} ({s})", idx, {conf: int(c), susp: int(s)}))
    return issues


# --- Rule 4 -------------------------------------------------------------------
def rule_04_deaths_le_cases(df: pd.DataFrame, denominators: Mapping[str, str]) -> list[Issue]:
    """Deaths do not exceed the case count the report uses for its CFR."""
    issues = []
    for idx, row in df.iterrows():
        base = denominators.get(row["disease_code"], "confirmed")
        for deaths_col, prefix in (("deaths", ""), ("cum_deaths", "cum_")):
            d, n = row.get(deaths_col), row.get(prefix + base)
            if not _missing(d) and not _missing(n) and int(d) > int(n):
                issues.append(Issue(4, f"{deaths_col} ({d}) > {prefix + base} ({n})", idx,
                                    {deaths_col: int(d), prefix + base: int(n)}))
    return issues


# --- Rule 5 -------------------------------------------------------------------
def rule_05_cfr_match(df: pd.DataFrame, tolerance_pp: float) -> list[Issue]:
    issues = []
    if "cfr_reported" not in df.columns or "cfr_pct" not in df.columns:
        return issues
    for idx, row in df.iterrows():
        calc, printed = row.get("cfr_pct"), row.get("cfr_reported")
        if _missing(calc) or _missing(printed):
            continue
        if abs(float(calc) - float(printed)) > tolerance_pp + 1e-9:
            issues.append(Issue(5, f"calculated CFR {calc}% vs printed {printed}%", idx,
                                {"cfr_pct": float(calc), "cfr_reported": float(printed)}))
    return issues


# --- Rule 6 -------------------------------------------------------------------
PreviousLookup = Callable[[str, str, int], Optional[Mapping[str, Any]]]


def rule_06_cumulative_not_decreasing(df: pd.DataFrame, previous: PreviousLookup | None) -> list[Issue]:
    """A year-to-date total is not lower than the previous week's total."""
    issues = []
    ordered = df.sort_values(["disease_code", "pcode", "epi_year", "epi_week"])
    last: dict[tuple, dict] = {}
    for idx, row in ordered.iterrows():
        key = (row["disease_code"], row["pcode"], int(row["epi_year"]))
        wid = week_id(int(row["epi_year"]), int(row["epi_week"]))
        prior = last.get(key) or (previous(row["disease_code"], row["pcode"], wid) if previous else None)
        if prior:
            for col in CUM_FIELDS:
                now, before = row.get(col), prior.get(col)
                if not _missing(now) and not _missing(before) and int(now) < int(before):
                    issues.append(Issue(6, f"{col} fell from {before} to {now}", idx,
                                        {"field": col, "previous": int(before), "current": int(now),
                                         "previous_week_id": prior.get("week_id")}))
        last[key] = {c: row.get(c) for c in CUM_FIELDS} | {"week_id": wid}
    return issues


# --- Rule 7 -------------------------------------------------------------------
def rule_07_states_sum_to_national(df: pd.DataFrame, national_pcode: str = "NG") -> list[Issue]:
    issues = []
    for (disease, year, week), group in df.groupby(["disease_code", "epi_year", "epi_week"]):
        national = group[group["pcode"] == national_pcode]
        states = group[group["pcode"] != national_pcode]
        if national.empty or len(states) < 2:
            continue
        nat_idx = national.index[0]
        for col in MEASURES:
            if col not in group.columns:
                continue
            nat_val = national.iloc[0][col]
            state_vals = states[col]
            if _missing(nat_val) or state_vals.map(_missing).any():
                continue
            total = int(sum(int(v) for v in state_vals))
            if total != int(nat_val):
                issues.append(Issue(7, f"states sum to {total} but national {col} is {nat_val}", nat_idx,
                                    {"field": col, "state_sum": total, "national": int(nat_val)}))
    return issues


# --- Rule 8 -------------------------------------------------------------------
def rule_08_epi_week(df: pd.DataFrame, printed_start: date | None, printed_end: date | None,
                     report_year: int | None, report_week: int | None) -> list[Issue]:
    issues = []
    for idx, row in df.iterrows():
        try:
            year, week = int(row["epi_year"]), int(row["epi_week"])
        except (TypeError, ValueError):
            issues.append(Issue(8, f"epi year/week not numeric: {row['epi_year']!r}/{row['epi_week']!r}", idx))
            continue
        if not is_valid_week(year, week):
            issues.append(Issue(8, f"{year}-W{week:02d} does not exist", idx, {"epi_year": year, "epi_week": week}))
            continue
        if printed_start and (year, week) == (report_year, report_week):
            iso_start, iso_end = week_start(year, week), week_end(year, week)
            if printed_start != iso_start or (printed_end and printed_end != iso_end):
                issues.append(Issue(8, f"printed dates {printed_start}–{printed_end} do not match "
                                       f"ISO {year}-W{week:02d} ({iso_start}–{iso_end})", idx))
    return issues


# --- Rule 9 -------------------------------------------------------------------
def rule_09_location(df: pd.DataFrame) -> list[Issue]:
    """Expects ``pcode`` already resolved by the engine; ``None`` = unmatched."""
    return [
        Issue(9, f"location {row['location_raw']!r} not in the HDX reference list", idx,
              {"location_raw": str(row["location_raw"])})
        for idx, row in df.iterrows()
        if _missing(row.get("pcode"))
    ]


# --- Rule 10 ------------------------------------------------------------------
def rule_10_duplicates(df: pd.DataFrame) -> list[Issue]:
    """Only one row per disease, location and week: keep the first."""
    dup = df.duplicated(subset=["disease_code", "pcode", "epi_year", "epi_week"], keep="first")
    return [Issue(10, "duplicate disease/location/week row in the same report", idx) for idx in df.index[dup]]


# --- Rule 11 ------------------------------------------------------------------
def rule_11_timeliness(df: pd.DataFrame, published: date | None, max_days: int) -> list[Issue]:
    if published is None:
        return []
    issues = []
    for idx, row in df.iterrows():
        try:
            end = week_end(int(row["epi_year"]), int(row["epi_week"]))
        except (TypeError, ValueError):
            continue
        lag = (published - end).days
        if lag > max_days:
            issues.append(Issue(11, f"published {lag} days after the end of the week", idx, {"lag_days": lag}))
    return issues
