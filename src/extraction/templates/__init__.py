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
    ocr_state_table: bool = False  # state table is a picture: read it with OCR (state_table_ocr)
    text_readers: tuple[str, ...] = ()  # plain-text readers to try (text_readers.py): key_indicators, summary_blocks, since_january
    read_disease_rows: bool = False  # WER: one row per disease
    state_default_period: str | None = None  # period for state columns whose header has no period word
    allow_highlights: bool = True  # last resort: read figures from highlight sentences
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
        verified=True,
        ocr_state_table=True,
        notes="Table 1 summary rows checked against the Week 44 2026 report. Table 3 (states) is an "
        "image in 2022-2026 reports: read by OCR and used only when every column adds up to its Total "
        "row and the confirmed total matches Table 1.",
    ),
    LayoutTemplate(
        template_id="cholera_sitrep_2021_v1",
        report_type="situation_report",
        diseases=("CHOLERA",),
        valid_from=date(2021, 1, 1),
        valid_to=date(2022, 12, 31),
        verified=True,
        text_readers=("summary_blocks", "since_january"),
        notes="2021-2022 cholera reports: the text layer has no spaces, so sentences cannot be read, but the "
        "state table has explicit 'Reporting week N' and 'Cumulative' headers and a Total row. Checked against "
        "the week 25 2021 report (14,343 suspected, 325 deaths since January). 2022 monthly-format reports: "
        "'Summary of current week' text block (week 26 2022: 23 suspected, 0 deaths; year to date 2,523 / 78, "
        "equal to the sum of the monthly table).",
    ),
    LayoutTemplate(
        template_id="mpox_key_indicators_v1",
        report_type="situation_report",
        diseases=("MPOX",),
        valid_from=date(2021, 1, 1),
        valid_to=date(2022, 12, 31),
        verified=True,
        read_summary=False,
        read_state_table=False,
        text_readers=("key_indicators",),
        notes="2022 mpox updates: 'Table 1 - Key Indicators' list. Checked against weeks 28 and 46 2022 "
        "(week 28: 37 suspected, 16 confirmed; Jan-week 28: 338 suspected, 117 confirmed, 3 deaths).",
    ),
    LayoutTemplate(
        template_id="ncdc_sitrep_generic_v1",
        report_type="situation_report",
        diseases=tuple(code for code in DISEASES if code != "LASSA"),
        valid_from=date(2020, 1, 1),
        read_state_table=False,
        notes="National figures only until each disease's state table is verified. "
        "Header-word mapping shared by the other six situation reports. "
        "Confirm against each disease's PDFs (run the 'NCDC parse check' workflow).",
    ),
    LayoutTemplate(
        template_id="ncdc_sitrep_fallback_v1",
        report_type="situation_report",
        diseases=(),
        read_summary=True,
        read_state_table=False,
        notes="Last resort for unexpected layouts (any disease); national figures only.",
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
