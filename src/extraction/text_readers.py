"""Readers for figures printed as plain text rather than as tables.

Some NCDC reports put their headline numbers in text the table readers cannot
see: a "Key Indicators" list (mpox, 2022), a "Summary of current week" block
whose numbers sit on a line of their own (cholera monthly reports, 2022), or a
sentence giving the total since the start of the year (cholera, 2021). Each
reader only accepts a figure when its label says exactly which period it
covers; anything ambiguous is left out.
"""

from __future__ import annotations

import re

from src.extraction.standardise import clean_count

NUM = r"(\d[\d,]*)"


def _n(text: str) -> int:
    return int(clean_count(text))


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def read_key_indicators(text: str, epi_year: int, epi_week: int) -> dict:
    """Mpox-style 'Table 1 - Key Indicators' list, one 'label  number' per line."""
    out: dict = {}
    for line in text.splitlines():
        line = " ".join(line.split())
        m = re.match(rf"^(.*?)\s+{NUM}$", line)
        if not m:
            continue
        label, value = m.group(1).lower(), _n(m.group(2))
        this_week = re.search(rf"in epi[\s-]*week\s*0?{epi_week}\b,?\s*{epi_year}", label)
        since_jan = re.search(rf"from january 1(?:st)?,? (?:{epi_year},? )?to .*{epi_year}", label) or \
            re.search(rf"\(epi week 0?1 to 0?{epi_week}\)", label)
        if label.startswith("total confirmed cases") and this_week:
            out.setdefault("confirmed", value)
        elif label.startswith("total suspected cases") and this_week:
            out.setdefault("suspected", value)
        elif label.startswith("total deaths") and this_week:
            out.setdefault("deaths", value)
        elif label.startswith("total suspected cases") and since_jan:
            out.setdefault("cum_suspected", value)
        elif label.startswith("total confirmed cases") and since_jan:
            out.setdefault("cum_confirmed", value)
        elif label.startswith("total deaths") and since_jan:
            out.setdefault("cum_deaths", value)
    if "suspected" not in out:
        # '... (37) new suspected cases reported in Epi week 28, 2022' / '(111) new suspected cases were reported in Epi week 46'
        m = re.search(rf"\({NUM}\)\s*new\s+suspected\s+cases\s+(?:were\s+)?reported\s+in\s+epi\s*week\s*0?{epi_week}\b",
                      _squash(text), re.I)
        if m:
            out["suspected"] = _n(m.group(1))
    return out


def _numbers_after(lines: list[str], start: int, look: int = 8) -> list[str] | None:
    """The first line after ``start`` made only of numbers and percentages."""
    for line in lines[start + 1: start + 1 + look]:
        tokens = line.split()
        if len(tokens) >= 3 and all(re.fullmatch(r"\d[\d,]*(?:\.\d+)?%?", t) for t in tokens):
            return tokens
    return None


def read_summary_blocks(text: str, epi_year: int, epi_week: int) -> dict:
    """Cholera monthly-format blocks:

        Table 1: Summary of current week (Epi week 26, 2022)
        Suspected  Deaths (Suspected cases)  Case Fatality Ratio (%)  States Reporting  LGAs Reporting
        23 0 0.0% 4 10

    The numbers come in the order suspected, deaths, CFR, states, LGAs; only the
    first two are used, and only when the CFR sits in third place (a check that
    the columns are the expected ones).
    """
    out: dict = {}
    lines = [" ".join(line.split()) for line in text.splitlines()]
    for i, line in enumerate(lines):
        low = line.lower()
        weekly = re.search(rf"summary of current week.*week\s*0?{epi_week}\b.*{epi_year}", low)
        cumulative = re.search(rf"cumulative summary.*{epi_year}", low)
        if not (weekly or cumulative):
            continue
        nums = _numbers_after(lines, i)
        if not nums or not nums[2].endswith("%"):
            continue
        susp, deaths = _n(nums[0]), _n(nums[1])
        if weekly:
            out.setdefault("suspected", susp)
            out.setdefault("deaths", deaths)
        else:
            out.setdefault("cum_suspected", susp)
            out.setdefault("cum_deaths", deaths)
    return out


def read_since_january(text: str, epi_year: int) -> dict:
    """'a total of 1,746 suspected cases including 50 deaths (CFR = 2.9%) have been reported from
    eight states ... since the beginning of 2021'."""
    m = re.search(rf"total\s+of\s+{NUM}\s+suspected\s+cases\s+including\s+{NUM}\s+deaths.{{0,400}}?"
                  rf"since\s+the\s+beginning\s+of\s+(\d{{4}})", _squash(text), re.I)
    if m and int(m.group(3)) == epi_year:
        return {"cum_suspected": _n(m.group(1)), "cum_deaths": _n(m.group(2))}
    return {}
