"""Manual inbox: files placed by hand in ``data/inbox`` (Section 3.4.3).

PDFs that the crawler could not fetch, and CSV files for manual entry,
are moved into the raw layer and then processed exactly like crawled files.
Manual-entry CSV columns: disease_code, state (or pcode), epi_year, epi_week,
suspected, confirmed, deaths and optionally the cum_* and cfr_reported columns.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from src.ingestion.hashing import sha256_file

ACCEPTED_SUFFIXES = {".pdf": "application/pdf", ".csv": "text/csv"}


@dataclass(frozen=True)
class InboxFile:
    source_path: Path
    sha256: str
    content_type: str
    is_synthetic: bool


def scan_inbox(inbox_dir: Path) -> list[InboxFile]:
    """List acceptable files in the inbox, oldest first."""
    if not inbox_dir.exists():
        return []
    files = sorted(
        (p for p in inbox_dir.iterdir() if p.is_file() and p.suffix.lower() in ACCEPTED_SUFFIXES),
        key=lambda p: (p.stat().st_mtime, p.name),
    )
    return [
        InboxFile(
            source_path=p,
            sha256=sha256_file(p),
            content_type=ACCEPTED_SUFFIXES[p.suffix.lower()],
            is_synthetic=p.name.startswith("SYNTHETIC_"),
        )
        for p in files
    ]


def move_to_raw(item: InboxFile, raw_dir: Path) -> Path:
    """Move an inbox file into ``data/raw/manual_inbox`` under a hash-based name."""
    folder = raw_dir / "manual_inbox"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{item.sha256[:16]}_{item.source_path.name}"
    shutil.move(str(item.source_path), target)
    return target
