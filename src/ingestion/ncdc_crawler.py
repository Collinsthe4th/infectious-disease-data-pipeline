"""Crawler for NCDC disease situation reports (Layer 2).

Listing pages live at ``/diseases/sitreps/?cat=<id>&name=<title>``; each row
links to a PDF under ``/themes/common/files/sitreps/<md5>.pdf``. Row titles
carry the epi week ("... for Week 44") and sometimes an upload date
("... Week 45 | 081126"); the year is read later from the PDF itself.
Weekly Epidemiological Reports are listed at ``/reports/weekly`` (multi-disease).

The crawler is idempotent: each PDF is hashed and skipped if its SHA-256 is
already in ``raw.documents``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable, Iterator
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from src.common.diseases import DISEASES, Disease
from src.ingestion.hashing import sha256_bytes
from src.ingestion.http import DownloadError, PoliteSession

log = logging.getLogger(__name__)

WEEK_RE = re.compile(r"\bweek\s*(\d{1,2})\b", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(20[12]\d)\b")
UPLOAD_DATE_RE = re.compile(r"\|\s*(\d{6})\s*$")


@dataclass(frozen=True)
class ListingEntry:
    """One report row on an NCDC listing page."""

    disease_code: str | None
    title: str
    url: str
    epi_week: int | None
    year_hint: int | None
    listing_date: date | None

    @property
    def report_family(self) -> str:
        return "situation_report" if self.disease_code else "weekly_epi_report"


@dataclass(frozen=True)
class DownloadedFile:
    entry: ListingEntry
    sha256: str
    path: Path
    size: int


def listing_url(base_url: str, disease: Disease) -> str:
    name = f"An update of {disease.name} outbreak in Nigeria"
    return f"{base_url}/diseases/sitreps/?cat={disease.ncdc_category}&name={quote(name)}"


def parse_listing(html: str, page_url: str, disease_code: str | None) -> list[ListingEntry]:
    """Extract every PDF link (with its row title) from a listing page."""
    soup = BeautifulSoup(html, "html.parser")
    entries: list[ListingEntry] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href.lower().endswith(".pdf"):
            continue
        url = urljoin(page_url, href)
        if url in seen:
            continue
        seen.add(url)
        title = _row_title(anchor)
        week = WEEK_RE.search(title)
        year = YEAR_RE.search(title)
        entries.append(
            ListingEntry(
                disease_code=disease_code,
                title=title,
                url=url,
                epi_week=int(week.group(1)) if week else None,
                year_hint=int(year.group(1)) if year else None,
                listing_date=_parse_upload_date(title),
            )
        )
    return entries


def _row_title(anchor) -> str:
    """Prefer the text of the table row; fall back to the link text."""
    row = anchor.find_parent("tr")
    if row is not None:
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        texts = [c for c in cells if c and not c.isdigit() and not c.lower().endswith(".pdf")]
        if texts:
            return max(texts, key=len)
    return anchor.get_text(" ", strip=True) or anchor.get("title", "") or anchor["href"]


def _parse_upload_date(title: str) -> date | None:
    match = UPLOAD_DATE_RE.search(title)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d%m%y").date()
    except ValueError:
        return None


class NcdcCrawler:
    """Discover and download NCDC report PDFs into the raw layer."""

    def __init__(self, session: PoliteSession, base_url: str, raw_dir: Path) -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.raw_dir = raw_dir

    def discover(self, disease_codes: Iterable[str] | None = None) -> Iterator[ListingEntry]:
        """Yield listing entries for the requested diseases (default: all seven)."""
        for code in disease_codes or DISEASES:
            disease = DISEASES[code]
            if disease.ncdc_category is None:
                continue
            url = listing_url(self.base_url, disease)
            try:
                resp = self.session.get(url)
            except DownloadError as exc:
                log.error("listing page failed for %s: %s", code, exc)
                continue
            entries = parse_listing(resp.text, url, code)
            log.info("found %d reports for %s", len(entries), code)
            yield from entries

    def download(
        self, entry: ListingEntry, already_stored: Callable[[str], bool]
    ) -> DownloadedFile | None:
        """Download one PDF; return ``None`` if its hash is already stored."""
        resp = self.session.get(entry.url)
        content = resp.content
        if not content.startswith(b"%PDF"):
            raise DownloadError(f"not a PDF: {entry.url}")
        digest = sha256_bytes(content)
        if already_stored(digest):
            return None
        folder = self.raw_dir / "ncdc" / (entry.disease_code or "wer").lower()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{digest[:16]}.pdf"
        path.write_bytes(content)  # raw files are written once and never modified
        return DownloadedFile(entry=entry, sha256=digest, path=path, size=len(content))
