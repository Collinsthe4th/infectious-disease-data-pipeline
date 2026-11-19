"""Turn one raw document into tidy weekly rows (Layer 4a).

For a PDF: check the text layer, classify the report from its title page,
then try each matching layout template until one yields figures. If no
template fits, raise :class:`ExtractionError` so the document goes to the
manual queue and the run continues (Section 3.5.2).

For a manual-entry CSV: one report per disease and week in the file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from src.common.epiweek import is_valid_week, week_start
from src.extraction import state_table_ocr
from src.extraction.classify import Classification, classify
from src.extraction.manual_csv import read_manual_csv
from src.extraction.parsers import read_disease_rows, read_state_tables, read_summary
from src.extraction.pdf_reader import NotAPdfError, PdfContent, camelot_tables, read_pdf
from src.extraction.standardise import clean_count, clean_percent
from src.extraction.templates import LayoutTemplate, candidate_templates

log = logging.getLogger(__name__)

MEASURES = ("suspected", "confirmed", "deaths", "cum_suspected", "cum_confirmed", "cum_deaths")


class ExtractionError(Exception):
    """The document could not be parsed automatically."""

    def __init__(self, message: str, *, manual: bool = True) -> None:
        super().__init__(message)
        self.manual = manual


@dataclass
class ReportExtraction:
    """Figures read from one report, before standardisation and validation."""

    report_code: str
    report_type: str
    disease_code: str | None
    epi_year: int
    epi_week: int
    rows: pd.DataFrame
    template_id: str
    engine: str
    title: str | None = None
    printed_start: date | None = None
    printed_end: date | None = None
    published_date: date | None = None
    is_synthetic: bool = False
    parse_errors: list[str] = field(default_factory=list)

    @property
    def week_start(self) -> date | None:
        if is_valid_week(self.epi_year, self.epi_week):
            return week_start(self.epi_year, self.epi_week)
        return None


def extract_document(doc: Mapping[str, Any], path: Path, alias_map: Mapping[str, str]) -> list[ReportExtraction]:
    """Dispatch on file type. ``doc`` is a ``raw.documents`` row as a dict."""
    if path.suffix.lower() == ".csv":
        return extract_manual_csv(doc, path)
    return [extract_pdf(doc, path, alias_map)]


# --------------------------------------------------------------------------- #
#  PDF
# --------------------------------------------------------------------------- #

def extract_pdf(doc: Mapping[str, Any], path: Path, alias_map: Mapping[str, str]) -> ReportExtraction:
    try:
        content = read_pdf(path)
    except NotAPdfError as exc:
        raise ExtractionError(str(exc)) from exc
    if not content.has_text_layer:
        raise ExtractionError("no text layer (scanned image): needs manual entry")

    cls = classify(content.head_text(), listing_title=doc.get("listing_title"), disease_hint=doc.get("disease_hint"))
    if not cls.is_complete:
        raise ExtractionError(f"could not read the epidemiological week/year from the title page ({cls.title!r})")
    if cls.report_type == "situation_report" and cls.disease_code is None:
        raise ExtractionError("situation report for a disease outside the seven priority diseases", manual=False)

    ws = week_start(cls.epi_year, cls.epi_week) if is_valid_week(cls.epi_year, cls.epi_week) else None
    tried: list[str] = []
    for template in candidate_templates(cls.report_type, cls.disease_code, ws):
        tried.append(template.template_id)
        result = _apply_template(template, content, cls, alias_map, path)
        if result is not None:
            rows, errors, engine, page = result
            return ReportExtraction(
                report_code=_report_code(cls, doc),
                report_type=cls.report_type,
                disease_code=cls.disease_code,
                epi_year=cls.epi_year,
                epi_week=cls.epi_week,
                rows=rows,
                template_id=template.template_id,
                engine=engine,
                title=cls.title,
                printed_start=cls.printed_start,
                printed_end=cls.printed_end,
                published_date=doc.get("listing_date"),
                is_synthetic=bool(doc.get("is_synthetic")),
                parse_errors=errors,
            )
    raise ExtractionError(f"no layout template matched (tried: {', '.join(tried) or 'none'})")


def _apply_template(
    template: LayoutTemplate,
    content: PdfContent,
    cls: Classification,
    alias_map: Mapping[str, str],
    path: Path,
) -> tuple[pd.DataFrame, list[str], str, int | None] | None:
    rows: list[dict] = []
    errors: list[str] = []
    engine = "pdfplumber"
    page = None

    national: dict | None = None
    if template.read_summary:
        monthly = cls.period == "monthly"
        summary = read_summary(content, cls.epi_year, epi_week=cls.epi_week, dash_is_zero=template.dash_is_zero,
                               monthly=monthly, allow_highlights=template.allow_highlights)
        if summary and summary.rows:
            national = {"location_raw": "National", **summary.rows[0]}
            if monthly:  # a monthly report's period figures are not weekly values
                national = {k: v for k, v in national.items() if k not in ("suspected", "confirmed", "deaths", "cfr_reported")}
            errors += summary.errors
            page = summary.page

    if template.read_state_table and cls.period == "weekly":
        block = read_state_tables(content.tables, alias_map, default_period=template.state_default_period,
                                  dash_is_zero=template.dash_is_zero)
        if block is None:
            fallback = camelot_tables(path)
            if fallback:
                block = read_state_tables(fallback, alias_map, default_period=template.state_default_period,
                                          dash_is_zero=template.dash_is_zero)
                engine = "camelot" if block else engine
        if block:
            errors += block.errors
            page = page or block.page
            for r in block.rows:
                if r.pop("is_total_row", False):
                    if national is None:
                        national = {**r, "location_raw": "National"}
                    continue
                rows.append(r)
        elif template.ocr_state_table:
            ocr_rows, note, ocr_page = _ocr_state_rows(path, alias_map, national)
            if note:
                errors.append(note)
            if ocr_rows:
                rows.extend(ocr_rows)
                engine = "ocr"  # national figures from the text layer, states by OCR
                page = page or ocr_page

    if template.read_disease_rows:
        block = read_disease_rows(content, dash_is_zero=template.dash_is_zero)
        if block:
            errors += block.errors
            page = block.page
            rows.extend(block.rows)

    if national is not None:
        rows.insert(0, national)
    if not any(any(r.get(m) not in (None, "") for m in MEASURES) for r in rows):
        return None

    df = pd.DataFrame(rows)
    if "disease_code" not in df.columns:
        df["disease_code"] = cls.disease_code
    df["disease_code"] = df["disease_code"].fillna(cls.disease_code)
    df["epi_year"] = cls.epi_year
    df["epi_week"] = cls.epi_week
    df["source_page"] = page
    return df, errors, engine, page


def _ocr_state_rows(path: Path, alias_map: Mapping[str, str], national: dict | None
                    ) -> tuple[list[dict], str | None, int | None]:
    """State rows from an image-only state table, or a note saying why none were used."""
    if not state_table_ocr.tesseract_available():
        return [], "state table is an image and OCR (tesseract) is not installed: national figures only", None
    target = None
    if national and national.get("cum_confirmed") not in (None, ""):
        try:
            target = clean_count(national["cum_confirmed"])
        except ValueError:
            target = None
    try:
        table, page = state_table_ocr.read_state_table_pdf(path, alias_map, target)
    except Exception as exc:  # noqa: BLE001 - OCR problems must never stop the national figures loading
        log.warning("state table OCR failed for %s: %s", path.name, exc)
        return [], f"state table OCR failed: {exc}", None
    if table is None:
        return [], None, None
    if not table.ok:
        return [], "state table (OCR) not used, checks failed: " + "; ".join(table.problems[:4]), page
    return state_table_ocr.to_rows(table), None, page


def _report_code(cls: Classification, doc: Mapping[str, Any]) -> str:
    family = "SITREP" if cls.report_type == "situation_report" else "WER"
    disease = cls.disease_code or "ALL"
    return f"NCDC-{family}-{disease}-{cls.epi_year}-W{cls.epi_week:02d}-{str(doc['sha256'])[:8]}"


# --------------------------------------------------------------------------- #
#  Manual CSV
# --------------------------------------------------------------------------- #

def extract_manual_csv(doc: Mapping[str, Any], path: Path) -> list[ReportExtraction]:
    df = read_manual_csv(path)
    prefix = "SYNTH" if doc.get("is_synthetic") else "MANUAL"
    reports: list[ReportExtraction] = []
    errors_all: list[str] = []
    numeric = [c for c in MEASURES if c in df.columns]
    for (disease, year, week), group in df.groupby(["disease_code", "epi_year", "epi_week"], sort=True):
        rows = group.copy()
        errors: list[str] = []
        for col in numeric:
            parsed = []
            for value in rows[col]:
                try:
                    parsed.append(clean_count(value))
                except ValueError as exc:
                    errors.append(f"{col}: {exc}")
                    parsed.append(value)
            rows[col] = parsed
        if "cfr_reported" in rows.columns:
            rows["cfr_reported"] = [clean_percent(v) for v in rows["cfr_reported"]]
        try:
            year_i, week_i = int(year), int(week)
        except ValueError:
            errors_all.append(f"bad epi year/week {year!r}/{week!r}")
            continue
        published = None
        if "published_date" in rows.columns and rows["published_date"].iloc[0]:
            published = pd.to_datetime(rows["published_date"].iloc[0]).date()
        rows["epi_year"] = year_i
        rows["epi_week"] = week_i
        rows["source_page"] = None
        reports.append(
            ReportExtraction(
                report_code=f"{prefix}-{str(doc['sha256'])[:8]}-{disease}-{year_i}-W{week_i:02d}",
                report_type="manual_entry",
                disease_code=disease,
                epi_year=year_i,
                epi_week=week_i,
                rows=rows.reset_index(drop=True),
                template_id="manual_csv_v1",
                engine="manual",
                title=path.name,
                published_date=published,
                is_synthetic=bool(doc.get("is_synthetic")),
                parse_errors=errors,
            )
        )
    if not reports:
        raise ExtractionError(f"manual CSV had no usable rows ({'; '.join(errors_all[:3])})")
    return reports
