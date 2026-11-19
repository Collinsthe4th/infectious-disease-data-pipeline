"""Dashboard pages must load for diseases with sparse real-world data.

Real NCDC data differs from the demo: measles and yellow fever reports are
monthly (only year-to-date totals), and some diseases have very few weeks.
This test (named zz_ so it runs last) reshapes the demo data that way and
opens every page for those diseases.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")
from sqlalchemy import text  # noqa: E402

DASH = Path(__file__).resolve().parents[2] / "dashboard"
PAGES = [DASH / "app.py", *sorted((DASH / "pages").glob("*.py"))]


@pytest.fixture(scope="module")
def sparse_data(engine, demo_run):
    with engine.begin() as conn:
        # measles: running totals only, like the monthly reports
        conn.execute(text("""UPDATE curated.fact_weekly_cases f SET suspected = NULL, confirmed = NULL, deaths = NULL,
                                    cfr_pct = NULL
                               FROM curated.dim_disease d WHERE d.disease_id = f.disease_id AND d.disease_code = 'MEASLES'"""))
        # yellow fever: a single week left
        conn.execute(text("""DELETE FROM curated.fact_weekly_cases f USING curated.dim_disease d
                              WHERE d.disease_id = f.disease_id AND d.disease_code = 'YELLOW_FEVER'
                                AND f.week_id <> (SELECT max(week_id) FROM curated.fact_weekly_cases)"""))
        conn.execute(text("""DELETE FROM analytics.stl_component s USING curated.dim_disease d
                              WHERE d.disease_id = s.disease_id AND d.disease_code IN ('MEASLES', 'YELLOW_FEVER')"""))
    return True


@pytest.mark.parametrize("disease", ["Measles", "Yellow fever", "Cholera"])
@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_loads_for_sparse_disease(page, disease, sparse_data):
    testing = pytest.importorskip("streamlit.testing.v1")
    sys.path.insert(0, str(DASH))
    at = testing.AppTest.from_file(str(page), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    boxes = [s for s in at.selectbox if s.label == "Disease"]
    if boxes:
        boxes[0].set_value(disease).run()
        assert not at.exception, [e.value for e in at.exception]
        assert not at.error, [e.value for e in at.error]


def test_scorecard_shows_every_disease(sparse_data):
    testing = pytest.importorskip("streamlit.testing.v1")
    sys.path.insert(0, str(DASH))
    at = testing.AppTest.from_file(str(DASH / "app.py"), default_timeout=120).run()
    html = " ".join(m.value for m in at.markdown)
    for name in ("Lassa fever", "Cholera", "Measles", "Yellow fever", "Mpox", "Diphtheria", "Cerebrospinal meningitis"):
        assert name in html, name
    assert "No recent weekly data" in html or "Low" in html
