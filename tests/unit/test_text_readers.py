"""Plain-text readers, checked against text copied from real NCDC reports."""

from src.extraction.text_readers import read_key_indicators, read_since_january, read_summary_blocks

MPOX_2022_W28 = """TITLE: UPDATE ON MONKEYPOX (MPX) IN NIGERIA
Table 1 – Key Indicators
Key Indicators Number
Total confirmed cases in Epi Week 28, 2022 16
Total suspected cases from January 1st to July 17th, 2022 (Epi week 1 to 28) 338
Total confirmed cases from January 1st to July 17th, 2022 (Epi week 1 to 28) 117
Total deaths from January 1st, 2022, to July 17th, 2022 (Epi week 1 to 28) 3
Total deaths Sept 2017 - July 17th 2022 11
Total confirmed cases in 2021 34
Grand total confirmed cases (Sept 2017 – July 17th, 2022) 343
• There were thirty-seven (37) new suspected cases reported in Epi week 28, 2022 (11th to 17th July
2022) from twenty-one (21) states"""

CHOLERA_2022_W26 = """Cholera Situation Report
MONTHLY EPIDEMIOLOGICAL REPORT 06
Table 1: Summary of current week (Epi week 26, 2022)
Deaths
Suspected (Suspected Case Fatality States LGAs
Cases cases) Ratio (%) Reporting Cases Reporting cases
23 0 0.0% 4 10
Table 2: Summary of monthly reported cases (Epi week 1- 26)
January 1 - 5 920 27 2.9% 20 47
Table 3: Cumulative summary from Epi week 1 - 21, 2022
Deaths
Suspected (Suspected Case Fatality States LGAs
Cases cases) Ratio (%) Reporting Cases Reporting cases
2,523 78 3.1% 31 160"""

CHOLERA_2021_MARCH = ("▪ As at March 28th, 2021, a total of 1,746 suspected cases including 50 deaths (CFR = 2.9%) have\n"
                      "been reported from eight states (Benue, Delta, Zamfara, Gombe, Bayelsa, Kogi, Sokoto and\n"
                      "Nasarawa) since the beginning of 2021")


def test_key_indicators_mpox_2022():
    assert read_key_indicators(MPOX_2022_W28, 2022, 28) == {
        "confirmed": 16, "cum_suspected": 338, "cum_confirmed": 117, "cum_deaths": 3, "suspected": 37}


def test_key_indicators_ignore_other_weeks_and_multi_year_totals():
    out = read_key_indicators(MPOX_2022_W28, 2022, 29)  # wrong week: no weekly figures
    assert "confirmed" not in out and "suspected" not in out
    assert out["cum_confirmed"] == 117  # never the Sept 2017 - 2022 grand total (343)


def test_summary_blocks_cholera_2022():
    assert read_summary_blocks(CHOLERA_2022_W26, 2022, 26) == {
        "suspected": 23, "deaths": 0, "cum_suspected": 2523, "cum_deaths": 78}
    assert "suspected" not in read_summary_blocks(CHOLERA_2022_W26, 2022, 25)


def test_since_january_needs_matching_year():
    assert read_since_january(CHOLERA_2021_MARCH, 2021) == {"cum_suspected": 1746, "cum_deaths": 50}
    assert read_since_january(CHOLERA_2021_MARCH, 2022) == {}
