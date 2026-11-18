"""Report classification from the title page (Section 3.4.4, step 2).

Reads the disease, report type, reporting period (weekly or monthly),
epidemiological week and printed dates with regular expressions. Real
NCDC title pages seen so far include::

    Lassa Fever Situation Report / Epi Week 44: 26th October – 1st November 2026
    Cholera Situation Report Epi Week: 9, 2025
    CholeraSituationReport EpiWeek:042022                (text with spaces lost)
    UPDATE ON MPOX IN NIGERIA / EPI-WEEK: 44 (October 28–November 3, 2024)
    TITLE: ... / EPI-WEEK: 5 / DATE: February 5, 2023
    WEEKLY DIPHTHERIA SITUATION REPORT / As of 26th January 2025 (Epi-Week 04, 2025)
    Measles Situation Report / September 2026                       (monthly)
    YELLOW FEVER SITUATION REPORT / ... Monthly Sitrep / Reporting Month: May 2025

Rules: the week printed in the title area wins over weeks mentioned in the
body; the year comes from the printed date range, else a week-year pair,
else the report date. Monthly reports are dated to the week containing the
last day of the month and only their year-to-date totals are used.
"""

from __future__ import annotations

import calendar
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
_MONTH = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
          r"sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_DASH = r"\s*(?:–|—|-|to)\s*"

# "26th October – 1st November 2026", "1st – 7th January, 2024", "31 Mar. – 6 Apr. 2025"
DAY_FIRST_RANGE_RE = re.compile(
    rf"{_DAY}\s*{_MONTH}?,?\s*(\d{{4}})?{_DASH}{_DAY}\s*{_MONTH},?\s*(\d{{4}})", re.IGNORECASE)
# "October 28–November 3, 2024", "January 2 - 8, 2023"
MONTH_FIRST_RANGE_RE = re.compile(
    rf"{_MONTH}\s*{_DAY},?\s*(\d{{4}})?{_DASH}{_MONTH}?\s*{_DAY},?\s*(\d{{4}})", re.IGNORECASE)
# single dates: "November 3, 2024", "26th January 2025", "31st May 2025", "July 31st 2024"
SINGLE_DATE_RES = (
    re.compile(rf"{_DAY}\s*(?:of\s*)?{_MONTH},?\s*(\d{{4}})", re.IGNORECASE),
    re.compile(rf"{_MONTH}\s*{_DAY},?\s*(\d{{4}})", re.IGNORECASE),
)
MONTH_YEAR_RE = re.compile(rf"(?<![\d\s]\s){_MONTH}\s*,?\s*(20\d\d)\b", re.IGNORECASE)

EPI_WEEK_RE = re.compile(r"(?:epi(?:demiological)?[\s\-]*)?wee?k\s*(?:no\.?|number)?\s*[:\-]?\s*(\d{1,2})(?!\d{3})\b", re.I)
EPI_WEEK_YEAR_RE = re.compile(r"(?:epi[\s\-]*)?week\s*[:\-]?\s*(\d{1,2})\s*[,/ ]?\s*(20\d\d)\b", re.I)
WER_RE = re.compile(r"weekly\s*epidemiological\s*report", re.I)
SITREP_RE = re.compile(r"situation\s*report|sitrep|an\s*update\s*of|update\s*on", re.I)
MONTHLY_RE = re.compile(r"monthly\s*sitrep|reporting\s*month|situation\s*report\s*" + _MONTH + r"\s*20\d\d|"
                        r"summary\s*of\s*the\s*current\s*month|new\s*in\s*" + _MONTH + r"|"
                        r"\bin\s+" + _MONTH + r",?\s*20\d\d\s*:", re.I)

TITLE_AREA = 450  # characters treated as the title block


@dataclass(frozen=True)
class Classification:
    report_type: str  # 'situation_report' | 'weekly_epi_report'
    disease_code: str | None
    epi_year: int | None
    epi_week: int | None
    printed_start: date | None
    printed_end: date | None
    title: str
    period: str = "weekly"  # 'weekly' | 'monthly'
    reference_date: date | None = None

    @property
    def is_complete(self) -> bool:
        return self.epi_year is not None and self.epi_week is not None


def _month(token: str | None) -> int | None:
    if not token:
        return None
    t = token.lower().rstrip(".")
    return MONTHS.get(t) or MONTHS.get(t[:4]) or MONTHS.get(t[:3])


def _range_ok(start: date, end: date) -> bool:
    return 0 <= (end - start).days <= 7


def parse_date_range(text: str) -> tuple[date, date] | None:
    """Return the first printed weekly (start, end) date range in ``text``."""
    candidates: list[tuple[int, date, date]] = []
    for m in DAY_FIRST_RANGE_RE.finditer(text):
        d1, m1, y1, d2, m2, y2 = m.groups()
        end_month = _month(m2)
        start_month = _month(m1) or end_month
        end_year = int(y2)
        start_year = int(y1) if y1 else (end_year - 1 if start_month and end_month and start_month > end_month else end_year)
        try:
            start, end = date(start_year, start_month, int(d1)), date(end_year, end_month, int(d2))
        except (TypeError, ValueError):
            continue
        if _range_ok(start, end):
            candidates.append((m.start(), start, end))
    for m in MONTH_FIRST_RANGE_RE.finditer(text):
        m1, d1, y1, m2, d2, y2 = m.groups()
        start_month = _month(m1)
        end_month = _month(m2) or start_month
        end_year = int(y2)
        start_year = int(y1) if y1 else (end_year - 1 if start_month > end_month else end_year)
        try:
            start, end = date(start_year, start_month, int(d1)), date(end_year, end_month, int(d2))
        except (TypeError, ValueError):
            continue
        if _range_ok(start, end):
            candidates.append((m.start(), start, end))
    if not candidates:
        return None
    _, start, end = min(candidates)
    return start, end


def parse_single_date(text: str) -> date | None:
    """First full date in ``text`` (day-month-year or month-day-year)."""
    found: list[tuple[int, date]] = []
    for rx, order in ((SINGLE_DATE_RES[0], "dmy"), (SINGLE_DATE_RES[1], "mdy")):
        for m in rx.finditer(text):
            a, b, year = m.groups()
            day, mon = (a, b) if order == "dmy" else (b, a)
            try:
                found.append((m.start(), date(int(year), _month(mon), int(day))))
            except (TypeError, ValueError):
                continue
    return min(found)[1] if found else None


def _month_end(text: str) -> date | None:
    m = MONTH_YEAR_RE.search(text)
    if not m:
        return None
    month, year = _month(m.group(1)), int(m.group(2))
    return date(year, month, calendar.monthrange(year, month)[1])


def _year_for_week(epi_week: int, ref: date) -> int:
    """ISO year of week ``epi_week`` closest to the reference date."""
    if epi_week >= 50 and ref.month == 1:
        return ref.year - 1
    if epi_week <= 2 and ref.month == 12:
        return ref.year + 1
    return ref.year


def classify(head_text: str, *, listing_title: str | None = None, disease_hint: str | None = None) -> Classification:
    """Classify a report from its first pages and (optionally) its listing title."""
    text = " ".join(head_text.split())
    title_area = text[:TITLE_AREA]
    title_line = next((ln.strip() for ln in head_text.splitlines() if ln.strip()), "")

    disease = detect_disease(text[:800]) or (DISEASES.get(disease_hint) if disease_hint else None)
    if WER_RE.search(title_area) and not SITREP_RE.search(title_area.replace("Weekly Epidemiological Report", "")):
        report_type, disease_code = "weekly_epi_report", None
    else:
        report_type, disease_code = "situation_report", disease.code if disease else None

    printed = parse_date_range(text[:1500])
    period = "monthly" if MONTHLY_RE.search(text[:1200]) and not printed else "weekly"

    epi_week = epi_year = None
    reference: date | None = None
    w = EPI_WEEK_RE.search(title_area)
    wy = EPI_WEEK_YEAR_RE.search(title_area)
    if wy and w and wy.start() > w.start():
        wy = None  # a week-year pair later in the text (e.g. an outbreak start) is not this report's week
    if wy:
        epi_week, epi_year = int(wy.group(1)), int(wy.group(2))
    elif w:
        epi_week = int(w.group(1))

    if period == "monthly":
        reference = _month_end(text[:600]) or parse_single_date(text[:800])
        if reference:
            epi_year, epi_week = week_of(reference)
    elif printed:
        iso_year, iso_week = week_of(printed[0])
        if epi_week is None or not wy:
            if epi_week is None:
                epi_week = iso_week
            epi_year = iso_year if iso_week == epi_week else printed[1].year
    if epi_week is not None and epi_year is None:
        later = EPI_WEEK_YEAR_RE.search(text[:1500])
        if later and int(later.group(1)) == epi_week:
            epi_year = int(later.group(2))
        else:
            reference = parse_single_date(text[:800])
            if reference:
                epi_year = _year_for_week(epi_week, reference)
    if epi_week is None and listing_title:
        lw = EPI_WEEK_RE.search(listing_title)
        if lw:
            epi_week = int(lw.group(1))
            reference = reference or parse_single_date(text[:800])
            if reference:
                epi_year = _year_for_week(epi_week, reference)
    if epi_year is not None and epi_week is not None and not is_valid_week(epi_year, epi_week):
        pass  # kept so that rule 8 can quarantine it with a clear reason

    return Classification(
        report_type=report_type,
        disease_code=disease_code,
        epi_year=epi_year,
        epi_week=epi_week,
        printed_start=printed[0] if printed else None,
        printed_end=printed[1] if printed else None,
        title=title_line[:300],
        period=period,
        reference_date=reference,
    )
