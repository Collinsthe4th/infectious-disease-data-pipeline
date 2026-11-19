"""Download the reference data the map needs and check it against data/reference/nga_states.csv.

* State populations from the HDX Nigeria subnational population dataset (cod-ps-nga),
  matched by HDX p-code, with the state names checked against ours.
* State boundaries (ADM1, simplified) from geoBoundaries, written to
  data/reference/nga_admin1.geojson with our p-codes as the join key.

Run by the 'Update reference data' GitHub workflow (needs internet).
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.common.text import normalise_alias  # noqa: E402

REF = Path(__file__).resolve().parents[1] / "data" / "reference"
UA = {"User-Agent": "MivaThesisDiseasePipeline/0.1 (+https://github.com/Collinsthe4th/infectious-disease-data-pipeline)"}


def alias_map() -> dict[str, str]:
    states = list(csv.DictReader((REF / "nga_states.csv").open(encoding="utf-8")))
    by_name = {r["state_name"]: r["pcode"] for r in states}
    out = {}
    for r in states:
        for a in (r["state_name"], f"{r['state_name']} state", r["pcode"]):
            out[normalise_alias(a)] = r["pcode"]
    for r in csv.DictReader((REF / "nga_location_aliases.csv").open(encoding="utf-8")):
        out[normalise_alias(r["alias"])] = by_name[r["state_name"]]
    return out


def update_population() -> None:
    meta = requests.get("https://data.humdata.org/api/3/action/package_show?id=cod-ps-nga", headers=UA, timeout=60).json()
    pkg = meta["result"]
    print("HDX dataset:", pkg.get("title"), "| reference period:", pkg.get("dataset_date"))
    year_match = re.search(r"(20\d\d)", str(pkg.get("dataset_date", "")))
    resources = [r for r in pkg["resources"] if (r.get("format") or "").lower() == "csv"]
    for r in resources:
        print("  resource:", r.get("name"))
    pick = next((r for r in resources if re.search(r"adm(in)?1", (r.get("name") or "") + r.get("url", ""), re.I)), None)
    if pick is None:
        raise SystemExit("no admin-1 CSV in cod-ps-nga")
    text = requests.get(pick["url"], headers=UA, timeout=120).text
    rows = list(csv.DictReader(io.StringIO(text)))
    cols = rows[0].keys()
    pcode_col = next(c for c in cols if re.fullmatch(r"adm1_?pcode", c, re.I))
    name_col = next((c for c in cols if re.fullmatch(r"adm1_?(en|name)", c, re.I)), None)
    total_col = next(c for c in cols if c.upper() in {"T_TL", "TOTAL", "POP_TOTAL", "T_TL_2020", "T_TL_2022"} or c.upper().startswith("T_TL"))
    year_col = next((c for c in cols if c.lower() in {"year", "reference_year"}), None)
    print(f"  using {pick['name']}: pcode={pcode_col} name={name_col} total={total_col}")

    ours = list(csv.DictReader((REF / "nga_states.csv").open(encoding="utf-8")))
    by_pcode = {r["pcode"]: r for r in ours}
    mismatches = 0
    for row in rows:
        pc = row[pcode_col].strip()
        if pc.startswith("#") or pc not in by_pcode:
            continue
        if name_col and normalise_alias(row[name_col]) != normalise_alias(by_pcode[pc]["state_name"]) and \
                alias_map().get(normalise_alias(row[name_col])) != pc:
            print(f"  NAME MISMATCH {pc}: HDX={row[name_col]!r} ours={by_pcode[pc]['state_name']!r}")
            mismatches += 1
        by_pcode[pc]["population"] = int(float(row[total_col]))
        by_pcode[pc]["population_year"] = row.get(year_col) if year_col else (year_match.group(1) if year_match else "")
        by_pcode[pc]["population_source"] = f"HDX cod-ps-nga ({pick['name']})"
    if mismatches:
        raise SystemExit(f"{mismatches} p-code/name mismatches: fix nga_states.csv first")
    filled = sum(1 for r in ours if r["population"])
    with (REF / "nga_states.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(ours[0].keys()))
        w.writeheader()
        w.writerows(ours)
    print(f"population filled for {filled}/37 states")


def round_coords(obj, nd=3):
    if isinstance(obj, list):
        return [round_coords(x, nd) for x in obj] if obj and isinstance(obj[0], (list, tuple)) else [round(v, nd) for v in obj]
    return obj


def _signed_area(ring) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1])) / 2


def rewind(geom: dict) -> dict:
    """Outer rings clockwise, holes anticlockwise: the order Plotly (d3-geo) expects.

    geoBoundaries ships the opposite (GeoJSON RFC 7946) order; Plotly then fills
    "the whole globe except the state" and the map looks blank."""
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    fixed = []
    for poly in polys:
        rings = []
        for i, ring in enumerate(poly):
            clockwise = _signed_area(ring) < 0
            want_clockwise = i == 0
            rings.append(ring if clockwise == want_clockwise else ring[::-1])
        fixed.append(rings)
    return {"type": geom["type"], "coordinates": fixed[0] if geom["type"] == "Polygon" else fixed}


def update_boundaries() -> None:
    api = requests.get("https://www.geoboundaries.org/api/current/gbOpen/NGA/ADM1/", headers=UA, timeout=60).json()
    url = api.get("simplifiedGeometryGeoJSON") or api["gjDownloadURL"]
    print("geoBoundaries:", api.get("boundarySource"), api.get("boundaryYearRepresented"), url)
    gj = requests.get(url, headers=UA, timeout=180).json()
    amap = alias_map()
    features, missing = [], []
    for f in gj["features"]:
        name = f["properties"].get("shapeName", "")
        pc = amap.get(normalise_alias(name)) or amap.get(normalise_alias(re.sub(r"\s+state$", "", name, flags=re.I)))
        if not pc:
            missing.append(name)
            continue
        geom = f["geometry"]
        geom = rewind({"type": geom["type"], "coordinates": round_coords(geom["coordinates"])})
        features.append({"type": "Feature", "properties": {"pcode": pc, "shapeName": name}, "geometry": geom})
    if missing:
        raise SystemExit(f"unmatched boundary names: {missing}")
    pcs = {f["properties"]["pcode"] for f in features}
    print(f"{len(features)} features, {len(pcs)} states matched")
    out = {"type": "FeatureCollection",
           "source": f"geoBoundaries gbOpen NGA ADM1 ({api.get('boundarySource')}), CC BY 4.0",
           "features": features}
    (REF / "nga_admin1.geojson").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    update_population()
    update_boundaries()
