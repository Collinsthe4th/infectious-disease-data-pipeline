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

CURRENT_WEEK_RE = re.compile(r"^\s*(?:current\s*(?:week|epi)|epi[\s\-]*week\s*\d+|week\s*\d+|reporting\s*week|this\s*week)", re.I)
CUMULATIVE_RE = re.compile(r"cumulative|^\s*jan(?:uary)?\.?\s*(?:to|-|–)\s*[a-z]+", re.I)
YEAR_RE = re.compile(r"(20\d\d)")
NUMBER_TOKEN_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?%?|(?<!\w)[-–—](?!\w))")
SEASON_RE = re.compile(r"season|\d{4}\s*/\s*\d{2,4}|since|re-?emergence|outbreak\s*start", re.I)


def cumulative_is_calendar(label: str, epi_year: int) -> bool:
    """True if a cumulative label means 'this calendar year so far'.

    Season totals (CSM: "Epi week 40 - 14 of 2024/2025") and outbreak totals
    (diphtheria: "Epi-Week 19, 2022 - Epi-Week 4, 2025") are NOT year-to-date
    totals; storing them as such would corrupt weekly values and rule 6.
    """
    if SEASON_RE.search(label):
        return False
    years = {int(y) for y in YEAR_RE.findall(label)}
    return not years or years == {epi_year}


@dataclass
class ParsedBlock:
    """Values read from one part of a report."""

    rows: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    page: Optional[int] = None
    method: str = ""


# --------------------------------------------------------------------------- #
#  National summary
# --------------------------------------------------------------------------- #

def _header_fields(header: list[str]) -> list[Optional[str]]:
    return [canonical_header(h) for h in header]


def _merged_header(table: Table, upto: int) -> list[Optional[str]]:
    """Field names from header rows 0..upto (headers often wrap over two rows)."""
    width = max(len(r) for r in table[: upto + 1])
    labels = [" ".join((r[i] if i < len(r) else "") for r in table[: upto + 1]).strip() for i in range(width)]
    return [canonical_header(lbl) if lbl else None for lbl in labels]


def _is_number(cell: str) -> bool:
    return bool(NUMBER_TOKEN_RE.fullmatch(cell.replace(" ", "")))


def _row_label(row: list[str]) -> str:
    return " ".join(c for c in row[:2] if c and not _is_number(c)).strip()


def _is_numeric_row(row: list[str]) -> bool:
    cells = [c for c in row if c]
    return bool(cells) and all(_is_number(c) or c.lower().startswith(("state", "lga")) for c in cells[:4])


def _summary_from_tables(content: PdfContent, epi_year: int, dash_is_zero: bool, monthly: bool = False) -> ParsedBlock | None:
    """Summary tables; weekly and cumulative rows may sit in separate tables (cholera)."""
    merged = ParsedBlock(method="summary_table")
    for page, table in content.tables:
        header_idx = next(
            (i for i in range(min(3, len(table)))
             if sum(1 for f in _merged_header(table, i) if f in COUNT_FIELDS) >= 2),
            None,
        )
        if header_idx is None:
            continue
        fields = _merged_header(table, header_idx)
        weekly = cumulative = None
        notes: list[str] = []
        unlabeled: list[list[str]] = []
        for row in table[header_idx + 1:]:
            label = _row_label(row)
            if not label and _is_numeric_row(row):
                unlabeled.append(row)
                continue
            if CUMULATIVE_RE.search(label):
                if cumulative_is_calendar(label, epi_year):
                    cumulative = cumulative or row
                else:
                    notes.append(f"ignored non-calendar cumulative row {label!r}")
            elif not monthly and CURRENT_WEEK_RE.search(label):
                weekly = weekly or row
        if weekly is None and cumulative is None and len(unlabeled) == 1:
            period = _period_from_context(content.page_texts[page - 1], unlabeled[0], epi_year)
            if period == "weekly" and not monthly:
                weekly = unlabeled[0]
            elif period == "cumulative":
                cumulative = unlabeled[0]
        if weekly is None and cumulative is None:
            continue
        values, errors = _read_summary_rows(fields, weekly, cumulative, dash_is_zero)
        if _has_values(values):
            _merge_into(merged, values, errors + notes, page)
    return merged if merged.rows else None


def _has_values(values: dict) -> bool:
    return any(values.get(k) not in (None, "") for k in (*COUNT_FIELDS, *CUM_FIELD.values()))


def _merge_into(block: ParsedBlock, values: dict, errors: list[str], page: int | None) -> None:
    """Add values to the block without overwriting what an earlier reader found."""
    if not block.rows:
        block.rows.append({})
        block.page = page
    target = block.rows[0]
    for key, value in values.items():
        if value not in (None, "") and target.get(key) in (None, ""):
            target[key] = value
    block.errors.extend(errors)


def _period_from_context(page_text: str, row: list[str], epi_year: int) -> str | None:
    """Find a label-less data row in the page text and read the caption above it."""
    numbers = [c for c in row if c][:2]
    if not numbers:
        return None
    pattern = r"\s*".join(re.escape(n) for n in numbers)
    lines = page_text.splitlines()
    for i, line in enumerate(lines):
        if re.search(rf"(?<![\d.,]){pattern}(?![\d.,])", line):
            for back in range(i - 1, max(-1, i - 9), -1):
                caption = lines[back].lower().replace(" ", "")
                if "cumulative" in caption:
                    return "cumulative" if cumulative_is_calendar(lines[back], epi_year) else None
                if "current" in caption or re.search(r"table\d*:.*week\d", caption):
                    return "weekly"
            return None
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
                if key in values:
                    continue
                try:
                    values[key] = clean_count(cell, dash_is_zero=dash_is_zero)
                except ValueError as exc:
                    errors.append(f"{key}: {exc}")
                    values[key] = cell  # left raw so rule 2 can quarantine it
            elif name == "cfr" and target == "weekly":
                values["cfr_reported"] = clean_percent(cell)
            elif name == "cfr" and target == "cumulative":
                values["cum_cfr_reported"] = clean_percent(cell)
    return values, errors


def _summary_from_text(content: PdfContent, epi_year: int, dash_is_zero: bool, monthly: bool = False) -> ParsedBlock | None:
    """Fallback when the summary table has no ruling lines: read text lines."""
    for page_no, text in enumerate(content.page_texts, start=1):
        lines = [ln.strip() for ln in text.splitlines()]
        weekly_idx = next((i for i, ln in enumerate(lines) if re.match(r"^\s*current\s*week", ln, re.I)
                           and len(_numbers_after_label(ln)) >= 3), None)
        if weekly_idx is None:
            continue
        window = " ".join(lines[max(0, weekly_idx - 8):weekly_idx]).lower()
        order = _field_order(window)
        if len([f for f in order if f in COUNT_FIELDS]) < 2:
            order = ["suspected", "confirmed", "probable", "deaths", "cfr"]
        weekly = None if monthly else _numbers_after_label(lines[weekly_idx])
        cumulative = None
        for ln in lines[weekly_idx + 1:weekly_idx + 6]:
            if CUMULATIVE_RE.search(ln):
                if cumulative_is_calendar(ln.split("(")[0], epi_year):
                    cumulative = _numbers_after_label(ln)
                break
        values, errors = _read_summary_rows(order, weekly, cumulative, dash_is_zero)
        if any(k in values for k in (*COUNT_FIELDS, *CUM_FIELD.values())):
            return ParsedBlock(rows=[values], errors=errors, page=page_no, method="summary_text")
    return None


YEAR_WEEK_ROW_RE = re.compile(r"^\s*(20\d\d)\s+week\s*(\d{1,2})\s+(.*)$", re.I)


def _summary_from_year_week_rows(content: PdfContent, epi_year: int, epi_week: int, dash_is_zero: bool) -> ParsedBlock | None:
    """Rows such as ``2023 Week 5 72 7 0 0.0 3 5`` with 'Current'/'Cumulative' beside them (Mpox 2023)."""
    for page_no, text in enumerate(content.page_texts[:2], start=1):
        lines = [ln.strip() for ln in text.splitlines()]
        header_start = next((i for i, ln in enumerate(lines) if re.search(r"suspect", ln, re.I)), None)
        if header_start is None:
            continue
        order = _field_order(" ".join(lines[header_start:header_start + 6]).lower())
        if len([f for f in order if f in COUNT_FIELDS]) < 2:
            continue
        weekly = cumulative = None
        for i, ln in enumerate(lines):
            m = YEAR_WEEK_ROW_RE.match(ln)
            if not m or int(m.group(1)) != epi_year or int(m.group(2)) != epi_week:
                continue
            context = " ".join(lines[i:i + 2]).lower()
            numbers = NUMBER_TOKEN_RE.findall(m.group(3))
            if "cumulative" in context:
                cumulative = cumulative or numbers
            elif "current" in context:
                weekly = weekly or numbers
        values, errors = _read_summary_rows(order, weekly, cumulative, dash_is_zero)
        if any(k in values for k in (*COUNT_FIELDS, *CUM_FIELD.values())):
            return ParsedBlock(rows=[values], errors=errors, page=page_no, method="year_week_rows")
    return None


CAPTION_RE = re.compile(r"^\s*table\s*\d+\s*[:.\-–]?\s*(.*)$", re.I)


def _summary_from_captions(content: PdfContent, epi_year: int, dash_is_zero: bool, monthly: bool = False) -> ParsedBlock | None:
    """Text tables introduced by a caption, e.g. 'Table 2: Cumulative suspected cases (Epi week 1 - 9, 2025)'
    followed by header lines and one line of numbers (cholera 2025)."""
    block = ParsedBlock(method="captions")
    for page_no, text in enumerate(content.page_texts[:2], start=1):
        lines = [ln.strip() for ln in text.splitlines()]
        for i, line in enumerate(lines):
            m = CAPTION_RE.match(line)
            if not m:
                continue
            caption = m.group(1).lower().replace(" ", "")
            if any(w in caption for w in ("state", "lga", "trend", "month", "age", "sex")):
                continue
            if "cumulative" in caption:
                if not cumulative_is_calendar(m.group(1), epi_year):
                    continue
                period = "cumulative"
            elif "current" in caption and not monthly:
                period = "weekly"
            else:
                continue
            header: list[str] = []
            for ln in lines[i + 1:i + 7]:
                tokens = ln.split()
                if tokens and all(_is_number(t) for t in tokens):
                    order = _field_order(" ".join(header).lower())
                    if order and order[0] == "suspected":  # wrapped headers can scramble order; trust only clean ones
                        values, errors = _read_summary_rows(order, tokens if period == "weekly" else None,
                                                            tokens if period == "cumulative" else None, dash_is_zero)
                        if _has_values(values):
                            _merge_into(block, values, errors, page_no)
                    break
                header.append(ln)
    return block if block.rows else None


_WORD_NUMBERS = {"zero": 0, "no": 0, "nil": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_NUM = r"(\d[\d,]*|zero|no|nil|one|two|three|four|five|six|seven|eight|nine|ten)"
HIGHLIGHT_PATTERNS = {
    "suspected": (rf"suspected\s*cases?\s*[:\-]\s*{_NUM}", rf"a\s*total\s*of\s*{_NUM}\s*(?:new\s*)?suspected",
                  rf"{_NUM}\s*(?:new\s*)?suspected\s*cases?\s*(?:were|was|have|has)"),
    "confirmed": (rf"confirmed\s*cases?\s*[:\-]\s*{_NUM}", rf"{_NUM}\s*\(\s*[\d.]+\s*%\s*\)\s*were\s*confirmed",
                  rf"a\s*total\s*of\s*{_NUM}\s*(?:new\s*)?confirmed",
                  rf"{_NUM}\s*(?:new\s*)?confirmed\s*cases?\s*(?:were|was|have|has)"),
    "deaths": (rf"deaths?\s*[:\-]\s*{_NUM}", rf"a\s*total\s*of\s*{_NUM}\s*deaths?",
               rf"{_NUM}\s*(?:new\s*)?deaths?\s*(?:were|was|have|has)"),
}


def _to_int(token: str) -> int:
    token = token.lower()
    return _WORD_NUMBERS[token] if token in _WORD_NUMBERS else int(token.replace(",", ""))


def _highlight_values(text: str) -> dict:
    out = {}
    for field_name, patterns in HIGHLIGHT_PATTERNS.items():
        for pattern in patterns:
            m = re.search(pattern, text, re.I)
            if m:
                out[field_name] = _to_int(m.group(1))
                break
    return out


def _summary_from_highlights(content: PdfContent, epi_year: int, monthly: bool = False,
                             epi_week: int | None = None) -> ParsedBlock | None:
    """Sentences such as 'Suspected Cases: 42 across 18 states' (Mpox 2024, diphtheria 2025, yellow fever).

    The text is split at the first line mentioning 'cumulative': figures
    before it describe the reporting period, figures after it the cumulative
    totals (kept only when they are calendar-year totals).
    """
    lines = "\n".join(content.page_texts[:2]).splitlines()
    cut = next((i for i, ln in enumerate(lines) if re.search(r"cumulative", ln, re.I)), None)
    values: dict = {}
    if not monthly and epi_week is not None:
        # current-period figures only under an explicit heading for THIS week (not a range, not the title)
        anchor_re = re.compile(rf"(?:wee?k|wk)[\s\-:]*0?{epi_week}\b(?!\s*[-–])|this\s+week|reporting\s+week", re.I)
        range_re = re.compile(r"\s[-–—]\s|–|—|\bto\b", re.I)  # a period spanning several weeks
        anchor = next((i for i, ln in enumerate(lines[:cut]) if i >= 2 and anchor_re.search(ln)
                       and not range_re.search(ln)), None)
        if anchor is not None:
            end = next((j for j in range(anchor + 1, cut if cut is not None else len(lines))
                        if range_re.search(lines[j]) and re.search(r"wee?k|wk", lines[j], re.I)), cut)
            values.update(_highlight_values(" ".join(lines[anchor:end])))
    if cut is not None and cumulative_is_calendar(" ".join(lines[cut:cut + 2]), epi_year):
        cum = _highlight_values(" ".join(lines[cut:cut + 25]))
        values.update({CUM_FIELD[k]: v for k, v in cum.items()})
    if not values:
        return None
    return ParsedBlock(rows=[values], page=1, method="highlights",
                       errors=["values read from highlight sentences; check against the PDF"])


def _field_order(window: str) -> list[str]:
    hits = []
    for name, pattern in (("suspected", r"suspect"), ("confirmed", r"(?<!\()(?<!\(\s)confirm"),
                          ("probable", r"probable"), ("deaths", r"death"), ("cfr", r"fatality|\bcfr\b")):
        m = re.search(pattern, window)
        if m:
            hits.append((m.start(), name))
    return [name for _, name in sorted(hits)]


def _numbers_after_label(line: str) -> list[str]:
    body = re.sub(r"^[^:)]*(?:\(week\s*\d+\))?\s*:?", "", line, flags=re.I)
    tokens = NUMBER_TOKEN_RE.findall(body)
    return [t for t in tokens if t]


def read_summary(content: PdfContent, epi_year: int, *, epi_week: int | None = None, dash_is_zero: bool = False,
                 monthly: bool = False, allow_highlights: bool = True) -> ParsedBlock | None:
    """Combine the structured readers (tables, text rows, captions); highlight sentences only as a last resort.

    Earlier readers win; later ones only fill values that are still missing.
    """
    merged = ParsedBlock()
    methods = []
    readers = [
        lambda: _summary_from_tables(content, epi_year, dash_is_zero, monthly),
        lambda: _summary_from_text(content, epi_year, dash_is_zero, monthly),
        lambda: (_summary_from_year_week_rows(content, epi_year, epi_week, dash_is_zero)
                 if epi_week is not None and not monthly else None),
        lambda: _summary_from_captions(content, epi_year, dash_is_zero, monthly),
    ]
    for reader in readers:
        block = reader()
        if block and block.rows:
            _merge_into(merged, block.rows[0], block.errors, block.page)
            methods.append(block.method)
    if not merged.rows and allow_highlights:
        return _summary_from_highlights(content, epi_year, monthly, epi_week)
    if not merged.rows:
        return None
    merged.method = "+".join(methods)
    return merged


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
    default_period: str | None = None,
    dash_is_zero: bool = False,
) -> ParsedBlock | None:
    """Read the first table that lists at least two recognised states.

    Columns whose header does not say whether they are weekly or cumulative
    are skipped unless the template sets ``default_period``.
    """
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
            if period not in ("weekly", "cumulative"):
                continue  # never guess whether a state column is weekly or cumulative
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
