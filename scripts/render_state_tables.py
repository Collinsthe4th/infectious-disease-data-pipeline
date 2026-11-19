"""Research helper: render the pages of real Lassa reports that hold the state table (Table 3)
as PNG images and run OCR on them, so the layout can be studied. Output: research/samples/."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.common.config import get_settings  # noqa: E402
from src.ingestion.http import PoliteSession  # noqa: E402
from src.ingestion.ncdc_crawler import NcdcCrawler  # noqa: E402

OUT = Path("research/samples")
OUT.mkdir(parents=True, exist_ok=True)
s = get_settings()
crawler = NcdcCrawler(PoliteSession(user_agent=s.user_agent, delay_s=3, max_retries=1, backoff_base_s=5), s.ncdc_base_url,
                      Path("/tmp/raw"))
listing = list(crawler.discover(["LASSA"]))
picks = [listing[i] for i in (0, 20, 45, 100, 150, 200) if i < len(listing)]
for e in picks:
    f = crawler.download(e, lambda h: False)
    stem = f"lassa_{e.epi_week:02d}_{f.sha256[:8]}"
    with pdfplumber.open(f.path) as pdf:
        for n, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if "Table 3" in text or "Table3" in text:
                png = OUT / f"{stem}_p{n}.png"
                page.to_image(resolution=150).save(png)
                tsv = subprocess.run(["tesseract", str(png), "stdout", "--psm", "6"], capture_output=True, text=True).stdout
                (OUT / f"{stem}_p{n}.txt").write_text(f"{e.title}\n{e.url}\n\n{tsv}", encoding="utf-8")
                print("saved", png)
