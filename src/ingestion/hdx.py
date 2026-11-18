"""HDX reference data: state p-codes and population (Table 3.2).

Uses the public HDX CKAN API, no key needed. ``refresh_population`` fills the
population columns of ``data/reference/nga_states.csv`` from the Nigeria
subnational population dataset (COD-PS). Run it once on a machine with
internet access, then commit the updated CSV.
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path

from src.ingestion.http import PoliteSession

log = logging.getLogger(__name__)

HDX_API = "https://data.humdata.org/api/3/action/package_show?id={dataset}"
POPULATION_DATASET = "cod-ps-nga"


def _find_resource(session: PoliteSession, dataset: str, keyword: str) -> str:
    meta = session.get(HDX_API.format(dataset=dataset)).json()
    for res in meta["result"]["resources"]:
        name = (res.get("name") or "").lower()
        if keyword in name and (res.get("format") or "").lower() == "csv":
            return res["url"]
    raise LookupError(f"no CSV resource containing {keyword!r} in HDX dataset {dataset}")


def refresh_population(session: PoliteSession, states_csv: Path) -> int:
    """Download admin-1 population and write it into the reference CSV."""
    url = _find_resource(session, POPULATION_DATASET, "admin1")
    text = session.get(url).text
    reader = csv.DictReader(io.StringIO(text))
    pcode_col = next(c for c in reader.fieldnames or [] if c.lower().startswith("adm1_pcode"))
    total_col = next(c for c in reader.fieldnames or [] if c.lower() in {"t_tl", "total", "pop_total"})
    population = {}
    for row in reader:
        if row[pcode_col].startswith("#"):
            continue  # HXL tag row
        population[row[pcode_col]] = int(float(row[total_col]))

    rows = list(csv.DictReader(states_csv.open(encoding="utf-8")))
    updated = 0
    for row in rows:
        if row["pcode"] in population:
            row["population"] = population[row["pcode"]]
            row["population_source"] = f"HDX {POPULATION_DATASET}"
            updated += 1
    with states_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    log.info("population updated for %d states", updated)
    return updated
