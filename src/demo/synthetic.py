"""Generate a synthetic but realistic 2021-2026 dataset for the demo.

Nothing here is real NCDC data. Every file name starts with ``SYNTHETIC_``,
which the inbox reader turns into ``is_synthetic = true`` on the document
and report rows, and the dashboard shows a banner whenever such rows exist.

The demo goes through the *real* pipeline: the files are dropped in
``data/inbox`` and processed like hand-downloaded reports, so ingestion,
parsing, validation, quarantine, revision tracking and analytics all run.

Files written:

* ``SYNTHETIC_1_history_<DISEASE>.csv`` - weekly + cumulative counts
  (Lassa fever and cholera by state, the others national only)
* ``SYNTHETIC_7_problems.csv``  - a few invalid rows that must be quarantined
* ``SYNTHETIC_8_revisions.csv`` - corrected figures for earlier weeks
* ``SYNTHETIC_lassa_<year>_w<week>.pdf`` - the last four Lassa weeks as PDFs
  in the NCDC layout, so the PDF parser is exercised too
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.common.epiweek import iter_weeks, week_end, week_of
from src.demo.pdf_writer import write_sitrep_pdf


@dataclass(frozen=True)
class Profile:
    code: str
    name: str
    base: float
    peak: float
    peak_week: int
    width: float
    confirm_ratio: float
    cfr: float
    denominator: str
    year_factor: dict
    states: dict | None = None  # state -> weight


PROFILES = (
    Profile("LASSA", "Lassa Fever", 35, 260, 6, 5.0, 0.17, 0.19, "confirmed",
            {2021: 0.8, 2022: 1.1, 2023: 1.0, 2024: 1.2, 2025: 1.05, 2026: 1.15},
            {"Ondo": 28, "Edo": 24, "Bauchi": 12, "Taraba": 8, "Ebonyi": 5, "Benue": 4, "Plateau": 3, "Kogi": 3,
             "Nasarawa": 2, "Gombe": 2, "Enugu": 2, "Kaduna": 2, "Anambra": 2, "Delta": 2, "Cross River": 1}),
    Profile("CHOLERA", "Cholera", 25, 700, 36, 7.0, 0.04, 0.025, "suspected",
            {2021: 3.0, 2022: 0.6, 2023: 0.4, 2024: 0.9, 2025: 0.7, 2026: 0.5},
            {"Borno": 16, "Bauchi": 14, "Zamfara": 10, "Katsina": 10, "Kano": 9, "Yobe": 8, "Jigawa": 7,
             "Sokoto": 6, "Lagos": 6, "Adamawa": 5, "Gombe": 5, "Kebbi": 4}),
    Profile("MEASLES", "Measles", 150, 450, 12, 6.0, 0.25, 0.004, "suspected",
            {2021: 1.0, 2022: 1.2, 2023: 0.9, 2024: 1.0, 2025: 1.1, 2026: 0.9}),
    Profile("CSM", "Cerebrospinal Meningitis", 15, 350, 12, 4.0, 0.10, 0.06, "suspected",
            {2021: 0.6, 2022: 0.8, 2023: 1.6, 2024: 1.0, 2025: 0.9, 2026: 0.8}),
    Profile("YELLOW_FEVER", "Yellow Fever", 22, 55, 44, 8.0, 0.03, 0.10, "confirmed",
            {2021: 1.0, 2022: 1.0, 2023: 0.9, 2024: 1.1, 2025: 1.0, 2026: 1.0}),
    Profile("DIPHTHERIA", "Diphtheria", 0, 0, 1, 1.0, 0.40, 0.05, "suspected", {}),
    Profile("MPOX", "Mpox", 7, 10, 30, 10.0, 0.35, 0.01, "confirmed",
            {2021: 0.5, 2022: 2.5, 2023: 1.0, 2024: 1.2, 2025: 1.0, 2026: 0.9}),
)


def _seasonal(week: int, peak_week: int, width: float) -> float:
    d = (week - peak_week + 26) % 52 - 26
    return math.exp(-(d ** 2) / (2 * width ** 2))


def _intensity(p: Profile, year: int, week: int, ws: date) -> float:
    if p.code == "DIPHTHERIA":  # emerges mid-2022, large 2023 outbreak, then endemic
        if ws < date(2022, 7, 25):
            return 0.3
        days = (ws - date(2023, 8, 28)).days / 7
        return 40 + 650 * math.exp(-(days ** 2) / (2 * 14 ** 2)) + (120 if ws > date(2023, 6, 1) else 0)
    return (p.base + p.peak * _seasonal(week, p.peak_week, p.width)) * p.year_factor.get(year, 1.0)


def last_complete_week(today: date | None = None, end_year: int = 2026) -> tuple[int, int]:
    today = today or date.today()
    year, week = week_of(today - timedelta(days=7))
    return (year, week) if year <= end_year else (end_year, week_of(date(end_year, 12, 28))[1])


def simulate(start: tuple[int, int] = (2021, 1), end: tuple[int, int] | None = None, seed: int = 42) -> pd.DataFrame:
    """One row per disease, location ('National' or a state) and week."""
    rng = np.random.default_rng(seed)
    end = end or last_complete_week()
    rows = []
    for p in PROFILES:
        states = p.states or {}
        names = list(states)
        weights = np.array([states[s] for s in names], float)
        weights = weights / weights.sum() if names else weights
        for year, week in iter_weeks(start, end):
            ws = date.fromisocalendar(year, week, 1)
            lam = _intensity(p, year, week, ws)
            k = 8.0  # negative binomial dispersion
            suspected = int(rng.poisson(rng.gamma(k, lam / k))) if lam > 0 else 0
            if names:
                split = rng.multinomial(suspected, weights)
                parts = []
                for name, s in zip(names, split):
                    c = int(rng.binomial(s, p.confirm_ratio))
                    base = c if p.denominator == "confirmed" else s
                    parts.append((name, int(s), c, int(rng.binomial(base, p.cfr))))
                for name, s, c, d in parts:
                    rows.append((p.code, name, year, week, s, c, d))
                rows.append((p.code, "National", year, week, sum(x[1] for x in parts), sum(x[2] for x in parts),
                             sum(x[3] for x in parts)))
            else:
                c = int(rng.binomial(suspected, p.confirm_ratio))
                base = c if p.denominator == "confirmed" else suspected
                rows.append((p.code, "National", year, week, suspected, c, int(rng.binomial(base, p.cfr))))
    df = pd.DataFrame(rows, columns=["disease_code", "state", "epi_year", "epi_week", "suspected", "confirmed", "deaths"])
    df = df.sort_values(["disease_code", "state", "epi_year", "epi_week"]).reset_index(drop=True)
    for col in ("suspected", "confirmed", "deaths"):
        df[f"cum_{col}"] = df.groupby(["disease_code", "state", "epi_year"])[col].cumsum()
    denom = {p.code: p.denominator for p in PROFILES}
    is_nat = df["state"] == "National"
    base = np.where(df["disease_code"].map(denom) == "confirmed", df["confirmed"], df["suspected"])
    df["cfr_reported"] = np.where(is_nat & (base > 0), np.round(100 * df["deaths"] / np.maximum(base, 1), 1), np.nan)
    lag = rng.integers(2, 6, len(df))
    lag[rng.random(len(df)) < 0.03] = 20  # a few late reports to trigger rule 11
    df["published_date"] = [week_end(int(y), int(w)) + timedelta(days=int(d))
                            for y, w, d in zip(df["epi_year"], df["epi_week"], lag)]
    return df


def write_demo_files(inbox: Path, *, seed: int = 42, end: tuple[int, int] | None = None, pdf_weeks: int = 4) -> list[Path]:
    """Write the demo files into ``inbox`` and return their paths."""
    inbox.mkdir(parents=True, exist_ok=True)
    df = simulate(end=end, seed=seed)
    written: list[Path] = []
    weeks = sorted({(int(y), int(w)) for y, w in zip(df["epi_year"], df["epi_week"])})
    pdf_set = set(weeks[-pdf_weeks:]) if pdf_weeks else set()

    for code, group in df.groupby("disease_code"):
        g = group
        if code == "LASSA":
            g = g[[(int(y), int(w)) not in pdf_set for y, w in zip(g["epi_year"], g["epi_week"])]]
        path = inbox / f"SYNTHETIC_1_history_{code}.csv"
        g.to_csv(path, index=False)
        written.append(path)

    written.append(_write_problems(inbox, df, weeks))
    written.append(_write_revisions(inbox, df, weeks, pdf_weeks))

    lassa = df[df["disease_code"] == "LASSA"]
    for year, week in sorted(pdf_set):
        wk = lassa[(lassa["epi_year"] == year) & (lassa["epi_week"] == week)]
        nat = wk[wk["state"] == "National"].iloc[0]
        states = [{k: int(r[k]) if k != "state" else r[k] for k in
                   ("state", "suspected", "confirmed", "deaths", "cum_suspected", "cum_confirmed", "cum_deaths")}
                  for _, r in wk[(wk["state"] != "National") & (wk["cum_suspected"] > 0)].iterrows()]
        path = inbox / f"SYNTHETIC_lassa_{year}_w{week:02d}.pdf"
        write_sitrep_pdf(
            path, disease_name="Lassa Fever", epi_year=year, epi_week=week,
            national_week={k: int(nat[k]) for k in ("suspected", "confirmed", "deaths")},
            national_cum={k: int(nat[f"cum_{k}"]) for k in ("suspected", "confirmed", "deaths")},
            states=states,
        )
        written.append(path)
    return written


def _write_problems(inbox: Path, df: pd.DataFrame, weeks: list[tuple[int, int]]) -> Path:
    y, w = weeks[-8]
    rows = [
        # rule 3: confirmed > suspected
        {"disease_code": "CSM", "state": "National", "epi_year": y, "epi_week": w, "suspected": 5, "confirmed": 9, "deaths": 0},
        # rule 9: place not in the HDX list
        {"disease_code": "CHOLERA", "state": "Atlantis", "epi_year": y, "epi_week": w, "suspected": 3, "confirmed": 0, "deaths": 0},
        # rule 4: deaths > confirmed for Lassa
        {"disease_code": "LASSA", "state": "Kwara", "epi_year": y, "epi_week": w, "suspected": 4, "confirmed": 1, "deaths": 3},
        # rule 2: not a whole number
        {"disease_code": "MEASLES", "state": "National", "epi_year": y, "epi_week": w, "suspected": "12.5", "confirmed": 2, "deaths": 0},
        # rule 8: week 54 does not exist
        {"disease_code": "MPOX", "state": "National", "epi_year": y, "epi_week": 54, "suspected": 1, "confirmed": 0, "deaths": 0},
    ]
    path = inbox / "SYNTHETIC_7_problems.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_revisions(inbox: Path, df: pd.DataFrame, weeks: list[tuple[int, int]], pdf_weeks: int) -> Path:
    """Late corrections: a few Lassa states get extra cases for six earlier weeks."""
    rng = np.random.default_rng(7)
    target = weeks[-(pdf_weeks + 10):-(pdf_weeks + 4)]
    lassa = df[df["disease_code"] == "LASSA"]
    out = []
    for year, week in target:
        wk = lassa[(lassa["epi_year"] == year) & (lassa["epi_week"] == week)].set_index("state")
        add_total = {"suspected": 0, "confirmed": 0, "deaths": 0}
        for state, r in wk.drop(index="National").iterrows():
            new = {"suspected": int(r.suspected), "confirmed": int(r.confirmed), "deaths": int(r.deaths)}
            if state in ("Ondo", "Edo", "Bauchi"):
                extra_c = int(rng.integers(1, 4))
                new["confirmed"] += extra_c
                new["suspected"] += extra_c + int(rng.integers(0, 4))
                new["deaths"] = min(new["deaths"] + int(rng.integers(0, 2)), new["confirmed"])
            for k in add_total:
                add_total[k] += new[k] - int(r[k])
            # every state is listed so the national total still equals the state sum (rule 7)
            out.append({"disease_code": "LASSA", "state": state, "epi_year": year, "epi_week": week, **new})
        nat = wk.loc["National"]
        out.append({"disease_code": "LASSA", "state": "National", "epi_year": year, "epi_week": week,
                    **{k: int(nat[k]) + add_total[k] for k in add_total}})
    path = inbox / "SYNTHETIC_8_revisions.csv"
    pd.DataFrame(out).to_csv(path, index=False)
    return path
