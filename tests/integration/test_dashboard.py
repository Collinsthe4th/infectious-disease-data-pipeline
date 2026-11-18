"""Every dashboard page renders without an exception against the demo database."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

DASH = Path(__file__).resolve().parents[2] / "dashboard"
PAGES = [DASH / "app.py", *sorted((DASH / "pages").glob("*.py"))]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_renders(page, demo_run):
    streamlit_testing = pytest.importorskip("streamlit.testing.v1")
    sys.path.insert(0, str(DASH))
    at = streamlit_testing.AppTest.from_file(str(page), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
