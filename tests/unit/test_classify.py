from datetime import date

from src.extraction.classify import classify, parse_date_range


def test_real_lassa_title_page():
    # text as it appears in the NCDC Lassa fever report for week 44, 2026
    text = ("Lassa Fever Situation Report\nEpi Week 44: 26th October – 1st November 2026\n"
            "Lassa Fever Situation Report Epi Week: 44 2026")
    c = classify(text)
    assert (c.report_type, c.disease_code, c.epi_year, c.epi_week) == ("situation_report", "LASSA", 2026, 44)
    assert (c.printed_start, c.printed_end) == (date(2026, 10, 26), date(2026, 11, 1))


def test_week_one_spans_new_year():
    c = classify("Cholera Situation Report\nEpi Week 1: 29th December – 4th January 2026")
    assert (c.disease_code, c.epi_year, c.epi_week) == ("CHOLERA", 2026, 1)
    assert c.printed_start == date(2025, 12, 29)


def test_listing_title_supplies_week_when_title_page_lacks_it():
    c = classify("An Update of Diphtheria Outbreak in Nigeria\n 2nd - 8th December, 2024",
                 listing_title="An Update of Diphtheria Outbreak in Nigeria for Week 49")
    assert c.disease_code == "DIPHTHERIA" and c.epi_week == 49 and c.epi_year == 2024


def test_mpox_old_name_and_weekly_report():
    assert classify("Monkeypox Situation Report Epi Week 10, 2022").disease_code == "MPOX"
    wer = classify("Weekly Epidemiological Report\nEpi week 12: 18th – 24th March 2024")
    assert wer.report_type == "weekly_epi_report" and wer.disease_code is None


def test_parse_date_range_rejects_long_spans():
    assert parse_date_range("1st January – 30th March 2024") is None
