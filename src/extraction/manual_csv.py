"""Manual-entry CSV reader (inbox files and the demo generator)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED = {"disease_code", "epi_year", "epi_week"}
LOCATION_COLUMNS = ("pcode", "state", "location")


def read_manual_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = REQUIRED - set(df.columns)
    if missing:
        raise ValueError(f"manual CSV {path.name} is missing columns: {sorted(missing)}")
    loc = next((c for c in LOCATION_COLUMNS if c in df.columns), None)
    if loc is None:
        raise ValueError(f"manual CSV {path.name} needs one of {LOCATION_COLUMNS}")
    df = df.rename(columns={loc: "location_raw"})
    df["disease_code"] = df["disease_code"].str.strip().str.upper()
    return df
