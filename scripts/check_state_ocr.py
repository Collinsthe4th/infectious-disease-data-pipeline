"""Check the OCR reader for the Lassa state table (Table 3) on real NCDC reports.

Downloads a spread of Lassa fever situation reports, reads each one exactly as the
pipeline does, and prints whether the state table passed its checks (every column
adds up to the Total row and the confirmed total matches Table 1).

    python scripts/check_state_ocr.py [how-many]
"""

from __future__ import annotations

import csv
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.common.config import get_settings  # noqa: E402
from src.common.text import normalise_alias  # noqa: E402
from src.extraction.extract import extract_pdf  # noqa: E402
from src.ingestion.http import PoliteSession  # noqa: E402
from src.ingestion.ncdc_crawler import NcdcCrawler  # noqa: E402

REF = Path("data/reference")


def alias_map() -> dict[str, str]:
    states = {normalise_alias(r["state_name"]): r["pcode"] for r in csv.DictReader(open(REF / "nga_states.csv"))}
    amap = dict(states)
    for r in csv.DictReader(open(REF / "nga_location_aliases.csv")):
        pc = states.get(normalise_alias(r["state_name"]))
        if pc:
            amap[normalise_alias(r["alias"])] = pc
    amap["national"] = "NG"
    amap["nigeria"] = "NG"
    return amap


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    dump = Path(sys.argv[2]) if len(sys.argv) > 2 else None  # save page images/text of failures here
    if dump:
        dump.mkdir(parents=True, exist_ok=True)
    s = get_settings()
    crawler = NcdcCrawler(PoliteSession(user_agent=s.user_agent, delay_s=3, max_retries=1, backoff_base_s=5),
                          s.ncdc_base_url, Path("/tmp/raw"))
    listing = [e for e in crawler.discover(["LASSA"])]
    step = max(1, len(listing) // n)
    picks = listing[::step][:n]
    amap = alias_map()
    lines = ["| Report | State table | States | Notes |", "|---|---|---|---|"]
    passed = 0
    for e in picks:
        f = crawler.download(e, lambda h: False)
        t0 = time.time()
        try:
            ex = extract_pdf({"sha256": f.sha256, "listing_title": e.title, "disease_hint": "LASSA"}, f.path, amap)
        except Exception as exc:  # noqa: BLE001 - a broken PDF must not stop the check
            lines.append(f"| {e.title} | not read | 0 | {type(exc).__name__}: {str(exc)[:120]} |")
            continue
        states = ex.rows[ex.rows["location_raw"] != "National"]
        ok = len(states) > 0
        passed += ok
        note = "; ".join(x for x in ex.parse_errors if "state table" in x)[:300]
        nat = ex.rows[ex.rows["location_raw"] == "National"]
        nat_cc = nat["cum_confirmed"].iloc[0] if len(nat) and "cum_confirmed" in nat else "?"
        top = ""
        if ok:
            st = states.assign(cc=states["cum_confirmed"].astype(int)).sort_values("cc", ascending=False).head(3)
            top = ", ".join(f"{r.location_raw} {r.cc}" for r in st.itertuples())
        if dump and not ok:
            import pdfplumber

            from src.extraction.state_table_ocr import DPI, find_table_page
            stem = f"{ex.epi_year}_W{ex.epi_week:02d}"
            pno = find_table_page(f.path)
            with pdfplumber.open(f.path) as pdf:
                (dump / f"{stem}_pages.txt").write_text("\n\n".join(
                    f"=== page {i} ===\n{(p.extract_text() or '')[:600]}" for i, p in enumerate(pdf.pages, 1)),
                    encoding="utf-8")
                for i, p in enumerate(pdf.pages, 1):
                    if i == pno or (pno is None and "table 3" in (p.extract_text() or "").lower()):
                        p.to_image(resolution=DPI).save(dump / f"{stem}_p{i}.png")
        lines.append(f"| {ex.epi_year} W{ex.epi_week:02d} (national confirmed {nat_cc}) | "
                     f"{'used' if ok else 'not used'} | {len(states)} | {top or note} ({time.time() - t0:.0f}s) |")
        print(lines[-1], flush=True)
    lines.insert(0, f"**{passed} of {len(picks)} reports: state table read and passed every check.**\n")
    out = "\n".join(lines)
    print(out)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(os.environ["GITHUB_STEP_SUMMARY"]).write_text(out + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
