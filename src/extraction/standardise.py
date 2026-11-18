"""Header, number and place-name standardisation (Section 3.4.4, steps 4-5)."""

from __future__ import annotations

import re
from typing import Iterable, Mapping, Optional

# Canonical variable names (Table 3.3) and the header words that map to them.
# Checked in order; the first match wins, so more specific patterns go first.
_HEADER_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cfr", (r"\bcfr\b", r"fatality")),
    # ignored columns: counts of places or people other than cases, percentages
    ("affected", (r"affected", r"\bhcws?\b", r"health\s*care\s*workers?", r"reporting\s*cases", r"\blgas?\b",
                  r"%", r"sample", r"discarded", r"pending", r"admission", r"contacts?")),
    ("deaths", (r"death",)),
    ("probable", (r"probable",)),
    ("confirmed", (r"confirm",)),
    ("suspected", (r"suspect", r"^#?\s*(total\s*)?(number\s*of\s*)?cases?$", r"total\s*(number\s*of\s*)?cases")),
    ("location", (r"\bstates?\b", r"\blocation\b", r"\bjurisdiction\b")),
    ("lga", (r"\blgas?\b",)),
    ("period", (r"reporting\s*period", r"\bperiod\b")),
)

_CUMULATIVE_WORDS = re.compile(r"cumulative|\btotal\b|year[-\s]*to[-\s]*date|\bytd\b|20\d\d\s*$", re.I)
_WEEKLY_WORDS = re.compile(r"current\s*week|this\s*week|\bweek\s*\d+|\bweekly\b|\bnew\b", re.I)

_FOOTNOTE_CHARS = re.compile(r"[*†‡§¹²³⁴⁵⁶⁷⁸⁹⁰#^]+")
_DASHES = {"-", "–", "—", "−", "nil", "n/a", "na", "nr", "none", "--", "…", "..."}


def canonical_header(text: str) -> Optional[str]:
    """Map a raw table header to a variable name, or ``None`` if unknown."""
    cleaned = " ".join(str(text or "").replace("\n", " ").split()).lower()
    for name, patterns in _HEADER_RULES:
        if any(re.search(p, cleaned) for p in patterns):
            return name
    return None


def header_period(text: str) -> str:
    """'cumulative', 'weekly' or 'unknown' for a column header."""
    cleaned = " ".join(str(text or "").split())
    if _CUMULATIVE_WORDS.search(cleaned):
        return "cumulative"
    if _WEEKLY_WORDS.search(cleaned):
        return "weekly"
    return "unknown"


def clean_count(value: object, *, dash_is_zero: bool = False) -> Optional[int]:
    """Parse a printed count.

    ``"1,234"`` -> 1234, ``"12*"`` -> 12, ``"-"`` / ``""`` -> None (missing,
    never zero) unless the template says the report prints a dash for zero.
    Raises ``ValueError`` for text that is not a whole number, so the row can
    be quarantined under rule 2 instead of silently becoming wrong.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"not a count: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value:  # NaN
            return None
        if not value.is_integer():
            raise ValueError(f"not a whole number: {value!r}")
        return int(value)
    text = _FOOTNOTE_CHARS.sub("", str(value)).strip().lower()
    text = text.replace(" ", " ")
    if text == "":
        return None
    if text in _DASHES:
        return 0 if dash_is_zero else None
    text = re.sub(r"(?<=\d)[,\s](?=\d{3}\b)", "", text)
    text = re.sub(r"\(.*?\)", "", text).strip()  # "12 (3%)" -> "12"
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.0+", text):
        return int(float(text))
    raise ValueError(f"not a whole number: {value!r}")


def clean_percent(value: object) -> Optional[float]:
    """Parse a printed percentage such as ``"30.8%"``; ``None`` if missing."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return None if value != value else round(float(value), 2)
    text = _FOOTNOTE_CHARS.sub("", str(value)).strip().lower().replace("%", "").replace(",", ".")
    if text == "" or text in _DASHES:
        return None
    try:
        return round(float(text), 2)
    except ValueError:
        return None


def normalise_place(name: object) -> str:
    """Key used for alias lookup: lower case, single spaces, no punctuation noise."""
    text = _FOOTNOTE_CHARS.sub("", str(name or ""))
    text = re.sub(r"\s+", " ", text.replace("\n", " ")).strip().lower()
    return text.rstrip(".:")


NATIONAL_NAMES = {"total", "national", "nigeria", "national total", "grand total", "total (national)"}


def resolve_location(name: object, alias_map: Mapping[str, str]) -> Optional[str]:
    """Return the HDX p-code for a printed place name, or ``None`` (rule 9)."""
    key = normalise_place(name)
    if not key:
        return None
    if key in NATIONAL_NAMES:
        return alias_map.get("ng", "NG")
    if key in alias_map:
        return alias_map[key]
    stripped = re.sub(r"\s+state$", "", key)
    return alias_map.get(stripped)


def is_total_label(name: object) -> bool:
    return normalise_place(name) in NATIONAL_NAMES


def first_matching(values: Iterable[str], pattern: str) -> Optional[str]:
    rx = re.compile(pattern, re.I)
    return next((v for v in values if rx.search(v)), None)
