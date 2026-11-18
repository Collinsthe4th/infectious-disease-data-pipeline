"""Table readers used by the layout templates.

All readers return plain dicts keyed by the canonical variable names of
Table 3.3 so the rest of the pipeline never sees raw PDF headers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Optional

from src.common.diseases import detect_disease
from src.extraction.pdf_reader import PdfContent, Table
from src.extraction.standardise import (
    canonical_header,
    clean_count,
    clean_percent,
    header_period,
    is_total_label,
    resolve_location,
)

COUNT_FIELDS = ("suspected", "confirmed", "deaths")
CUM_FIELD = {"suspected": "cum_suspected", "confirmed": "cum_confirmed", "deaths": "cum_deaths"}

CURRENT_WEEK_RE = re.compile(r"^\s*current\s*week", re.I)
CUMULATIVE_RE = re.compile(r"cumulative", re.I)
YEAR_RE = re.compile(r"(20\d\d)")
NUMBER_TOKEN_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?%?|(?<!\w)[-–—](?!\w))")


@dataclass
class ParsedBlock:
    """Values read from one part of a report."""

    rows: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    page: Optional[int] = None


# --------------------------------------------------------------------------- #
#  National summary ("Current week" and "<year> Cumulative" rows)
# --------------------------------------------------------------------------- #

def _header_fields(header: list[str]) -> list[Optional[str]]:
    return [canonical_header(h) for h in header]


def _summary_from_tables(content: PdfContent, epi_year: int, dash_is_zero: bool) -> ParsedBlock | None:
    for page, table in content.tables:
        header_idx = next(
            (i for i, row in enumerate(table[:3])
             if sum(1 for f in _header_fields(row) if f in COUNT_FIELDS) >= 2),
            None,
        )
        if header_idx is None:
            continue
        fields = _header_fields(table[header_idx])
        weekly = cumulative = None
        for row in table[header_idx + 1:]:
            label = row[0] if row else ""
            if CURRENT_WEEK_RE.search(label):
                weekly = row
            elif CUMULATIVE_RE.search(label):
                year = YEAR_RE.search(label)
                if year is None or int(year.group(1)) == epi_year:
                    cumulative = cumulative or row
        if weekly is None and cumulative is None:
            continue
        values, errors = _read_summary_rows(fields, weekly, cumulative, dash_is_zero)
        return ParsedBlock(rows=[values], errors=errors, page=page)
    return None


def _read_summary_rows(fields, weekly, cumulative, dash_is_zero) -> tuple[dict, list[str]]:
    values: dict = {}
    errors: list[str] = []
    for target, row in (("weekly", weekly), ("cumulative", cumulative)):
        if row is None:
            continue
        for name, cell in zip(fields, row):
            if name in COUNT_FIELDS:
                key = name if target == "weekly" else CUM_FIELD[name]
                try:
                    values[key] = clean_count(cell, dash_is_zero=dash_is_zero)
                except ValueError as exc:
                    errors.append(f"{key}: {exc}")
                    values[key] = cell  # left raw so rule 1/2 can quarantine it
            elif name == "cfr" and target == "weekly":
                values["cfr_reported"] = clean_percent(cell)
            elif name == "cfr" and target == "cumulative":
                values["cum_cfr_reported"] = clean_percent(cell)
    return values, errors


def _summary_from_text(content: PdfContent, epi_year: int, dash_is_zero: bool) -> ParsedBlock | None:
    """Fallback when the summary table has no ruling lines: read text lines."""
    for page_no, text in enumerate(content.page_texts, start=1):
        lines = [ln.strip() for ln in text.splitlines()]
        weekly_idx = next((i for i, ln in enumerate(lines) if CURRENT_WEEK_RE.search(ln)), None)
        if weekly_idx is None:
            continue
        window = " ".join(lines[max(0, weekly_idx - 8):weekly_idx]).lower()
        order = _field_order(window)
        if len([f for f in order if f in COUNT_FIELDS]) < 2:
            order = ["suspected", "confirmed", "probable", "deaths", "cfr"]
        weekly = _numbers_after_label(lines[weekly_idx])
        cumulative = None
        for ln in lines[weekly_idx + 1:weekly_idx + 6]:
            if CUMULATIVE_RE.search(ln):
                year = YEAR_RE.search(ln.split("cumulative")[0] if "cumulative" in ln.lower() else ln)
                if year is None or int(year.group(1)) == epi_year:
                    cumulative = _numbers_after_label(ln)
                    break
        values, errors = _read_summary_rows(order, weekly, cumulative, dash_is_zero)
        if any(k in values for k in (*COUNT_FIELDS, *CUM_FIELD.values())):
            return ParsedBlock(rows=[values], errors=errors, page=page_no)
    return None


def _field_order(window: str) -> list[str]:
    hits = []
    for name, pattern in (("suspected", r"suspect"), ("confirmed", r"confirmed\s+cases?(?!\))"),
                          ("probable", r"probable"), ("deaths", r"death"), ("cfr", r"fatality|\bcfr\b")):
        m = re.search(pattern, window)
        if m:
            hits.append((m.start(), name))
    return [name for _, name in sorted(hits)]


def _numbers_after_label(line: str) -> list[str]:
    body = re.sub(r"^[^:)]*(?:\(week\s*\d+\))?\s*:?", "", line, flags=re.I)
    tokens = NUMBER_TOKEN_RE.findall(body)
    return [t for t in tokens if t]


def read_summary(content: PdfContent, epi_year: int, *, dash_is_zero: bool = False) -> ParsedBlock | None:
    return _summary_from_tables(content, epi_year, dash_is_zero) or _summary_from_text(content, epi_year, dash_is_zero)


# --------------------------------------------------------------------------- #
#  State table
# --------------------------------------------------------------------------- #

def _column_labels(table: Table, n_header: int) -> list[str]:
    width = max(len(r) for r in table)
    labels = [""] * width
    for row in table[:n_header]:
        carry = ""
        for i in range(width):
            cell = row[i] if i < len(row) else ""
            if cell:
                carry = cell
            # forward-fill merged top-level headers ("Week 44" spanning several cells)
            labels[i] = f"{labels[i]} {cell or (carry if row is table[0] else '')}".strip()
    return labels


def read_state_tables(
    tables: list[tuple[int, Table]],
    alias_map: Mapping[str, str],
    *,
    default_period: str = "weekly",
    dash_is_zero: bool = False,
) -> ParsedBlock | None:
    """Read the first table that lists at least two recognised states."""
    for page, table in tables:
        if len(table) < 3:
            continue
        first_data = next(
            (i for i, row in enumerate(table)
             if row and (resolve_location(row[0], alias_map) or (len(row) > 1 and resolve_location(row[1], alias_map)))),
            None,
        )
        if first_data is None or first_data == 0:
            continue
        loc_col = 0 if resolve_location(table[first_data][0], alias_map) else 1
        labels = _column_labels(table, first_data)
        mapping: dict[int, str] = {}
        for idx, label in enumerate(labels):
            if idx == loc_col:
                continue
            name = canonical_header(label)
            if name not in COUNT_FIELDS:
                continue
            period = header_period(label)
            if period == "unknown":
                period = default_period
            key = name if period == "weekly" else CUM_FIELD[name]
            if key not in mapping.values():
                mapping[idx] = key
        if not mapping:
            continue
        block = ParsedBlock(page=page)
        recognised = 0
        for row in table[first_data:]:
            if not row or len(row) <= loc_col:
                continue
            place = row[loc_col]
            if not place:
                continue
            values: dict = {"location_raw": place, "is_total_row": is_total_label(place)}
            for idx, key in mapping.items():
                cell = row[idx] if idx < len(row) else ""
                try:
                    values[key] = clean_count(cell, dash_is_zero=dash_is_zero)
                except ValueError as exc:
                    block.errors.append(f"{place}/{key}: {exc}")
                    values[key] = cell
            if resolve_location(place, alias_map):
                recognised += 1
            block.rows.append(values)
        if recognised >= 2:
            return block
    return None


# --------------------------------------------------------------------------- #
#  Weekly Epidemiological Report: one row per disease
# --------------------------------------------------------------------------- #

def read_disease_rows(content: PdfContent, *, dash_is_zero: bool = False) -> ParsedBlock | None:
    for page, table in content.tables:
        header_idx = next(
            (i for i, row in enumerate(table[:3])
             if sum(1 for f in _header_fields(row) if f in COUNT_FIELDS) >= 2),
            None,
        )
        if header_idx is None:
            continue
        labels = _column_labels(table, header_idx + 1)
        mapping = {}
        for idx, label in enumerate(labels):
            name = canonical_header(label)
            if name in COUNT_FIELDS:
                key = name if header_period(label) != "cumulative" else CUM_FIELD[name]
                mapping.setdefault(key, idx)
        block = ParsedBlock(page=page)
        for row in table[header_idx + 1:]:
            disease = detect_disease(row[0] if row else "")
            if not disease:
                continue
            values: dict = {"disease_code": disease.code, "location_raw": "National"}
            for key, idx in mapping.items():
                try:
                    values[key] = clean_count(row[idx] if idx < len(row) else "", dash_is_zero=dash_is_zero)
                except ValueError as exc:
                    block.errors.append(f"{disease.code}/{key}: {exc}")
                    values[key] = row[idx]
            block.rows.append(values)
        if block.rows:
            return block
    return None
