"""Parser tests on synthetic PDFs that copy the NCDC situation-report layout."""

from pathlib import Path

import pytest

from src.demo.pdf_writer import write_sitrep_pdf
from src.extraction.extract import ExtractionError, extract_document, extract_pdf

STATES = [
    {"state": "Ondo", "suspected": 60, "confirmed": 5, "deaths": 2, "cum_suspected": 3000, "cum_confirmed": 440, "cum_deaths": 90},
    {"state": "Edo", "suspected": 40, "confirmed": 4, "deaths": 1, "cum_suspected": 2000, "cum_confirmed": 120, "cum_deaths": 30},
    {"state": "FCT", "suspected": 79, "confirmed": 4, "deaths": 1, "cum_suspected": 3968, "cum_confirmed": 626, "cum_deaths": 158},
]


@pytest.fixture
def lassa_pdf(tmp_path) -> Path:
    return write_sitrep_pdf(
        tmp_path / "lassa.pdf", disease_name="Lassa Fever", epi_year=2026, epi_week=44,
        national_week={"suspected": 179, "confirmed": 13, "deaths": 4},
        national_cum={"suspected": 8968, "confirmed": 1186, "deaths": 278}, states=STATES)


def test_reads_summary_and_state_table(lassa_pdf, alias_map):
    ex = extract_pdf({"sha256": "ab" * 32}, lassa_pdf, alias_map)
    assert ex.template_id == "lassa_sitrep_v1"
    assert (ex.epi_year, ex.epi_week, ex.disease_code) == (2026, 44, "LASSA")
    rows = ex.rows.set_index("location_raw")
    nat = rows.loc["National"]
    assert (nat.suspected, nat.confirmed, nat.deaths) == (179, 13, 4)
    assert (nat.cum_suspected, nat.cum_confirmed, nat.cum_deaths) == (8968, 1186, 278)
    assert nat.cfr_reported == 30.8
    assert rows.loc["FCT"].cum_confirmed == 626
    assert "Total" not in rows.index  # total row becomes the national row, not a state
    assert ex.report_code.startswith("NCDC-SITREP-LASSA-2026-W44-")


def test_other_disease_uses_generic_template(tmp_path, alias_map):
    pdf = write_sitrep_pdf(tmp_path / "cholera.pdf", disease_name="Cholera", epi_year=2024, epi_week=36,
                           national_week={"suspected": 1200, "confirmed": 40, "deaths": 30},
                           national_cum={"suspected": 9000, "confirmed": 300, "deaths": 250}, cfr_base="suspected")
    ex = extract_pdf({"sha256": "cd" * 32}, pdf, alias_map)
    assert ex.disease_code == "CHOLERA" and ex.template_id == "ncdc_sitrep_generic_v1"
    assert int(ex.rows.iloc[0]["suspected"]) == 1200


def test_scanned_or_empty_pdf_goes_to_manual_queue(tmp_path, alias_map):
    from reportlab.pdfgen import canvas

    path = tmp_path / "blank.pdf"
    c = canvas.Canvas(str(path))
    c.rect(10, 10, 100, 100)  # drawing only, no text layer
    c.save()
    with pytest.raises(ExtractionError) as err:
        extract_pdf({"sha256": "ef" * 32}, path, alias_map)
    assert err.value.manual


def test_not_a_pdf(tmp_path, alias_map):
    path = tmp_path / "fake.pdf"
    path.write_text("<html>not a pdf</html>")
    with pytest.raises(ExtractionError):
        extract_pdf({"sha256": "00" * 32}, path, alias_map)


def test_manual_csv_one_report_per_disease_week(tmp_path, alias_map):
    path = tmp_path / "entry.csv"
    path.write_text("disease_code,state,epi_year,epi_week,suspected,confirmed,deaths\n"
                    "lassa,Edo,2024,5,10,2,1\nLASSA,Ondo,2024,5,8,1,0\nCHOLERA,Borno,2024,5,30,1,1\n")
    reports = extract_document({"sha256": "11" * 32, "is_synthetic": False}, path, alias_map)
    assert sorted((r.disease_code, len(r.rows)) for r in reports) == [("CHOLERA", 1), ("LASSA", 2)]
    assert all(r.report_type == "manual_entry" for r in reports)
