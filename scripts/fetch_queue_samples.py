"""Download a spread of reports that the parser could not read (manual queue) for each disease,
so their layouts can be studied and the reading templates extended.

    python scripts/fetch_queue_samples.py OUT_DIR [per_disease]
    python scripts/fetch_queue_samples.py OUT_DIR sha:<prefix>,<prefix>   (specific files, for checking a figure)
Needs DATABASE_URL (read-only use). Writes the PDFs and an index.csv to OUT_DIR.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.common.config import get_settings  # noqa: E402
from src.ingestion.http import PoliteSession  # noqa: E402
from src.storage.db import get_engine  # noqa: E402

out = Path(sys.argv[1])
per = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 6
out.mkdir(parents=True, exist_ok=True)
s = get_settings()
session = PoliteSession(user_agent=s.user_agent, delay_s=2, timeout_s=60, max_retries=2, backoff_base_s=5)
only = [p for p in (sys.argv[2][4:].split(",") if len(sys.argv) > 2 and sys.argv[2].startswith("sha:") else []) if p]
if only:
    per = 10_000
with get_engine().connect() as conn:
    docs = conn.execute(text("""SELECT disease_hint, listing_title, source_url, sha256, status_reason
          FROM raw.documents WHERE sha256 LIKE ANY(:p)"""), {"p": [f"{x}%" for x in only]}).mappings().all() if only else \
        conn.execute(text("""
        SELECT disease_hint, listing_title, source_url, sha256, status_reason
          FROM raw.documents
         WHERE NOT is_synthetic AND disease_hint <> 'LASSA' AND source_url IS NOT NULL
           AND processing_status IN ('manual_queue', 'parse_failed')
           AND status_reason NOT LIKE 'outside study period%'
         ORDER BY disease_hint, listing_title""")).mappings().all()
rows = []
by = {}
for d in docs:
    by.setdefault(d["disease_hint"], []).append(d)
for disease, items in by.items():
    step = max(1, len(items) // per)
    for d in items[::step][:per]:
        name = f"{disease}_{d['sha256'][:8]}.pdf"
        try:
            (out / name).write_bytes(session.get(d["source_url"]).content)
        except Exception as exc:  # noqa: BLE001
            print("download failed", d["source_url"], exc)
            continue
        rows.append({"file": name, "disease": disease, "title": d["listing_title"], "reason": d["status_reason"],
                     "url": d["source_url"]})
        print(name, d["listing_title"])
with (out / "index.csv").open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["file", "disease", "title", "reason", "url"])
    w.writeheader()
    w.writerows(rows)
