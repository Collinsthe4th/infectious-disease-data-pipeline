"""Shared fixtures."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.common.text import normalise_alias

REF = Path(__file__).resolve().parents[1] / "data" / "reference"


@pytest.fixture(scope="session")
def alias_map() -> dict[str, str]:
    """Same mapping the database builds in ``seed_reference``."""
    states = list(csv.DictReader((REF / "nga_states.csv").open(encoding="utf-8")))
    by_name = {r["state_name"]: r["pcode"] for r in states}
    out = {"ng": "NG"}
    for r in states:
        for a in (r["state_name"], r["pcode"], f"{r['state_name']} state"):
            out[normalise_alias(a)] = r["pcode"]
    for r in csv.DictReader((REF / "nga_location_aliases.csv").open(encoding="utf-8")):
        out[normalise_alias(r["alias"])] = by_name[r["state_name"]]
    return out


@pytest.fixture(scope="session")
def denominators() -> dict[str, str]:
    from src.common.diseases import DISEASES

    return {code: d.cfr_denominator for code, d in DISEASES.items()}
