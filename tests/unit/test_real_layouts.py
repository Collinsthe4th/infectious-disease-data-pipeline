"""Parser behaviour on text from real NCDC reports (excerpts captured by the NCDC parse check workflow).

Only the first lines of each report are used: enough to fix the layout,
never whole documents.
"""

import pytest

from src.extraction.classify import classify
from src.extraction.parsers import cumulative_is_calendar, read_summary
from src.extraction.pdf_reader import PdfContent

MPOX_2024_W44 = """UPDATE ON MPOX IN NIGERIA
SERIAL 20
NUMBER:
EPI-WEEK: 44 (October 28–November 3, 2024),
DATE: November 3, 2024
Key Highlights for Epi Week 44:
 Suspected Cases: 42 across 18 states and the Federal Capital Territory (FCT).
 Confirmed Cases: 6 reported from 4 states.
 Zero deaths were reported for the week.
Cumulative Summary (Weeks 1–44, 2024):
 Suspected Cases: 1,484 from 36 states and FCT.
 Confirmed Cases: 124 from 28 states and FCT.
 Deaths: 0"""

MPOX_2023_W5 = """TITLE: UPDATE ON MPOX (MPX) IN NIGERIA
SERIAL NUMBER: 5
EPI-WEEK: 5
DATE: February 5, 2023
Table 1 – Key Indicators
Reporting Reporting Suspected Confirmed Deaths Case States LGAs Affected
Year week cases cases (Confirmed Fatality Affected (Confirmed
2023 Week 5 72 7 0 0.0 3 5
Current
2023 Week 5 258 34 1 2.9 12 +FCT 27
Cumulative
2022 Week 5 7 0 0 0.0 0 0
Cumulative"""

CHOLERA_2025_W9 = """Cholera Situation Report Epi Week: 9, 2025
Epidemiological week 9: (3 February – 2 March 2025)
Key Points
Table 1: Current Epi-summary for week 9, 2025
Suspected Deaths Case Fatality States LGAs
Cases (Suspected cases) Ratio (%) Reporting Cases Reporting cases
2 0 0.0% 2 2
Table 2: Cumulative suspected cases (Epi week 1 - 9, 2025)
Suspected Deaths Case Fatality States LGAs
Cases (Suspected cases) Ratio (%) Reporting Cases Reporting cases
1,149 28 2.4% 25 66"""

CHOLERA_TABLES = [
    (1, [["Suspected Cases", "Deaths (Suspected cases)", "Case Fatality Ratio (%)", "States Reporting Cases", "LGAs Reporting cases"],
         ["2", "0", "0.0%", "2", "2"]]),
    (1, [["Suspected Cases", "Deaths (Suspected cases)", "Case Fatality Ratio (%)", "States Reporting Cases", "LGAs Reporting cases"],
         ["1,149", "28", "2.4%", "25", "66"]]),
]

CSM_2025_TABLE = [(1, [
    ["Reporting", "", "", "Deaths (Suspected", "Case Fatality", "States and LGAs"],
    ["Period", "Suspected", "Confirmed", "cases )", "Ratio (CFR)", "affected"],
    ["Epi Week 14", "296", "25", "10", "3.4%", "State(s): 6"],
    ["Cumulative (Epi week 40 - 14 of 2024/2025)", "2911", "192", "225", "7.7%", "State(s): 24"],
])]

DIPHTHERIA_2025_W4 = """WEEKLY DIPHTHERIA SITUATION REPORT
As of 26th January 2025 (Epi-Week 04, 2025)
HIGHLIGHTS
In Epi-Week 04, 2025
§ A total of 8 suspected cases were reported from 2 states across 5 LGAs.
§ Of the 8 suspected cases reported, 6 (75%) were confirmed cases (0 lab
§ A total of 0 deaths (CFR: 0%) were recorded among the confirmed cases.
Cumulatively: Epi-Week 19, 2022 - Epi-Week 4, 2025
§ A total of 41,336 suspected cases were reported from 37 states across 350 LGAs."""

MEASLES_2026_SEPT = """Situation Report September 2026
Measles Situation Report
September 2026
Key Points
Table 1: Summary of the current month, cumulative Epi month, current year, and comparison with the previous"""

MEASLES_TABLE = [(1, [
    ["Reporting Period", "Suspected cases", "Confirmed cases", "Deaths (Confirmed cases)", "Case Fatality Ratio (CFR)", "States and LGAs affected"],
    ["September 2026", "373", "214", "1", "0.47%", "State(s): 26"],
    ["Jan to September 2026", "10,894", "7,681", "119", "1.55%", "State(s): 36 + FCT"],
    ["Jan to September 2025", "26,438", "19,193", "76", "0.40%", "State(s): 36 + FCT"],
])]


def content(text, tables=()):
    return PdfContent(path=None, page_texts=[text], tables=list(tables))


@pytest.mark.parametrize("text,expected", [
    (MPOX_2024_W44, ("MPOX", 2024, 44, "weekly")),
    (MPOX_2023_W5, ("MPOX", 2023, 5, "weekly")),
    (CHOLERA_2025_W9, ("CHOLERA", 2025, 9, "weekly")),
    ("CholeraSituationReport EpiWeek:042022\nEpidemiologicalweek1-4:(03Januaryto30January2022)", ("CHOLERA", 2022, 4, "weekly")),
    ("Cerebrospinal Meningitis Situation Report Epi Week: 14 2025\nEpidemiological week 14: (31 Mar. – 6 Apr. 2025)", ("CSM", 2025, 14, "weekly")),
    (DIPHTHERIA_2025_W4, ("DIPHTHERIA", 2025, 4, "weekly")),
    ("Situation Report Epi Week:3\nDiphtheria Situation Report\nEpi Week 3: 12th January 2026 – 18th January 2026\n"
     "Cumulatively: Epi-Week 19, 2022 - Epi-Week 4, 2025", ("DIPHTHERIA", 2026, 3, "weekly")),
    (MEASLES_2026_SEPT, ("MEASLES", 2026, 40, "monthly")),
    ("YELLOW FEVER SITUATION REPORT\nSerial Number: 005 May 2025 Monthly Sitrep\nEpi Week: 20 - 24 as at 31st May 2025\n"
     "Reporting Month: May 2025", ("YELLOW_FEVER", 2025, 22, "monthly")),
])
def test_classify_real_title_pages(text, expected):
    c = classify(text)
    assert (c.disease_code, c.epi_year, c.epi_week, c.period) == expected


def test_mpox_highlight_sentences():
    block = read_summary(content(MPOX_2024_W44), 2024, epi_week=44)
    assert block.method == "highlights"
    assert block.rows[0] == {"suspected": 42, "confirmed": 6, "deaths": 0,
                             "cum_suspected": 1484, "cum_confirmed": 124, "cum_deaths": 0}


def test_mpox_year_week_rows_ignore_previous_year():
    block = read_summary(content(MPOX_2023_W5), 2023, epi_week=5)
    assert block.method == "year_week_rows"
    assert block.rows[0] == {"suspected": 72, "confirmed": 7, "deaths": 0, "cfr_reported": 0.0,
                             "cum_suspected": 258, "cum_confirmed": 34, "cum_deaths": 1, "cum_cfr_reported": 2.9}


def test_cholera_unlabelled_tables_use_their_captions():
    block = read_summary(content(CHOLERA_2025_W9, CHOLERA_TABLES), 2025, epi_week=9)
    assert block.method.startswith("summary_table")
    row = block.rows[0]
    assert (row["suspected"], row["deaths"], row["cfr_reported"]) == (2, 0, 0.0)
    assert (row["cum_suspected"], row["cum_deaths"]) == (1149, 28)  # second table, read via its caption
    assert "confirmed" not in row  # "States Reporting Cases" must not be read as a case count


def test_cholera_caption_tables_from_text_only():
    """When pdfplumber finds no usable table, the captioned text lines are enough."""
    block = read_summary(content(CHOLERA_2025_W9), 2025, epi_week=9)
    row = block.rows[0]
    assert (row["suspected"], row["deaths"], row["cum_suspected"], row["cum_deaths"]) == (2, 0, 1149, 28)


def test_totals_without_a_this_week_heading_are_not_weekly():
    text = ("TITLE: 2018/2019 CEREBROSPINAL MENINGITIS OUTBREAK IN NIGERIA\nEPI-WEEK: 13\nDATE: March 27, 2019\n"
            "HIGHLIGHTS\nBeginning from 1st October, 2018 to 27th March, 2019, a total of 541 suspected cases have been reported")
    assert read_summary(content(text), 2019, epi_week=13) is None


def test_monthly_measles_infographic_is_detected():
    c = classify("MEASLES SITUATION REPORT\nSerial Number 07\nData as at July 31st 2024\nHIGHLIGHTS\n"
                 "SITUATION UPDATES\n‒ In July, 2024:\n# Jan - July (# New in July)")
    assert c.period == "monthly" and (c.epi_year, c.epi_week) == (2024, 31)


def test_csm_season_totals_are_not_stored_as_calendar_totals():
    block = read_summary(content("Cerebrospinal Meningitis Situation Report Epi Week: 14 2025", CSM_2025_TABLE), 2025, epi_week=14)
    row = block.rows[0]
    assert (row["suspected"], row["confirmed"], row["deaths"]) == (296, 25, 10)
    assert not any(k.startswith("cum_") for k in row)
    assert any("non-calendar" in e for e in block.errors)


def test_diphtheria_outbreak_totals_are_not_calendar_totals():
    block = read_summary(content(DIPHTHERIA_2025_W4), 2025, epi_week=4)
    assert block.rows[0] == {"suspected": 8, "confirmed": 6, "deaths": 0}


def test_measles_monthly_keeps_only_this_years_totals():
    block = read_summary(content(MEASLES_2026_SEPT, MEASLES_TABLE), 2026, epi_week=40, monthly=True)
    row = block.rows[0]
    assert (row["cum_suspected"], row["cum_confirmed"], row["cum_deaths"]) == (10894, 7681, 119)
    assert "suspected" not in row


@pytest.mark.parametrize("label,expected", [
    ("2026 Cumulative (week 44)", True), ("Cumulative (Epi week 40 - 14 of 2024/2025)", False),
    ("Cumulatively: Epi-Week 19, 2022 - Epi-Week 4, 2025", False), ("Jan to September 2025", False),
    ("Cumulative Summary (Weeks 1–44, 2024):", True),
])
def test_cumulative_basis(label, expected):
    year = 2026 if "2026" in label else (2024 if "2024):" in label else 2025)
    if "September 2025" in label:
        year = 2026
    assert cumulative_is_calendar(label, year) is expected
