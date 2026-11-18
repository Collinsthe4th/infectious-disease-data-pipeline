"""Render a synthetic situation report PDF that mimics the NCDC layout.

Used for parser test fixtures and the demo. The layout copies the real
Lassa fever report's structure (title lines, Table 1 summary with
"Current week" and "<year> Cumulative" rows, a state table), and every
page carries a SYNTHETIC watermark line.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from src.common.epiweek import week_end, week_start

WATERMARK = "SYNTHETIC DEMO DATA - NOT REAL NCDC FIGURES"


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _pct(deaths: int | None, base: int | None) -> str:
    if not base or deaths is None:
        return "-"
    return f"{100 * deaths / base:.1f}%"


def write_sitrep_pdf(
    path: Path,
    *,
    disease_name: str,
    epi_year: int,
    epi_week: int,
    national_week: Mapping[str, int | None],
    national_cum: Mapping[str, int | None],
    states: Sequence[Mapping[str, object]] = (),
    cfr_base: str = "confirmed",
    cfr_override: str | None = None,
    printed_dates: tuple | None = None,
) -> Path:
    """Write the PDF and return its path.

    ``states`` items: ``{"state": str, "suspected", "confirmed", "deaths",
    "cum_suspected", "cum_confirmed", "cum_deaths"}``.
    """
    start, end = printed_dates or (week_start(epi_year, epi_week), week_end(epi_year, epi_week))
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"{disease_name} Situation Report", styles["Title"]),
        Paragraph(
            f"Epi Week {epi_week}: {_ordinal(start.day)} {start:%B} – {_ordinal(end.day)} {end:%B} {end.year}",
            styles["Heading2"],
        ),
        Paragraph(WATERMARK, styles["Normal"]),
        Spacer(1, 12),
        Paragraph("Table 1: Summary of current week and cumulative figures", styles["Heading3"]),
    ]
    header = ["Reporting Period", "Suspected cases", "Confirmed cases", "Probable cases",
              "Deaths (Confirmed cases)", "Case Fatality Rate (CFR)"]

    def summary_row(label: str, v: Mapping[str, int | None], cfr: str | None = None) -> list[str]:
        fmt = lambda x: "-" if x is None else f"{x:,}"  # noqa: E731
        return [label, fmt(v.get("suspected")), fmt(v.get("confirmed")), "0", fmt(v.get("deaths")),
                cfr or _pct(v.get("deaths"), v.get(cfr_base))]

    data = [
        header,
        summary_row(f"Current week (week {epi_week})", national_week, cfr_override),
        summary_row(f"{epi_year} Cumulative (week {epi_week})", national_cum),
    ]
    story.append(_ruled(data))
    if states:
        story += [Spacer(1, 12),
                  Paragraph(f"Table 3: Weekly and cumulative cases by state, {epi_year}", styles["Heading3"])]
        rows = [["States", f"Week {epi_week} Suspected", f"Week {epi_week} Confirmed", f"Week {epi_week} Deaths",
                 "Cumulative Suspected", "Cumulative Confirmed", "Cumulative Deaths"]]
        totals = [0] * 6
        for s in states:
            vals = [s.get(k) for k in ("suspected", "confirmed", "deaths", "cum_suspected", "cum_confirmed", "cum_deaths")]
            rows.append([str(s["state"])] + ["-" if v is None else str(v) for v in vals])
            totals = [t + (v or 0) for t, v in zip(totals, vals)]
        rows.append(["Total"] + [str(t) for t in totals])
        story.append(_ruled(rows))
    story += [Spacer(1, 12), Paragraph(WATERMARK, styles["Normal"])]
    path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(path), pagesize=A4, title=f"{disease_name} Situation Report").build(story)
    return path


def _ruled(data: list[list[str]]) -> Table:
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
    ]))
    return table
