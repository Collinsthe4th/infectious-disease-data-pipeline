"""PDF access: text layer check, page text and table extraction.

pdfplumber is used first because NCDC tables usually have ruling lines;
Camelot (stream mode) is the fallback for tables without lines
(Section 3.4.4, step 3). Camelot is optional at import time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

log = logging.getLogger(__name__)

Table = list[list[str]]


@dataclass
class PdfContent:
    path: Path
    page_texts: list[str]
    tables: list[tuple[int, Table]] = field(default_factory=list)  # (1-based page, rows)
    engine: str = "pdfplumber"

    @property
    def page_count(self) -> int:
        return len(self.page_texts)

    @property
    def has_text_layer(self) -> bool:
        return sum(len(t.strip()) for t in self.page_texts) > 50

    @property
    def full_text(self) -> str:
        return "\n".join(self.page_texts)

    def head_text(self, pages: int = 2) -> str:
        return "\n".join(self.page_texts[:pages])


class NotAPdfError(ValueError):
    pass


def is_pdf(path: Path) -> bool:
    with path.open("rb") as fh:
        return fh.read(5) == b"%PDF-"


def read_pdf(path: Path, *, max_pages: int | None = None) -> PdfContent:
    """Read text and ruled tables from every page with pdfplumber."""
    if not is_pdf(path):
        raise NotAPdfError(f"{path.name} is not a valid PDF")
    texts: list[str] = []
    tables: list[tuple[int, Table]] = []
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            if max_pages and number > max_pages:
                break
            texts.append(page.extract_text() or "")
            for raw in page.extract_tables():
                rows = [[_cell(c) for c in row] for row in raw if row and any(c for c in row)]
                if rows:
                    tables.append((number, rows))
    return PdfContent(path=path, page_texts=texts, tables=tables)


def camelot_tables(path: Path, pages: str = "1-end") -> list[tuple[int, Table]]:
    """Fallback for tables without ruling lines. Returns [] if Camelot is unavailable."""
    try:
        import camelot  # type: ignore
    except Exception:  # pragma: no cover - optional dependency
        log.info("camelot not installed; skipping stream-mode fallback")
        return []
    try:
        found = camelot.read_pdf(str(path), pages=pages, flavor="stream")
    except Exception as exc:  # camelot raises many types on odd PDFs
        log.warning("camelot failed on %s: %s", path.name, exc)
        return []
    out: list[tuple[int, Table]] = []
    for table in found:
        rows = [[_cell(c) for c in row] for row in table.df.values.tolist()]
        out.append((int(table.page), [r for r in rows if any(r)]))
    return out


def _cell(value: object) -> str:
    return " ".join(str(value).split()) if value is not None else ""
