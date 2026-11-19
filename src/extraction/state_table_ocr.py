"""Read the Lassa fever state table (Table 3) when NCDC publishes it as a picture.

In the Lassa fever situation reports the state-by-state table is pasted into the
PDF as an image, so it has no text layer and the ordinary table readers see
nothing. This module reads it with OCR (Tesseract) one cell at a time:

1. Render the page at 300 dpi.
2. Find the table's left and right borders (long vertical lines) and place the
   eleven column boundaries. The layout has been the same since 2022, so the
   boundaries are fixed fractions of the table width, snapped to the faint grid
   lines wherever they can be seen.
3. Read the state names (one row each) from the "States" column.
4. Read each non-empty cell with Tesseract restricted to digits.
5. Check the result. Every column must add up to the table's own "Total" row,
   and the cumulative confirmed total must match the national figure from
   Table 1. If any check fails, no state rows are used: a single misread digit
   would otherwise put wrong numbers on the map.

Blank cells mean zero (the state is listed but had no cases in that column).
"""

from __future__ import annotations

import difflib
import io
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import numpy as np

log = logging.getLogger(__name__)

COLUMNS = ("wk_suspected", "wk_confirmed", "trend", "wk_probable", "wk_hcw", "wk_deaths",
           "cum_suspected", "cum_confirmed", "cum_probable", "cum_hcw", "cum_deaths")
NUMERIC = tuple(c for c in COLUMNS if c != "trend")
# Right edge of each column as a fraction of the table width, measured on the
# 2022-2026 reports (the States column ends at 0.132).
BOUNDARIES = (0.132, 0.210, 0.291, 0.348, 0.408, 0.455, 0.586, 0.680, 0.762, 0.830, 0.874, 1.0)
# How the columns map onto the pipeline's measures.
MEASURE_OF = {"wk_suspected": "suspected", "wk_confirmed": "confirmed", "wk_deaths": "deaths",
              "cum_suspected": "cum_suspected", "cum_confirmed": "cum_confirmed", "cum_deaths": "cum_deaths"}
DPI = 300


class OcrUnavailable(RuntimeError):
    """Tesseract is not installed on this machine."""


@dataclass
class StateTable:
    rows: dict[str, dict[str, int]]  # OCR name -> {column: value}
    total: dict[str, int]
    names: dict[str, str | None]  # OCR name -> matched alias (None when unknown)
    checks: dict[str, tuple[int, int | None, bool]] = field(default_factory=dict)  # column -> (sum, total, ok)
    problems: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)  # single-cell repairs confirmed by the Total row

    @property
    def ok(self) -> bool:
        return not self.problems


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


# --------------------------------------------------------------------------- #
#  Image helpers
# --------------------------------------------------------------------------- #

def _vertical_lines(gray: np.ndarray, scale: float) -> list[tuple[float, int]]:
    """Thin vertical lines: pixels darker than both neighbours a few pixels away."""
    a = gray.astype(int)
    k = max(2, round(3 * scale))
    left = np.full_like(a, 255)
    right = np.full_like(a, 255)
    left[:, k:] = a[:, :-k]
    right[:, :-k] = a[:, k:]
    counts = ((a < left - 12) & (a < right - 12)).sum(axis=0)
    h, w = a.shape
    groups: list[list[int]] = []
    for x in np.flatnonzero(counts > h * 0.12):
        if groups and x - groups[-1][-1] <= max(4, round(4 * scale)):
            groups[-1].append(int(x))
        else:
            groups.append([int(x)])
    return [(float(np.mean(g)), int(counts[g].max())) for g in groups]


def _tesseract(image, psm: int, whitelist: str | None = None, upscale: int = 1) -> list[dict]:
    from PIL import Image

    if upscale > 1:
        image = image.resize((image.width * upscale, image.height * upscale), Image.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, "PNG")
    cmd = ["tesseract", "stdin", "stdout", "--psm", str(psm)]
    if whitelist:
        cmd += ["-c", f"tessedit_char_whitelist={whitelist}"]
    out = subprocess.run(cmd + ["tsv"], input=buf.getvalue(), capture_output=True, check=False).stdout.decode()
    words = []
    for line in out.splitlines()[1:]:
        p = line.split("\t")
        if len(p) == 12 and p[11].strip() and float(p[10]) >= 0:
            x, y, w, h = (int(v) / upscale for v in p[6:10])
            words.append({"x": x, "y": y, "w": w, "h": h, "text": p[11].strip(), "conf": float(p[10]),
                          "line": (p[2], p[3], p[4])})
    return words


# --------------------------------------------------------------------------- #
#  Name matching
# --------------------------------------------------------------------------- #

def _squash(name: str) -> str:
    s = name.lower().replace("!", "i").replace("|", "i").replace("1", "i").replace("0", "o")
    return re.sub(r"[^a-z]", "", s)


def match_state(raw: str, alias_map: Mapping[str, str]) -> str | None:
    """Best alias for an OCR'd state name, or None. Spaces and common OCR slips are ignored."""
    keys = {_squash(a): a for a in alias_map}
    s = _squash(raw)
    if not s:
        return None
    # The row number in front of the name is sometimes read as one or two stray letters,
    # and OCR mixes up l/i and e/c ("Fet" for "Fct"). Try those variants, exact first.
    variants = []
    for cut in (0, 1, 2):
        base = s[cut:]
        if len(base) >= 3:
            variants += [base, base.replace("l", "i"), base.replace("e", "c")]
    for v in variants:
        if v in keys:
            return keys[v]
    for v in variants[:3]:
        best = difflib.get_close_matches(v, list(keys), n=2, cutoff=0.8)
        if best and (len(best) == 1 or difflib.SequenceMatcher(None, v, best[0]).ratio()
                     > difflib.SequenceMatcher(None, v, best[1]).ratio() + 0.05):
            return keys[best[0]]
    return None


# --------------------------------------------------------------------------- #
#  Reader
# --------------------------------------------------------------------------- #

def _treatments(cell, scale: float):
    from PIL import ImageOps

    base = 2 if scale < 1.5 else 1
    padded = ImageOps.expand(cell, border=int(10 * scale), fill=255)
    # Tight crop around the digits (dark pixels only, so grid lines and shading are dropped),
    # with a generous white margin: Tesseract reads isolated digits far better this way.
    a = np.asarray(cell)
    ys, xs = np.nonzero(a < 150)
    tight = padded
    if len(xs):
        tight = ImageOps.expand(cell.crop((max(xs.min() - 2, 0), max(ys.min() - 2, 0), xs.max() + 3, ys.max() + 3)),
                                border=int(10 * scale), fill=255)
    return (
        (tight, 7, base),
        (tight, 10, base),
        (padded, 7, base),
        (padded.point(lambda v: 0 if v < 170 else 255), 7, base * 2),
        (padded, 8, base * 2),
        (padded.point(lambda v: 0 if v < 200 else 255), 6, base * 2),
        (padded.point(lambda v: 0 if v < 140 else 255), 8, base * 3),
        (padded, 13, base * 3),
    )


def _read_number(cell, scale: float) -> int:
    """Digits in one cell, or -1 when there is ink but no reading. Tries a few image treatments."""
    for img, psm, up in _treatments(cell, scale)[:5]:
        text = "".join(wd["text"] for wd in _tesseract(img, psm=psm, whitelist="0123456789", upscale=up))
        if text.isdigit():
            return int(text)
    return -1


def _alternatives(cell, scale: float) -> set[int]:
    """Every reading any treatment gives for a cell (used only to repair a column that does not add up)."""
    out = set()
    for img, psm, up in _treatments(cell, scale):
        text = "".join(wd["text"] for wd in _tesseract(img, psm=psm, whitelist="0123456789", upscale=up))
        if text.isdigit():
            out.add(int(text))
    return out


def _repair_column(col: str, labels: list[str], values: list[dict[str, int]], cells: dict, scale: float,
                   total: int) -> str | None:
    """If changing exactly one cell to another reading OCR also produced makes the column add up to
    its Total, apply it and return a note. Otherwise leave the column alone (it will fail the check)."""
    fixes = []
    for j in range(len(values) - 1):
        cell = cells.get((j, col))
        if cell is None:
            continue
        current = max(values[j].get(col, 0), 0)
        others = sum(max(v.get(col, 0), 0) for k, v in enumerate(values[:-1]) if k != j)
        for alt in _alternatives(cell, scale):
            if alt != current and others + alt == total:
                fixes.append((j, current, alt))
    if len(fixes) != 1:
        return None
    j, old, new = fixes[0]
    values[j][col] = new
    return f"{col} for {labels[j]}: OCR read {old}, corrected to {new} (the only reading that matches the Total row)"


def read_table_image(image, alias_map: Mapping[str, str], national_cum_confirmed: int | None = None) -> StateTable:
    """Read Table 3 from a rendered page (PIL image)."""
    if not tesseract_available():
        raise OcrUnavailable("tesseract is not installed")
    pil = image.convert("L")
    gray = np.asarray(pil)
    h, w = gray.shape
    scale = h / 1754  # 1754 px = A4 at 150 dpi

    lines = _vertical_lines(gray, scale)
    strong = [x for x, c in lines if c > h * 0.45]
    if len(strong) < 4:
        return StateTable({}, {}, {}, problems=["could not find the table borders"])
    inner = [x for x in strong if strong[0] + 5 * scale < x < strong[-1] - 5 * scale]
    if len(inner) < 2:
        return StateTable({}, {}, {}, problems=["could not find the table borders"])
    left, right = inner[0], inner[-1]
    span = right - left
    faint = [x for x, _ in lines]
    edges = []
    for frac in BOUNDARIES:
        expected = left + frac * span
        near = [x for x in faint if abs(x - expected) <= span * 0.008]
        edges.append(min(near, key=lambda x: abs(x - expected)) if near else expected)

    # --- rows: state names in the first column
    words = _tesseract(pil.crop((int(left), 0, int(edges[0]), h)), psm=6)
    by_line: dict[tuple, list[dict]] = {}
    for word in words:
        by_line.setdefault(word["line"], []).append(word)
    lines_txt = sorted(
        ((min(x["y"] for x in ws), max(x["y"] + x["h"] for x in ws), " ".join(x["text"] for x in ws))
         for ws in by_line.values()))
    total_i = [i for i, r in enumerate(lines_txt) if re.search(r"t\s*o\s*t\s*a\s*l", r[2], re.I)]
    head_i = [i for i, r in enumerate(lines_txt) if re.search(r"states", r[2], re.I)]
    if not total_i:
        return StateTable({}, {}, {}, problems=["no 'Total' row found"])
    body = lines_txt[(head_i[0] + 1 if head_i else 0): total_i[-1] + 1]
    body = [r for r in body if re.search(r"[A-Za-z]{2}", r[2])]
    # Drop title/header lines OCR picked up above the first state row.
    first = next((i for i, r in enumerate(body) if match_state(r[2], alias_map)), None)
    if first is None:
        return StateTable({}, {}, {}, problems=["no state names recognised"])
    body = body[first:]
    if len(body) < 10:
        return StateTable({}, {}, {}, problems=[f"only {len(body)} rows found"])
    centres = [(r[0] + r[1]) / 2 for r in body]
    labels = [re.sub(r"^[\W\d_]+", "", r[2]).strip() for r in body]
    gap = float(np.median(np.diff(centres)))

    values: list[dict[str, int]] = []
    cells: dict[tuple[int, str], object] = {}
    for j, c in enumerate(centres):
        top = max(c - gap / 2, (centres[j - 1] + c) / 2 if j else c - gap / 2)
        bot = min(c + gap / 2, (centres[j + 1] + c) / 2 if j + 1 < len(centres) else c + gap / 2)
        y0, y1 = int(top + 1 * scale), int(bot - 1 * scale)
        row: dict[str, int] = {}
        for ci, col in enumerate(COLUMNS):
            if col == "trend":
                continue
            x0, x1 = int(edges[ci] + 3 * scale), int(edges[ci + 1] - 1 * scale)
            cell = gray[y0:y1, x0:x1]
            if (cell < 150).sum() < 8 * scale * scale:
                row[col] = 0
                continue
            from PIL import Image

            cells[(j, col)] = Image.fromarray(cell)
            row[col] = _read_number(cells[(j, col)], scale)
        values.append(row)

    total = values[-1]
    corrections = []
    for col in NUMERIC:
        t = total.get(col, -1)
        if t >= 0 and sum(max(v.get(col, 0), 0) for v in values[:-1]) != t:
            note = _repair_column(col, labels, values, cells, scale, t)
            if note:
                corrections.append(note)
    state_rows = dict(zip(labels[:-1], values[:-1]))
    table = StateTable(rows=state_rows, total=total, names={n: match_state(n, alias_map) for n in state_rows},
                       corrections=corrections)

    # --- checks
    for name, row in state_rows.items():
        bad = [c for c, v in row.items() if v < 0]
        if bad:
            table.problems.append(f"unreadable cell(s) for {name}: {', '.join(bad)}")
    if any(v < 0 for v in total.values()):
        table.problems.append("unreadable cell(s) in the Total row")
    for col in NUMERIC:
        s = sum(max(r.get(col, 0), 0) for r in state_rows.values())
        t = total.get(col)
        ok = t is not None and t >= 0 and s == t
        table.checks[col] = (s, t, ok)
        if not ok:
            table.problems.append(f"{col}: states add up to {s} but the Total row says {t}")
    if national_cum_confirmed is not None and total.get("cum_confirmed") != national_cum_confirmed:
        table.problems.append(f"Total confirmed {total.get('cum_confirmed')} does not match Table 1 "
                              f"({national_cum_confirmed})")
    unknown = [n for n, a in table.names.items() if a is None]
    if unknown:
        # Not fatal: NCDC sometimes lists a city (e.g. Jos). Those rows are left out of the state figures.
        log.info("state table rows not matched to a state: %s", unknown)
    seen: dict[str, str] = {}
    for n, a in table.names.items():
        if a is None:
            continue
        if alias_map[a] in seen.values():
            table.problems.append(f"two rows matched the same state ({n})")
        seen[n] = alias_map[a]
    return table


def find_table_page(pdf_path: Path) -> int | None:
    """1-based number of the page titled 'Table 3 ... suspected and confirmed cases'."""
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        for n, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").lower()
            if re.search(r"table\s*3\.?\s*weekly\s*and\s*cumulative", text):
                return n
    return None


def read_state_table_pdf(pdf_path: Path, alias_map: Mapping[str, str],
                         national_cum_confirmed: int | None = None) -> tuple[StateTable | None, int | None]:
    """Find, render and read Table 3. Returns (table, page) or (None, None) when there is no such page."""
    import pdfplumber

    page_no = find_table_page(pdf_path)
    if page_no is None:
        return None, None
    with pdfplumber.open(pdf_path) as pdf:
        image = pdf.pages[page_no - 1].to_image(resolution=DPI).original
    return read_table_image(image, alias_map, national_cum_confirmed), page_no


def to_rows(table: StateTable) -> list[dict]:
    """Pipeline rows (one per matched state) from a table that passed every check."""
    out = []
    for name, vals in table.rows.items():
        alias = table.names.get(name)
        if alias is None:
            continue
        row = {"location_raw": alias}
        for col, measure in MEASURE_OF.items():
            row[measure] = str(vals.get(col, 0))
        out.append(row)
    return out
