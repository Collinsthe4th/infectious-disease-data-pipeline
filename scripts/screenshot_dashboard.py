"""Take screenshots of the running dashboard in a real browser (Playwright, Chromium).

    python scripts/screenshot_dashboard.py http://localhost:8501 OUT_DIR
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

base, out = sys.argv[1].rstrip("/"), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
PAGES = {"overview": "", "by_state": "By_state", "forecasts": "Forecasts", "pipeline_health": "Data_pipeline_health"}
with sync_playwright() as p:
    browser = p.chromium.launch()
    for scheme in ("dark", "light"):
        for width, label in ((1366, "desktop"), (390, "mobile")):
            page = browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
            for name, path in PAGES.items():
                if label == "mobile" and name != "overview":
                    continue
                page.goto(f"{base}/{path}", wait_until="networkidle")
                page.wait_for_timeout(6000)  # let Streamlit finish rendering and the web font load
                font = page.evaluate("() => getComputedStyle(document.querySelector('h1') || document.body).fontFamily")
                loaded = page.evaluate("() => document.fonts.check('16px \"Nunito\"')")
                print(f"{scheme} {label} {name}: font={font!r} loaded={loaded}")
                page.screenshot(path=str(out / f"{scheme}_{label}_{name}.png"), full_page=True)
                for css, part in ((".rk", "risk_panel"), (".rs", "risk_tiles"), (".cards", "report_cards")):
                    el = page.locator(css).first
                    if name == "overview" and el.count():
                        el.scroll_into_view_if_needed()
                        page.wait_for_timeout(500)
                        el.screenshot(path=str(out / f"{scheme}_{label}_{part}.png"))
            page.close()
    browser.close()
