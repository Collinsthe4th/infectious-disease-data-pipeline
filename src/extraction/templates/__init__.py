"""Layout templates: one entry per NCDC report family and date range.

A template does not hard-code page coordinates. It names which blocks to
look for (the national summary table, the state table) and how to read
them; columns are found by header words, so small layout changes between
years do not break parsing. When NCDC changes a layout substantially, add a
new template with a ``valid_from`` date rather than editing the old one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.common.diseases import DISEASES


@dataclass(frozen=True)
class LayoutTemplate:
    template_id: str
    report_type: str  # 'situation_report' | 'weekly_epi_report'
    diseases: tuple[str, ...]  # empty tuple = any disease
    valid_from: date | None = None
    valid_to: date | None = None
    read_summary: bool = True  # "Current week" / "<year> Cumulative" rows
    read_state_table: bool = True
    read_disease_rows: bool = False  # WER: one row per disease
    state_default_period: str = "weekly"  # used when a state column header has no period word
    dash_is_zero: bool = False
    verified: bool = False  # True once checked against real NCDC PDFs
    notes: str = ""

    def applies(self, report_type: str, disease_code: str | None, week_start: date | None) -> bool:
        if report_type != self.report_type:
            return False
        if self.diseases and disease_code not in self.diseases:
            return False
        if week_start and self.valid_from and week_start < self.valid_from:
            return False
        if week_start and self.valid_to and week_start > self.valid_to:
            return False
        return True


TEMPLATES: tuple[LayoutTemplate, ...] = (
    LayoutTemplate(
        template_id="lassa_sitrep_v1",
        report_type="situation_report",
        diseases=("LASSA",),
        valid_from=date(2020, 1, 1),
        state_default_period="weekly",
        verified=True,
        notes="Table 1 summary rows checked against the Week 44 2026 report; "
        "state table read when it has a text layer.",
    ),
    LayoutTemplate(
        template_id="ncdc_sitrep_generic_v1",
        report_type="situation_report",
        diseases=tuple(code for code in DISEASES if code != "LASSA"),
        valid_from=date(2020, 1, 1),
        notes="Header-word mapping shared by the other six situation reports. "
        "Confirm against each disease's PDFs (run the 'NCDC parse check' workflow).",
    ),
    LayoutTemplate(
        template_id="ncdc_sitrep_fallback_v1",
        report_type="situation_report",
        diseases=(),
        read_summary=True,
        read_state_table=True,
        state_default_period="cumulative",
        notes="Last resort for unexpected layouts: treats unlabelled state columns as cumulative.",
    ),
    LayoutTemplate(
        template_id="ncdc_wer_v1",
        report_type="weekly_epi_report",
        diseases=(),
        read_summary=False,
        read_state_table=False,
        read_disease_rows=True,
        notes="Weekly Epidemiological Report: one national row per priority disease.",
    ),
)


def candidate_templates(report_type: str, disease_code: str | None, week_start: date | None) -> list[LayoutTemplate]:
    """Templates to try, most specific first."""
    return [t for t in TEMPLATES if t.applies(report_type, disease_code, week_start)]
