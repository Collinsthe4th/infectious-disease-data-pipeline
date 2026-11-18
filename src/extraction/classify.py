"""Report classification from the title page (Section 3.4.4, step 2).

Reads the disease, report type, epidemiological week and the printed date
range with regular expressions, e.g.::

    Lassa Fever Situation Report
    Epi Week 44: 26th October – 1st November 2026
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from src.common.diseases import DISEASES, detect_disease
from src.common.epiweek import is_valid_week, week_of

MONTHS = {
    m: i
    for i, names in enumerate(
        [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",),
         ("jun", "june"), ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"),
         ("oct", "october"), ("nov", "november"), ("dec", "december")],
        start=1,
    )
    for m in names
}
_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_DASH = r"\s*(?:–|—|-|to)\s*"

# "26th October – 1st November 2026" or "1st – 7th January, 2024" or "26 Oct 2025 - 1 Nov 2025"
DATE_RANGE_RE = re.compile(
    rf"{_DAY}\s+{_MONTH}?,?\s*(\d{{4}})?{_DASH}{_DAY}\s+{_MONTH},?\s*(\d{{4}})", re.IGNORECASE
)
EPI_WEEK_RE = re.compile(r"(?:epi(?:demiological)?\s*)?week\s*(?:no\.?|number)?\s*[:\-]?\s*(\d{1,2})\b", re.I)
EPI_WEEK_YEAR_RE = re.compile(r"(?:epi\s*)?week\s*[:\-]?\s*(\d{1,2})\s*[,/ ]\s*(20\d\d)\b", re.I)
WER_RE = re.compile(r"weekly\s+epidemiological\s+report", re.I)
SITREP_RE = re.compile(r"situation\s+report|sitrep|an update of", re.I)


@dataclass(frozen=True)
class Classification:
    report_type: str  # 'situation_report' | 'weekly_epi_report'
    disease_code: str | None
    epi_year: int | None
    epi_week: int | None
    printed_start: date | None
    printed_end: date | None
    title: str

    @property
    def is_complete(self) -> bool:
        return self.epi_year is not None and self.epi_week is not None


def _month(token: str | None) -> int | None:
    return MONTHS.get(token.lower()[:4].rstrip(".")) or MONTHS.get(token.lower()[:3]) if token else None


def parse_date_range(text: str) -> tuple[date, date] | None:
    """Return the first printed (start, end) date range in ``text``."""
    for m in DATE_RANGE_RE.finditer(text):
        d1, m1, y1, d2, m2, y2 = m.groups()
        end_month = _month(m2)
        start_month = _month(m1) or end_month
        end_year = int(y2)
        start_year = int(y1) if y1 else (end_year - 1 if start_month and end_month and start_month > end_month else end_year)
        try:
            start = date(start_year, start_month, int(d1))
            end = date(end_year, end_month, int(d2))
        except (TypeError, ValueError):
            continue
        if 0 <= (end - start).days <= 7:
            return start, end
    return None


def classify(head_text: str, *, listing_title: str | None = None, disease_hint: str | None = None) -> Classification:
    """Classify a report from its first pages and (optionally) its listing title."""
    text = " ".join(head_text.split())
    title_line = next((ln.strip() for ln in head_text.splitlines() if ln.strip()), "")

    disease = detect_disease(text[:600]) or (DISEASES.get(disease_hint) if disease_hint else None)
    if WER_RE.search(text[:600]) and not (disease_hint and SITREP_RE.search(text[:600])):
        report_type, disease_code = "weekly_epi_report", None
    else:
        report_type, disease_code = "situation_report", disease.code if disease else None

    printed = parse_date_range(text[:1500])
    epi_week = epi_year = None
    wy = EPI_WEEK_YEAR_RE.search(text[:1500])
    if wy:
        epi_week, epi_year = int(wy.group(1)), int(wy.group(2))
    else:
        w = EPI_WEEK_RE.search(text[:1500]) or (EPI_WEEK_RE.search(listing_title) if listing_title else None)
        if w:
            epi_week = int(w.group(1))
    if printed:
        iso_year, iso_week = week_of(printed[0])
        if epi_week is None:
            epi_week = iso_week
        if epi_year is None:
            # the ISO year of the week's Monday is the epi year (handles Dec/Jan weeks)
            epi_year = iso_year if iso_week == epi_week else printed[1].year
    if epi_year is not None and epi_week is not None and not is_valid_week(epi_year, epi_week):
        # keep the values so rule 8 can quarantine with a clear reason
        pass

    return Classification(
        report_type=report_type,
        disease_code=disease_code,
        epi_year=epi_year,
        epi_week=epi_week,
        printed_start=printed[0] if printed else None,
        printed_end=printed[1] if printed else None,
        title=title_line[:300],
    )
