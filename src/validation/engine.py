"""Run the validation rules on one report (Layer 4b).

Order of work for each report:

1. Rule 1 on the whole table (a failure quarantines the table).
2. Resolve place names to HDX p-codes; rule 9 for names that do not match.
3. Rules 8 and 10 (week validity, duplicates), then rule 2 on the raw counts.
4. Weekly values from cumulative totals (a negative result is a rule 2 error).
5. CFR, then rules 3 and 4.
6. Warning rules 5, 6, 7 and 11: rows are kept and flagged.

Error-level failures never stop the rest of the report from loading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Optional

import pandas as pd

from src.extraction.standardise import resolve_location
from src.extraction.transform import add_cfr, ensure_columns, weekly_from_cumulative
from src.validation import rules as R
from src.validation.rules import ERROR, Issue, PreviousLookup


@dataclass
class ValidationContext:
    alias_map: Mapping[str, str]
    denominators: Mapping[str, str]  # disease_code -> 'confirmed' | 'suspected'
    previous: Optional[PreviousLookup] = None
    cfr_tolerance_pp: float = 0.1
    timeliness_days: int = 14
    printed_start: Optional[date] = None
    printed_end: Optional[date] = None
    published_date: Optional[date] = None
    report_year: Optional[int] = None
    report_week: Optional[int] = None


@dataclass
class ValidationResult:
    passed: pd.DataFrame
    quarantined: list[dict[str, Any]] = field(default_factory=list)  # row payload + rule + reason
    flags: list[dict[str, Any]] = field(default_factory=list)
    table_error: Optional[Issue] = None

    @property
    def n_warned(self) -> int:
        if self.passed.empty or "quality_flags" not in self.passed.columns:
            return 0
        return int(self.passed["quality_flags"].map(bool).sum())


def _payload(row: pd.Series) -> dict[str, Any]:
    return {k: (None if (not isinstance(v, (list, str)) and pd.isna(v)) else v) for k, v in row.items()
            if k != "quality_flags"}


def validate_report(rows: pd.DataFrame, ctx: ValidationContext) -> ValidationResult:
    df = rows.copy()

    table_issues = R.rule_01_schema(df)
    if table_issues:
        return ValidationResult(passed=df.iloc[0:0], table_error=table_issues[0])

    df = ensure_columns(df)
    df["pcode"] = [resolve_location(v, ctx.alias_map) for v in df["location_raw"]]
    df["disease_code"] = df["disease_code"].astype(str).str.upper()
    unknown_disease = ~df["disease_code"].isin(list(ctx.denominators))

    quarantined: list[dict[str, Any]] = []
    dropped: set[Any] = set()

    def quarantine(issues: list[Issue]) -> None:
        for issue in issues:
            if issue.index is None or issue.index in dropped:
                continue
            dropped.add(issue.index)
            quarantined.append({"rule_id": issue.rule_id, "reason": issue.message,
                                "payload": _payload(df.loc[issue.index]), "detail": dict(issue.detail),
                                "source_page": _int_or_none(df.loc[issue.index].get("source_page"))})

    for idx in df.index[unknown_disease]:
        quarantine([Issue(1, f"unknown disease code {df.at[idx, 'disease_code']!r}", idx)])

    quarantine(R.rule_09_location(df.drop(index=list(dropped))))
    quarantine(R.rule_08_epi_week(df.drop(index=list(dropped)), ctx.printed_start, ctx.printed_end,
                                  ctx.report_year, ctx.report_week))
    quarantine(R.rule_10_duplicates(df.drop(index=list(dropped))))
    quarantine(R.rule_02_non_negative(df.drop(index=list(dropped))))

    live = df.drop(index=list(dropped)).copy()
    if live.empty:
        return ValidationResult(passed=live, quarantined=quarantined)
    for col in R.MEASURES:
        live[col] = live[col].map(lambda v: None if R._missing(v) else int(float(v))).astype("object")
    live["epi_year"] = live["epi_year"].astype(int)
    live["epi_week"] = live["epi_week"].astype(int)

    live, negative = weekly_from_cumulative(live, ctx.previous)
    quarantine([Issue(2, p["message"], p["index"], {"field": p["field"], "value": p["value"]}) for p in negative])
    live = live.drop(index=[i for i in live.index if i in dropped])

    live = add_cfr(live, ctx.denominators)
    quarantine(R.rule_03_confirmed_le_suspected(live))
    quarantine(R.rule_04_deaths_le_cases(live, ctx.denominators))
    live = live.drop(index=[i for i in live.index if i in dropped])

    warnings: list[Issue] = []
    warnings += R.rule_05_cfr_match(live, ctx.cfr_tolerance_pp)
    warnings += R.rule_06_cumulative_not_decreasing(live, ctx.previous)
    warnings += R.rule_07_states_sum_to_national(live)
    warnings += R.rule_11_timeliness(live, ctx.published_date, ctx.timeliness_days)

    live["quality_flags"] = [[] for _ in range(len(live))]
    flags: list[dict[str, Any]] = []
    for issue in warnings:
        row = live.loc[issue.index]
        if issue.code not in row["quality_flags"]:
            row["quality_flags"].append(issue.code)
        flags.append({"rule_id": issue.rule_id, "flag_type": issue.code, "disease_code": row["disease_code"],
                      "pcode": row["pcode"], "epi_year": int(row["epi_year"]), "epi_week": int(row["epi_week"]),
                      "detail": {"message": issue.message, **issue.detail}})
    assert all(R.RULES[i.rule_id][1] != ERROR for i in warnings)
    return ValidationResult(passed=live, quarantined=quarantined, flags=flags)


def _int_or_none(v: Any) -> Optional[int]:
    try:
        return None if v is None or pd.isna(v) else int(v)
    except (TypeError, ValueError):
        return None
