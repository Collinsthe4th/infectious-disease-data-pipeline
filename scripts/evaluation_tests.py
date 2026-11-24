"""Evaluation tests for Chapter 4 (read-only: nothing in the database is changed).

    python scripts/evaluation_tests.py db OUT_DIR                 # run time, reliability, data quality, revisions, alerts
    python scripts/evaluation_tests.py sample OUT_DIR [N]         # extraction audit sample: stored figures + PDF page images
    python scripts/evaluation_tests.py dashboard OUT_DIR URL      # live dashboard load time and displayed-value check

Each command writes CSV files and a JSON summary into OUT_DIR.
"""

from __future__ import annotations

import json
import random
import re
import sys
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.storage.db import get_engine  # noqa: E402

SEED = 42
NOT_DEMO_RUN = "COALESCE(error_message, '') NOT LIKE '%synthetic demo run%'"


def q(sql: str, **params) -> pd.DataFrame:
    with get_engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


def save(out: Path, name: str, df: pd.DataFrame) -> None:
    df.to_csv(out / f"{name}.csv", index=False)
    print(f"\n== {name} ({len(df)} rows)\n{df.head(40).to_string(index=False)}")


# --------------------------------------------------------------------------- #
#  1. Database analyses
# --------------------------------------------------------------------------- #

def cmd_db(out: Path) -> dict:
    summary: dict = {}

    # Run time and reliability ------------------------------------------------
    runs = q(f"""SELECT run_type, trigger_source, status, started_at, ended_at,
                        EXTRACT(EPOCH FROM (ended_at - started_at)) AS duration_s,
                        files_discovered, files_new, rows_extracted, rows_passed, rows_warned, rows_quarantined,
                        revisions_detected, left(error_message, 200) AS error_message
                   FROM ops.pipeline_run_log WHERE {NOT_DEMO_RUN} ORDER BY started_at""")
    save(out, "runs", runs)
    done = runs[runs["status"] != "running"]
    by_type = done.groupby("run_type").agg(
        runs=("status", "size"), success=("status", lambda s: (s == "success").sum()),
        partial=("status", lambda s: (s == "partial").sum()), failed=("status", lambda s: (s == "failed").sum()),
        median_min=("duration_s", lambda s: round(s.median() / 60, 2)),
        p90_min=("duration_s", lambda s: round(s.quantile(0.9) / 60, 2)),
        max_min=("duration_s", lambda s: round(s.max() / 60, 2))).reset_index()
    save(out, "runs_by_type", by_type)
    weekly = done[done["run_type"].isin(["scheduled", "daily_check"])]
    summary["routine_runs"] = {
        "n": int(len(weekly)),
        "completed_pct": round(100 * weekly["status"].isin(["success", "partial"]).mean(), 1) if len(weekly) else None,
        "under_15_min_pct": round(100 * (weekly["duration_s"] < 900).mean(), 1) if len(weekly) else None,
        "median_min": round(weekly["duration_s"].median() / 60, 2) if len(weekly) else None,
        "max_min": round(weekly["duration_s"].max() / 60, 2) if len(weekly) else None,
    }

    # Data quality: share of reports that passed every rule --------------------
    docs = q("""SELECT disease_hint AS disease, processing_status, count(*) AS files
                  FROM raw.documents WHERE NOT is_synthetic GROUP BY 1, 2 ORDER BY 1, 2""")
    save(out, "documents_by_status", docs)
    rep = q("""WITH r AS (
                 SELECT r.report_id, r.document_id, COALESCE(d.disease_code, 'MULTI') AS disease
                   FROM curated.dim_source_report r LEFT JOIN curated.dim_disease d USING (disease_id)
                  WHERE NOT r.is_synthetic)
               SELECT r.disease,
                      count(*) AS reports,
                      count(*) FILTER (WHERE NOT w.warned AND NOT h.held) AS passed_all_rules,
                      count(*) FILTER (WHERE w.warned AND NOT h.held) AS warnings_only,
                      count(*) FILTER (WHERE h.held) AS had_figures_held_back
                 FROM r
                 CROSS JOIN LATERAL (SELECT EXISTS (SELECT 1 FROM curated.fact_weekly_cases f
                                      WHERE f.report_id = r.report_id AND cardinality(f.quality_flags) > 0) AS warned) w
                 CROSS JOIN LATERAL (SELECT EXISTS (SELECT 1 FROM ops.quarantine x
                                      WHERE x.document_id = r.document_id AND x.scope <> 'document'
                                        AND x.review_status = 'open') AS held) h
                GROUP BY ROLLUP (r.disease) ORDER BY r.disease NULLS LAST""")
    rep["disease"] = rep["disease"].fillna("ALL")
    rep["passed_pct"] = (100 * rep["passed_all_rules"] / rep["reports"]).round(1)
    save(out, "reports_passing_rules", rep)
    summary["data_quality"] = rep[rep["disease"] == "ALL"].iloc[0].to_dict()
    rules = q("""SELECT v.rule_code, v.severity, count(*) AS rows
                   FROM curated.fact_weekly_cases f JOIN curated.dim_source_report r USING (report_id),
                        unnest(f.quality_flags) AS flag JOIN ops.validation_rule v ON v.rule_code = flag
                  WHERE NOT r.is_synthetic GROUP BY 1, 2
                 UNION ALL
                 SELECT COALESCE(v.rule_code, 'PARSE_FAILURE'), COALESCE(v.severity, 'error'), count(*)
                   FROM ops.quarantine x LEFT JOIN ops.validation_rule v USING (rule_id)
                  WHERE x.review_status = 'open' GROUP BY 1, 2 ORDER BY 3 DESC""")
    save(out, "rule_counts", rules)

    # Revisions (RQ2) ---------------------------------------------------------
    rev = q("""SELECT d.disease_code, l.admin_level, w.epi_year, w.epi_week, h.field_name, h.old_value, h.new_value,
                      h.abs_change, h.rel_change, h.revision_lag_weeks,
                      o.report_code AS old_report, n.report_code AS new_report,
                      (h.old_report_id = h.new_report_id) AS same_report
                 FROM curated.revision_history h
                 JOIN curated.dim_disease d USING (disease_id) JOIN curated.dim_location l USING (location_id)
                 JOIN curated.dim_epi_week w USING (week_id)
                 JOIN curated.dim_source_report o ON o.report_id = h.old_report_id
                 JOIN curated.dim_source_report n ON n.report_id = h.new_report_id
                WHERE NOT n.is_synthetic""")
    save(out, "revisions_all", rev)
    ncdc = rev[~rev["same_report"]].copy()  # same report read again = our own correction, not an NCDC revision
    facts = q("""SELECT d.disease_code, count(*) AS facts
                   FROM curated.fact_weekly_cases f JOIN curated.dim_source_report r USING (report_id)
                   JOIN curated.dim_disease d ON d.disease_id = f.disease_id
                  WHERE NOT r.is_synthetic GROUP BY 1""")
    if len(ncdc):
        ncdc["abs_rel_pct"] = (ncdc["rel_change"].astype(float).abs() * 100)
        by = ncdc.groupby(["disease_code", "field_name"]).agg(
            revisions=("field_name", "size"),
            median_abs_change=("abs_change", lambda s: float(s.astype(float).abs().median())),
            median_rel_pct=("abs_rel_pct", "median"), max_rel_pct=("abs_rel_pct", "max"),
            median_lag_weeks=("revision_lag_weeks", "median"),
            upward=("abs_change", lambda s: int((s.astype(float) > 0).sum()))).reset_index().round(2)
    else:
        by = pd.DataFrame()
    save(out, "revisions_ncdc_by_disease_field", by)
    revised_facts = ncdc.drop_duplicates(["disease_code", "admin_level", "epi_year", "epi_week"]).shape[0] if len(ncdc) else 0
    summary["revisions"] = {
        "all_changes": int(len(rev)), "own_rereads": int(rev["same_report"].sum()) if len(rev) else 0,
        "ncdc_revisions": int(len(ncdc)), "facts_total": int(facts["facts"].sum()),
        "ncdc_revised_weeks_and_places": int(revised_facts),
        "median_rel_pct": round(float(ncdc["abs_rel_pct"].median()), 2) if len(ncdc) else None,
        "share_within_1pct": round(float((ncdc["abs_rel_pct"] <= 1).mean() * 100), 1) if len(ncdc) else None,
        "national_ncdc_revisions": int((ncdc["admin_level"] == 0).sum()) if len(ncdc) else 0,
    }

    # Revisions implied by the year-to-date totals --------------------------------
    # NCDC reports only print the current week, so a later report rarely restates an earlier week. But each
    # report also prints the year-to-date total. If NCDC changed earlier weeks, the new total differs from
    # last week's total plus this week's new cases: gap = cum_t - cum_(t-1) - new_t.
    ytd = q("""SELECT disease_code, epi_year, epi_week, week_start, suspected, confirmed, deaths,
                      cum_suspected, cum_confirmed, cum_deaths, is_derived_from_cumulative
                 FROM curated.v_weekly_cases WHERE admin_level = 0 AND NOT is_synthetic
                ORDER BY disease_code, week_start""")
    gaps = []
    for (code, year), g in ytd.groupby(["disease_code", "epi_year"]):
        g = g.sort_values("epi_week")
        for m in ("suspected", "confirmed", "deaths"):
            prev = g[f"cum_{m}"].shift()
            ok = (g["epi_week"].diff() == 1) & g[m].notna() & g[f"cum_{m}"].notna() & prev.notna() \
                & ~g["is_derived_from_cumulative"]
            for r, pv in zip(g[ok].itertuples(), prev[ok]):
                gap = int(getattr(r, f"cum_{m}") - pv - getattr(r, m))
                gaps.append({"disease": code, "epi_year": int(year), "epi_week": int(r.epi_week), "measure": m,
                             "previous_total": int(pv), "new_this_week": int(getattr(r, m)),
                             "total_now": int(getattr(r, f"cum_{m}")), "gap": gap,
                             "gap_pct_of_total": round(100 * gap / pv, 2) if pv else None})
    gaps = pd.DataFrame(gaps)
    save(out, "revisions_implied_weekly", gaps)
    if len(gaps):
        gs = gaps.groupby(["disease", "measure"]).agg(
            week_pairs=("gap", "size"), with_gap=("gap", lambda x: int((x != 0).sum())),
            upward=("gap", lambda x: int((x > 0).sum())), downward=("gap", lambda x: int((x < 0).sum())),
            median_abs_gap=("gap", lambda x: float(x[x != 0].abs().median()) if (x != 0).any() else 0.0),
            median_abs_gap_pct=("gap_pct_of_total", lambda x: float(x[x != 0].abs().median()) if (x != 0).any() else 0.0),
            max_abs_gap=("gap", lambda x: int(x.abs().max()))).reset_index()
        gs["with_gap_pct"] = (100 * gs["with_gap"] / gs["week_pairs"]).round(1)
        save(out, "revisions_implied_summary", gs)
        summary["revisions_implied"] = {
            "week_pairs": int(len(gaps)), "with_gap_pct": round(100 * float((gaps["gap"] != 0).mean()), 1),
            "upward_pct_of_gaps": round(100 * float((gaps["gap"] > 0).sum() / max((gaps["gap"] != 0).sum(), 1)), 1),
            "median_abs_gap_pct_of_total": round(float(gaps.loc[gaps["gap"] != 0, "gap_pct_of_total"].abs().median()), 2)}

    # Two reports for the same disease and week (one of them is probably labelled with the wrong week)
    dup = q("""SELECT d.disease_code, w.epi_year, w.epi_week, r.report_code, doc.listing_title, doc.listing_date,
                      doc.file_name, doc.source_url
                 FROM curated.dim_source_report r JOIN curated.dim_disease d USING (disease_id)
                 JOIN curated.dim_epi_week w USING (week_id) JOIN raw.documents doc USING (document_id)
                WHERE NOT r.is_synthetic AND (r.disease_id, r.week_id) IN (
                      SELECT disease_id, week_id FROM curated.dim_source_report WHERE NOT is_synthetic
                       GROUP BY 1, 2 HAVING count(*) > 1)
                ORDER BY 1, 2, 3, 4""")
    save(out, "reports_sharing_a_week", dup)

    # Alerts against each season's peak ---------------------------------------
    nat = q("""SELECT disease_code, week_id, week_start, epi_year, suspected, confirmed
                 FROM curated.v_weekly_cases WHERE admin_level = 0 AND NOT is_synthetic ORDER BY 1, 3""")
    alerts = q("""SELECT d.disease_code, a.week_id, a.method, a.is_alert
                    FROM analytics.alert a JOIN curated.dim_disease d USING (disease_id)
                    JOIN curated.dim_location l USING (location_id) WHERE l.admin_level = 0""")
    rows, weeks_rows = [], []
    for code, g in nat.groupby("disease_code"):
        measure = "confirmed" if g["confirmed"].notna().sum() >= g["suspected"].notna().sum() else "suspected"
        g = g.dropna(subset=[measure]).copy()
        a = alerts[(alerts["disease_code"] == code)]
        fired = a[a["is_alert"]].groupby("week_id")["method"].apply(lambda m: ",".join(sorted(m)))
        g["alert_methods"] = g["week_id"].map(fired).fillna("")
        g["checked"] = g["week_id"].isin(a["week_id"])
        for year, y in g.groupby("epi_year"):
            if len(y) < 10 or not y["checked"].any():
                continue
            peak_val = y[measure].max()
            peak = y.loc[y[measure].idxmax()]
            high = y[measure] >= 0.5 * peak_val  # the 'busy' weeks of that year: at least half the peak
            al = y[y["alert_methods"] != ""]
            first = al.iloc[0] if len(al) else None
            rows.append({
                "disease": code, "year": int(year), "measure": measure, "weeks": len(y),
                "peak_week": str(peak["week_start"]), "peak_value": int(peak_val),
                "alert_weeks": len(al), "first_alert_week": str(first["week_start"]) if first is not None else "",
                "weeks_before_peak": int((pd.Timestamp(peak["week_start"]) - pd.Timestamp(first["week_start"])).days // 7)
                                     if first is not None else None,
                "busy_weeks": int(high.sum()),
                "busy_weeks_with_alert": int((high & (y["alert_methods"] != "")).sum()),
                "alerts_in_busy_weeks": int(((y["alert_methods"] != "") & high).sum()),
                "alerts_in_quiet_weeks": int(((y["alert_methods"] != "") & ~high).sum()),
            })
            weeks_rows.append(y.assign(disease=code, measure=measure, busy=high)[
                ["disease", "epi_year", "week_start", "measure", measure, "busy", "alert_methods"]].rename(
                columns={measure: "value"}))
    season = pd.DataFrame(rows)
    save(out, "alerts_by_season", season)
    if weeks_rows:
        save(out, "alerts_weekly", pd.concat(weeks_rows))
    if len(season):
        tot = season[["alert_weeks", "alerts_in_busy_weeks", "busy_weeks", "busy_weeks_with_alert"]].sum()
        summary["alerts"] = {
            "seasons": int(len(season)),
            "seasons_with_alert_before_or_at_peak": int((season["weeks_before_peak"] >= 0).sum()),
            "median_weeks_before_peak": float(season["weeks_before_peak"].median()),
            "share_of_alerts_in_busy_weeks_pct": round(100 * tot["alerts_in_busy_weeks"] / max(tot["alert_weeks"], 1), 1),
            "share_of_busy_weeks_flagged_pct": round(100 * tot["busy_weeks_with_alert"] / max(tot["busy_weeks"], 1), 1),
        }
    return summary


# --------------------------------------------------------------------------- #
#  2. Extraction audit sample (RQ1)
# --------------------------------------------------------------------------- #

FIELDS = ["suspected", "confirmed", "deaths", "cum_suspected", "cum_confirmed", "cum_deaths", "cfr_reported"]


def cmd_sample(out: Path, n: int = 30) -> dict:
    import io

    import pdfplumber
    import pypdfium2 as pdfium
    import requests

    reports = q(f"""SELECT r.report_id, r.report_code, d.disease_code, w.epi_year, w.epi_week, r.template_id,
                           r.extraction_engine, doc.source_url, doc.file_name,
                           {", ".join(f"f.{c}" for c in FIELDS)}, f.is_derived_from_cumulative
                      FROM curated.dim_source_report r
                      JOIN curated.dim_disease d USING (disease_id)
                      JOIN curated.dim_epi_week w USING (week_id)
                      JOIN raw.documents doc USING (document_id)
                      JOIN curated.fact_weekly_cases f ON f.report_id = r.report_id AND f.week_id = r.week_id
                      JOIN curated.dim_location l ON l.location_id = f.location_id AND l.admin_level = 0
                     WHERE NOT r.is_synthetic AND doc.source_url IS NOT NULL
                     ORDER BY r.report_id""")
    rng = random.Random(SEED)
    # Stratified: up to 4 reports per disease, then fill the rest at random (most reports are Lassa fever).
    picked: list[int] = []
    for _, g in reports.groupby("disease_code"):
        ids = g["report_id"].tolist()
        picked += rng.sample(ids, min(4, len(ids)))
    rest = [i for i in reports["report_id"] if i not in picked]
    picked += rng.sample(rest, max(0, min(n - len(picked), len(rest))))
    sample = reports[reports["report_id"].isin(picked)].sort_values(["disease_code", "epi_year", "epi_week"])

    img_dir = out / "pages"
    img_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    session = requests.Session()
    session.headers["User-Agent"] = "ncdc-pipeline-research (thesis evaluation; contact via GitHub)"
    for i, r in enumerate(sample.itertuples(), 1):
        values = {c: getattr(r, c) for c in FIELDS if pd.notna(getattr(r, c))}
        page_no, note = 1, ""
        try:
            pdf = session.get(r.source_url, timeout=60).content
            # Find the page that prints the week's key figures (the first page that mentions the epi week);
            # fall back to page 1. Only used to choose which page to show; the check itself is by eye.
            with pdfplumber.open(io.BytesIO(pdf)) as doc:
                for k, page in enumerate(doc.pages[:6], 1):
                    t = (page.extract_text() or "").lower()
                    if re.search(rf"week\s*{r.epi_week}\b", t) and any(f"{int(v):,}" in t or str(int(v)) in t
                                                                      for v in values.values() if float(v).is_integer()):
                        page_no = k
                        break
            pdoc = pdfium.PdfDocument(pdf)
            pages = [page_no] + ([page_no + 1] if page_no + 1 <= len(pdoc) else [])
            for p in pages:
                pdoc[p - 1].render(scale=1.4).to_pil().save(img_dir / f"{i:02d}_{r.report_code}_p{p}.png")
        except Exception as exc:  # the file could not be fetched or rendered
            note = f"page image failed: {exc}"[:200]
        for field, v in values.items():
            rows.append({"n": i, "disease": r.disease_code, "report_code": r.report_code, "epi_year": r.epi_year,
                         "epi_week": r.epi_week, "template": r.template_id, "engine": r.extraction_engine,
                         "field": field, "stored": v,
                         "derived_from_cumulative": bool(r.is_derived_from_cumulative) and field in
                         ("suspected", "confirmed", "deaths"),
                         "page_shown": page_no, "source_url": r.source_url, "note": note,
                         "pdf_value": "", "match": ""})
        time.sleep(1.5)  # be polite to ncdc.gov.ng
    df = pd.DataFrame(rows)
    save(out, "extraction_sample", df)
    return {"reports": int(sample.shape[0]), "fields": int(len(df)),
            "by_disease": sample["disease_code"].value_counts().to_dict()}


# --------------------------------------------------------------------------- #
#  3. Live dashboard: load time and displayed values
# --------------------------------------------------------------------------- #

PAGES = {"overview": "", "trends": "Trends_over_time", "by_state": "By_state", "forecasts": "Forecasts",
         "alerts": "Outbreak_alerts", "pipeline_health": "Data_pipeline_health"}


def _wait_rendered(frame, timeout_s: float = 90) -> float:
    """Seconds until the page has a title and Streamlit has stopped running the script."""
    t0 = time.perf_counter()
    frame.locator("h1").first.wait_for(state="visible", timeout=timeout_s * 1000)
    while time.perf_counter() - t0 < timeout_s:
        running = frame.locator('[data-testid="stStatusWidget"]').count()
        skeleton = frame.locator('[data-testid="stSkeleton"]').count()
        if not running and not skeleton:
            break
        time.sleep(0.1)
    return time.perf_counter() - t0


def cmd_dashboard(out: Path, base: str, repeats: int = 5) -> dict:
    from playwright.sync_api import sync_playwright

    base = base.rstrip("/")
    timings, cards = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        # Wake the app (Streamlit Community Cloud puts idle apps to sleep) and record that first load.
        t0 = time.perf_counter()
        page.goto(base, wait_until="domcontentloaded", timeout=300_000)
        page.wait_for_timeout(5000)
        wake = page.get_by_role("button", name=re.compile("get this app back up", re.I))
        if wake.count():
            wake.first.click()
            page.wait_for_timeout(5000)
        cold = None
        for _ in range(30):  # up to ~5 minutes while the app starts
            try:
                page.goto(f"{base}/~/+/", wait_until="domcontentloaded", timeout=60_000)
                _wait_rendered(page.main_frame, timeout_s=30)
                cold = time.perf_counter() - t0
                break
            except Exception:
                page.screenshot(path=str(out / "dashboard_wait.png"))
        if cold is None:
            raise SystemExit("The dashboard did not start within 5 minutes (see dashboard_wait.png).")
        for name, path in PAGES.items():
            for k in range(repeats + 1):  # the first visit of each page warms the cache and is not counted
                t0 = time.perf_counter()
                page.goto(f"{base}/~/+/{path}", wait_until="domcontentloaded", timeout=120_000)
                secs = _wait_rendered(page.main_frame)
                total = time.perf_counter() - t0
                timings.append({"page": name, "visit": k, "warm_up": k == 0, "seconds": round(total, 2),
                                "render_seconds": round(secs, 2)})
                print(f"{name} visit {k}: {total:.2f}s")
            if name == "overview":
                f = page.main_frame
                for c in f.locator(".cards .card:not(.muted)").all():
                    cards.append({"name": c.locator(".name").inner_text().strip(),
                                  "num": c.locator(".num").inner_text().strip(),
                                  "what": c.locator(".what").inner_text().strip(),
                                  "more": c.locator(".more").inner_text().strip()})
                week_txt = f.get_by_text(re.compile(r"This week's reports: week \d+, \d{4}")).first.inner_text()
            if name == "pipeline_health":
                f = page.main_frame
                metric = f.locator('[data-testid="stMetric"]', has_text="Waiting for review").first
                waiting = metric.locator('[data-testid="stMetricValue"]').inner_text().strip()
        browser.close()

    t = pd.DataFrame(timings)
    save(out, "dashboard_timings", t)
    warm = t[~t["warm_up"]]
    per_page = warm.groupby("page")["seconds"].agg(["median", "max"]).round(2).reset_index()
    save(out, "dashboard_timings_by_page", per_page)

    # Displayed values against the database
    m = re.search(r"week (\d+), (\d{4})", week_txt)
    ew, ey = int(m.group(1)), int(m.group(2))
    db = q("""SELECT disease_name, suspected, confirmed, deaths FROM curated.v_weekly_cases
               WHERE admin_level = 0 AND epi_year = :y AND epi_week = :w""", y=ey, w=ew).set_index("disease_name")
    checks = []
    for c in cards:
        shown = {c["what"].lower(): int(c["num"].replace(",", ""))}
        for part in re.split(r"\s*•\s*", c["more"]):
            mm = re.match(r"(Suspected cases|Confirmed cases|Deaths)\s+([\d,]+)", part, re.I)
            if mm:
                shown[mm.group(1).lower()] = int(mm.group(2).replace(",", ""))
        for label, val in shown.items():
            col = "suspected" if "suspected" in label else "confirmed" if "confirmed" in label else "deaths"
            expected = db.loc[c["name"], col] if c["name"] in db.index else None
            checks.append({"card": c["name"], "field": col, "shown": val,
                           "database": None if pd.isna(expected) else int(expected),
                           "match": (not pd.isna(expected)) and int(expected) == val})
    open_items = int(q("SELECT count(*) AS n FROM ops.quarantine WHERE review_status = 'open'")["n"].iloc[0])
    checks.append({"card": "Data pipeline health", "field": "waiting for review",
                   "shown": int(waiting.replace(",", "")), "database": open_items,
                   "match": int(waiting.replace(",", "")) == open_items})
    ch = pd.DataFrame(checks)
    save(out, "dashboard_value_checks", ch)
    return {"cold_start_s": round(cold, 1), "warm_median_s": round(float(warm["seconds"].median()), 2),
            "warm_p90_s": round(float(warm["seconds"].quantile(0.9)), 2),
            "warm_under_3s_pct": round(100 * float((warm["seconds"] < 3).mean()), 1),
            "values_checked": int(len(ch)), "values_matching": int(ch["match"].sum()), "week": f"{ew}/{ey}"}


if __name__ == "__main__":
    cmd, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    if cmd == "db":
        result = cmd_db(out)
    elif cmd == "sample":
        result = cmd_sample(out, int(sys.argv[3]) if len(sys.argv) > 3 else 30)
    elif cmd == "dashboard":
        result = cmd_dashboard(out, sys.argv[3])
    else:
        raise SystemExit(__doc__)
    (out / f"summary_{cmd}.json").write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps(result, indent=2, default=str))
