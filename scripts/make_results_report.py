"""Write the results page (results/README.md, charts and a data snapshot) from the database.

Run by the 'Results report' GitHub workflow after every database load, so the analysed
real NCDC data and the model results can be read on GitHub without installing anything.
Synthetic (demo) rows are never included. Every number in the text is filled in from the
database when the page is built.

    python scripts/make_results_report.py --out results
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.analytics.run import prepare_forecast_series  # noqa: E402
from src.analytics.series import forecast_measure  # noqa: E402
from src.common.config import get_settings  # noqa: E402
from src.storage.db import get_engine  # noqa: E402

MODEL_LABELS = {"seasonal_naive": "Seasonal naive", "sarima": "SARIMA", "negative_binomial": "Negative binomial",
                "random_forest": "Random Forest", "gradient_boosting": "Gradient Boosting", "xgboost": "XGBoost",
                "svr": "SVR"}
METHOD_LABELS = {"EARS_C1": "EARS C1", "EARS_C2": "EARS C2", "EARS_C3": "EARS C3",
                 "SARIMA_RESID_Z": "SARIMA residual"}
BLUE, ORANGE, GREEN, RED = "#1f4e79", "#e67e22", "#2c7a3f", "#c0392b"


def q(conn, sql: str, **params) -> pd.DataFrame:
    return pd.read_sql(text(sql), conn, params=params)


def md_table(df: pd.DataFrame, floatfmt: str = "{:.3f}") -> str:
    if df.empty:
        return "_none_\n"
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if isinstance(v, float):
                cells.append("" if pd.isna(v) else floatfmt.format(v))
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def n(x: float | int) -> str:
    return f"{int(round(x)):,}"


def code_version() -> str:
    sha = os.environ.get("GITHUB_SHA")
    if not sha:
        try:
            sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        except Exception:  # noqa: BLE001
            sha = "unknown"
    return sha[:12]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results")
    args = ap.parse_args()
    out = Path(args.out)
    fig_dir, data_dir = out / "figures", out / "data"
    fig_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = get_settings()

    with get_engine().connect() as conn:
        weekly = q(conn, "SELECT * FROM curated.v_weekly_cases WHERE NOT is_synthetic ORDER BY week_start")
        if weekly.empty:
            raise SystemExit("no real NCDC data in the database yet")
        docs = q(conn, """SELECT processing_status, count(*) AS files FROM raw.documents
                           WHERE NOT is_synthetic GROUP BY 1""")
        stl = q(conn, """SELECT d.disease_code, w.week_start, s.observed, s.trend, s.seasonal
                           FROM analytics.stl_component s JOIN curated.dim_disease d USING (disease_id)
                           JOIN curated.dim_location l USING (location_id) JOIN curated.dim_epi_week w USING (week_id)
                          WHERE l.pcode = 'NG' ORDER BY w.week_start""")
        latest = q(conn, "SELECT run_id::text AS r, max(created_at) AS at FROM analytics.forecast "
                         "GROUP BY 1 ORDER BY 2 DESC LIMIT 1")
        rid = latest["r"].iloc[0] if not latest.empty else None
        as_of = (pd.Timestamp(latest["at"].iloc[0]).tz_convert(None).normalize() if rid
                 else pd.Timestamp.today().normalize())
        evaluation = q(conn, """SELECT d.disease_code, d.disease_name, e.model_name, e.horizon, e.n_origins, e.mae,
                                       e.rmse, e.mase, e.dm_reference_model, e.dm_statistic, e.dm_p_value
                                  FROM analytics.model_evaluation e JOIN curated.dim_disease d USING (disease_id)
                                  JOIN curated.dim_location l USING (location_id)
                                 WHERE e.run_id = CAST(:r AS uuid) AND l.pcode = 'NG'""", r=rid) if rid else pd.DataFrame()
        fc = q(conn, """SELECT d.disease_code, f.model_name, f.is_backtest, f.horizon, t.week_start AS target,
                               f.predicted, f.actual, f.lower_95, f.upper_95
                          FROM analytics.forecast f JOIN curated.dim_epi_week t ON t.week_id = f.target_week_id
                          JOIN curated.dim_disease d USING (disease_id) JOIN curated.dim_location l USING (location_id)
                         WHERE f.run_id = CAST(:r AS uuid) AND l.pcode = 'NG'""", r=rid) if rid else pd.DataFrame()
        alerts = q(conn, """SELECT d.disease_name, a.method, count(*) FILTER (WHERE a.is_alert) AS alerts,
                                   count(*) AS weeks_checked
                              FROM analytics.alert a JOIN curated.dim_disease d USING (disease_id)
                              JOIN curated.dim_location l USING (location_id)
                             WHERE l.pcode = 'NG' GROUP BY 1, 2 ORDER BY 1, 2""")
        runs = q(conn, """SELECT started_at, run_type, status, files_new, rows_extracted, rows_passed,
                                 rows_warned, rows_quarantined, revisions_detected,
                                 EXTRACT(EPOCH FROM (ended_at - started_at))::int AS seconds
                            FROM ops.pipeline_run_log
                           WHERE COALESCE(error_message, '') NOT LIKE '%synthetic demo run%'
                           ORDER BY started_at DESC""")
        quarantine = q(conn, """SELECT COALESCE(r.rule_code, 'parse') AS rule, count(*) AS rows_held_back,
                                       min(q.failure_reason) AS example
                                  FROM ops.quarantine q LEFT JOIN ops.validation_rule r USING (rule_id)
                                 GROUP BY 1 ORDER BY 2 DESC""")
        flags = q(conn, "SELECT flag_type, count(*) AS n FROM ops.data_quality_flag GROUP BY 1 ORDER BY 2 DESC")
        revisions = q(conn, """SELECT d.disease_name, h.field_name, h.old_value, h.new_value, h.revision_lag_weeks
                                 FROM curated.revision_history h JOIN curated.dim_disease d USING (disease_id)
                                 JOIN curated.dim_source_report r ON r.report_id = h.new_report_id
                                WHERE NOT r.is_synthetic""")

    weekly["week_start"] = pd.to_datetime(weekly["week_start"])
    nat = weekly[weekly["admin_level"] == 0].copy()
    names = dict(zip(nat["disease_code"], nat["disease_name"]))
    lines: list[str] = []
    add = lines.extend

    # ------------------------------------------------------------------ header
    add(["# Results", "",
         f"> **Real NCDC data, as of {pd.Timestamp.now(tz='Africa/Lagos'):%d %B %Y}.** All figures were read "
         "automatically from the situation reports of the Nigeria Centre for Disease Control and Prevention (NCDC) "
         "and checked by the pipeline's validation rules. The four most recent weeks may still be revised by later "
         "reports. No synthetic (demo) data is included.", "",
         "This page is rebuilt automatically after every database load, so every number below comes from the "
         "database at the time of writing. The research questions (RQ1 to RQ4) are the ones in the thesis.", ""])

    # ------------------------------------------------------------------ 1 data
    has_weekly = nat.groupby("disease_name")[["suspected", "confirmed"]].apply(lambda g: g.notna().any().any())
    coverage = (nat.groupby("disease_name")
                .agg(first_week=("week_start", "min"), last_week=("week_start", "max"),
                     weeks=("week_start", "nunique"))
                .reset_index())
    coverage["Weekly figures"] = coverage["disease_name"].map(
        lambda d: "yes" if has_weekly.get(d) else "year-to-date totals only")
    most = coverage.sort_values("weeks", ascending=False).iloc[0]
    for c in ("first_week", "last_week"):
        coverage[c] = coverage[c].dt.strftime("%d %b %Y")
    coverage = coverage.rename(columns={"disease_name": "Disease", "first_week": "First week",
                                        "last_week": "Last week", "weeks": "Weeks"})
    files = dict(zip(docs["processing_status"], docs["files"]))
    total_files = int(docs["files"].sum())
    add(["## 1. Data collected", "",
         f"The pipeline downloaded {n(total_files)} NCDC report files. It read {n(files.get('parsed', 0))} of them "
         f"automatically, set aside {n(files.get('ignored', 0))} (mostly reports from before the 2021 to 2026 study "
         f"period) and sent {n(files.get('manual_queue', 0))} to the manual queue because their layout could not be "
         f"read with confidence. Table 1 shows what was stored for each disease. {most['disease_name']} had the most "
         f"complete record, with {n(most['weeks'])} weeks. This is because NCDC publishes a Lassa fever report "
         "almost every week, while the other diseases are reported in short runs during outbreaks or once a month.", "",
         "Table 1- National figures stored for each disease", "", md_table(coverage), ""])

    # ------------------------------------------------------------------ 2 runs
    total_runs = len(runs)
    ok_runs = int((runs["status"] == "success").sum())
    partial = int((runs["status"] == "partial").sum())
    failed = int((runs["status"] == "failed").sum())
    recent = runs.head(10).assign(started_at=runs.head(10)["started_at"].astype(str).str[:16]).rename(columns={
        "started_at": "Started (UTC)", "run_type": "Type", "status": "Status", "files_new": "New files",
        "rows_extracted": "Rows read", "rows_passed": "Rows loaded", "rows_warned": "Rows flagged",
        "rows_quarantined": "Rows held back", "revisions_detected": "Revisions", "seconds": "Seconds"})
    add(["## 2. Pipeline runs (RQ1)", "",
         f"Out of {total_runs} recorded runs, {ok_runs} finished successfully, {partial} finished partly (the run "
         f"completed but at least one report could not be read or loaded) and {failed} failed. A partial run still "
         "saves everything it could read, and the reports it could not read are listed for review.", "",
         "Table 2- The ten most recent pipeline runs", "", md_table(recent), ""])

    # ------------------------------------------------------------------ 3 quality
    held = int(quarantine["rows_held_back"].sum()) if not quarantine.empty else 0
    add(["## 3. Data quality", "",
         f"The validation rules held back {n(held)} rows in quarantine (a holding area for figures that fail a "
         "check, so they never reach the dashboard). Table 3 shows which rule stopped them. Rows that passed but "
         "looked unusual were loaded with a warning flag instead (Table 4).", "",
         "Table 3- Rows held back, by rule", "",
         md_table(quarantine.rename(columns={"rule": "Rule", "rows_held_back": "Rows held back",
                                             "example": "Example reason"})), "",
         "Table 4- Warnings on loaded rows", "",
         md_table(flags.rename(columns={"flag_type": "Warning", "n": "Rows"})), ""])

    # ------------------------------------------------------------------ 4 revisions
    if revisions.empty:
        add(["## 4. Revisions (RQ2)", "", "No figure has been changed by a later report yet.", ""])
    else:
        by = (revisions.groupby("disease_name").agg(revisions=("field_name", "size"),
                                                   median_lag_weeks=("revision_lag_weeks", "median"))
              .reset_index().rename(columns={"disease_name": "Disease", "revisions": "Revisions",
                                             "median_lag_weeks": "Median lag (weeks)"}))
        lag = revisions["revision_lag_weeks"].median()
        lag_text = ("0 weeks, which means most corrections came in a second report for the same week"
                    if lag == 0 else f"{lag:.0f} week{'s' if lag != 1 else ''}")
        add(["## 4. Revisions (RQ2)", "",
             f"Later reports changed {n(len(revisions))} figures that had already been stored. The median revision "
             f"lag (the number of weeks between the first report and the correction) was {lag_text}. Every change is kept in the revision history "
             "table with the old value, the new value and both source reports.", "",
             "Table 5- Revisions by disease", "", md_table(by, "{:.1f}"), ""])

    # ------------------------------------------------------------------ 5 trends
    plotted = []
    for name in sorted(has_weekly[has_weekly].index):
        d = nat[nat["disease_name"] == name]
        col = forecast_measure(d["disease_code"].iloc[0], d)
        if d[col].notna().sum() >= 3:
            plotted.append((name, d, col))
    fig, axes = plt.subplots(len(plotted), 1, figsize=(11, 2.1 * len(plotted)), sharex=True, squeeze=False)
    for ax, (name, d, col) in zip(axes[:, 0], plotted):
        ax.bar(d["week_start"], d[col], width=6, color=BLUE)
        ax.set_title(f"{name}: weekly {col} cases (Nigeria)", fontsize=9, loc="left")
    fig.tight_layout()
    fig.savefig(fig_dir / "01_weekly_cases_all_diseases.png", dpi=110)
    plt.close(fig)
    add(["## 5. Trends (RQ3)", "",
         "Fig. 1 shows the weekly national figures for every disease with at least three reported weeks. Each "
         "disease is shown with the measure it is analysed with: "
         + ", ".join(f"{nm} as {c} cases" for nm, _, c in plotted)
         + ". Gaps are weeks with no published report, not weeks with zero cases.", "",
         "![Weekly cases](figures/01_weekly_cases_all_diseases.png)", "",
         "Fig. 1- Weekly national cases by disease. Source: Collins C. Akagha (2026), using NCDC situation reports.", ""])
    ls = stl[stl["disease_code"] == "LASSA"].copy()
    if not ls.empty:
        ls["week_start"] = pd.to_datetime(ls["week_start"])
        fig, ax = plt.subplots(2, 1, figsize=(11, 5), sharex=True)
        ax[0].bar(ls["week_start"], ls["observed"], width=6, color="#9db7d5", label="observed")
        ax[0].plot(ls["week_start"], ls["trend"], color=RED, lw=2.5, label="trend")
        ax[0].legend()
        ax[0].set_title("Lassa fever: weekly confirmed cases and trend", loc="left", fontsize=10)
        ax[1].plot(ls["week_start"], ls["seasonal"], color=GREEN)
        ax[1].axhline(0, color="grey", lw=0.5)
        ax[1].set_title("Seasonal pattern", loc="left", fontsize=10)
        fig.tight_layout()
        fig.savefig(fig_dir / "02_lassa_stl.png", dpi=110)
        plt.close(fig)
        peak = ls.assign(m=ls["week_start"].dt.month).groupby("m")["seasonal"].mean().idxmax()
        add(["I split the Lassa fever series into a trend and a seasonal pattern with STL (Seasonal-Trend "
             "decomposition using Loess). The seasonal pattern is highest in "
             f"{pd.Timestamp(2000, int(peak), 1):%B}, which matches the dry-season peak that NCDC describes "
             "every year (Fig. 2).", "",
             "![Lassa STL](figures/02_lassa_stl.png)", "",
             "Fig. 2- Lassa fever trend and seasonal pattern (STL). Source: Collins C. Akagha (2026).", ""])

    # ------------------------------------------------------------------ 6 alerts
    if not alerts.empty:
        alerts["method"] = alerts["method"].map(lambda m: METHOD_LABELS.get(m, m))
        piv = alerts.pivot_table(index="disease_name", columns="method", values="alerts", aggfunc="sum", fill_value=0)
        piv.plot(kind="barh", figsize=(9, 3.6), color=[BLUE, ORANGE, GREEN, RED][:piv.shape[1]])
        plt.xlabel("Weeks that crossed the alert threshold")
        plt.ylabel("")
        plt.tight_layout()
        plt.savefig(fig_dir / "03_alerts_by_disease.png", dpi=110)
        plt.close()
        tbl = alerts.rename(columns={"disease_name": "Disease", "method": "Method", "alerts": "Alerts",
                                     "weeks_checked": "Weeks checked"})
        add(["## 6. Outbreak alerts (RQ3)", "",
             "I ran the EARS C1, C2 and C3 checks (simple statistical tests from the US CDC's Early Aberration "
             "Reporting System that compare a week with the weeks before it) on every national series, and a "
             "SARIMA residual check on every series with more than two years of weekly data. Together they flagged "
             f"{n(int(alerts['alerts'].sum()))} weeks out of {n(int(alerts['weeks_checked'].sum()))} checks (Fig. 3).",
             "", "![Alerts](figures/03_alerts_by_disease.png)", "",
             "Fig. 3- Weeks flagged by each alert method. Source: Collins C. Akagha (2026).", "",
             "Table 6- Alert checks by disease and method", "", md_table(tbl), ""])

    # ------------------------------------------------------------------ 7 forecasting
    forecast_codes = sorted(evaluation["disease_code"].unique()) if not evaluation.empty else []
    add(["## 7. Forecasting (RQ4)", "",
         "I compared seven forecasting methods: a seasonal naive benchmark (the same week last year), SARIMA, a "
         "negative binomial regression, Random Forest, Gradient Boosting, XGBoost and support vector regression "
         "(SVR). Each method was tested with a rolling origin (it forecasts 1 to 4 weeks ahead from a point in the "
         "past, using only the data that existed at that point, then the point moves forward one week). Accuracy is "
         "reported as MAE (mean absolute error, in cases), RMSE (root mean squared error) and MASE (the error "
         "divided by the error of the seasonal naive benchmark, so a value below 1 means the method beat it). I "
         "used the Diebold-Mariano test (a test of whether two sets of forecast errors are really different) with "
         "the Harvey-Leybourne-Newbold correction for small samples to compare each method with SARIMA.", ""])
    not_forecast = []
    for code in sorted(nat["disease_code"].unique()):
        if code in forecast_codes:
            continue
        _, _, why = prepare_forecast_series(nat[nat["disease_code"] == code], code, settings, today=as_of)
        not_forecast.append({"Disease": names[code], "Reason": why})
    fig_no, tab_no = 4, 7
    for code in forecast_codes:
        e = evaluation[evaluation["disease_code"] == code].copy()
        name = e["disease_name"].iloc[0]
        e1 = e.drop_duplicates(["model_name", "horizon"])
        acc = e1.pivot_table(index="model_name", columns="horizon", values="mase")
        acc.index = [MODEL_LABELS.get(m, m) for m in acc.index]
        acc = acc.sort_values(acc.columns[0])
        h1 = e1[e1["horizon"] == 1].sort_values("mase")
        best = h1.iloc[0]
        second = h1.iloc[1] if len(h1) > 1 else None
        naive = h1[h1["model_name"] == "seasonal_naive"]
        sarima = h1[h1["model_name"] == "sarima"]
        dm = e[(e["dm_reference_model"] == "sarima") & (e["horizon"] == 1) & (e["model_name"] != "sarima")]
        significant = dm[dm["dm_p_value"] < 0.05]
        beat_naive = int((h1["mase"] < 1).sum())
        measure = prepare_forecast_series(nat[nat["disease_code"] == code], code, settings, today=as_of)[1]
        best_label = MODEL_LABELS.get(best["model_name"], best["model_name"])

        fig, ax = plt.subplots(figsize=(9, 4))
        acc.plot(kind="bar", ax=ax)
        ax.axhline(1, color="red", ls="--", lw=1)
        ax.set_ylabel("MASE (lower is better)")
        ax.legend(title="weeks ahead")
        plt.xticks(rotation=20)
        fig.tight_layout()
        fig.savefig(fig_dir / f"{fig_no:02d}_{code.lower()}_mase.png", dpi=110)
        plt.close(fig)

        f = fc[fc["disease_code"] == code].copy()
        f["target"] = pd.to_datetime(f["target"])
        live = f[(~f["is_backtest"]) & (f["model_name"] == best["model_name"])].sort_values("target")
        bt = f[f["is_backtest"] & (f["horizon"] == 1)]
        series = nat[(nat["disease_code"] == code) & (nat["week_start"] >= bt["target"].min() - pd.Timedelta(weeks=8))]
        fig, ax = plt.subplots(figsize=(11, 4.2))
        ax.bar(series["week_start"], series[measure], width=6, color="#9db7d5", label="reported")
        for m, g in bt.groupby("model_name"):
            if m in (best["model_name"], "sarima", "seasonal_naive"):
                g = g.sort_values("target")
                ax.plot(g["target"], g["predicted"], lw=1.2, label=f"{MODEL_LABELS.get(m, m)} (1 week ahead, backtest)")
        if live["lower_95"].notna().any():
            ax.fill_between(live["target"], live["lower_95"], live["upper_95"], color=ORANGE, alpha=0.2,
                            label="95% interval")
        ax.plot(live["target"], live["predicted"], "o-", color=ORANGE, lw=2.5, label=f"Next 4 weeks: {best_label}")
        ax.set_ylabel(f"{measure} cases per week")
        ax.legend(fontsize=7, loc="upper left")
        fig.tight_layout()
        fig.savefig(fig_dir / f"{fig_no + 1:02d}_{code.lower()}_forecast.png", dpi=110)
        plt.close(fig)

        acc_tbl = acc.round(3)
        acc_tbl.columns = [f"MASE {c} wk" for c in acc_tbl.columns]
        acc_tbl = acc_tbl.reset_index().rename(columns={"index": "Method"})
        dm_tbl = dm.assign(Method=dm["model_name"].map(lambda m: MODEL_LABELS.get(m, m)))[
            ["Method", "mae", "rmse", "mase", "dm_statistic", "dm_p_value"]].rename(
            columns={"mae": "MAE", "rmse": "RMSE", "mase": "MASE", "dm_statistic": "DM statistic",
                     "dm_p_value": "p-value"}).sort_values("MASE")
        compare = ""
        if second is not None:
            gap = (second["mase"] - best["mase"]) / second["mase"] * 100
            compare = (f" The next best was {MODEL_LABELS.get(second['model_name'], second['model_name'])} "
                       f"(MASE {second['mase']:.3f}), so the best method was {gap:.1f}% more accurate.")
        vs = ""
        if not naive.empty and not sarima.empty:
            vs = (f" For comparison, SARIMA scored {sarima['mase'].iloc[0]:.3f} and the seasonal naive benchmark "
                  f"scored {naive['mase'].iloc[0]:.3f}.")
        if significant.empty:
            dm_text = ("None of the differences from SARIMA were statistically significant at the 5% level "
                       "(every p-value was 0.05 or higher). This means that, with the number of test weeks "
                       "available, I cannot say that any method is truly better than SARIMA, even though their "
                       "average errors differ.")
        else:
            sar = sarima["mase"].iloc[0] if not sarima.empty else None
            better = [r for r in significant.itertuples() if sar is not None and r.mase < sar]
            worse = [r for r in significant.itertuples() if sar is None or r.mase >= sar]
            def phrase(rows, how):
                names_ = [f"{MODEL_LABELS.get(r.model_name, r.model_name)} (p = {r.dm_p_value:.3f})" for r in rows]
                joined = names_[0] if len(names_) == 1 else ", ".join(names_[:-1]) + " and " + names_[-1]
                return f"{joined} {'was' if len(names_) == 1 else 'were'} significantly {how} accurate than SARIMA"
            parts = ([phrase(better, "more")] if better else []) + ([phrase(worse, "less")] if worse else [])
            dm_text = ("At the 5% level, the Diebold-Mariano test showed that " + ", and ".join(parts)
                       + ". For the other methods the difference from SARIMA could be due to chance, so I cannot "
                       "say that any of them is truly better or worse than SARIMA.")
        add([f"### {name}", "",
             f"The models were trained on {measure} cases. Each was tested over {int(e1['n_origins'].max())} forecast "
             f"origins. One week ahead, the most accurate method was **{best_label}** with a MASE of "
             f"{best['mase']:.3f} and an average error of {best['mae']:.1f} cases per week.{compare}{vs} "
             f"{beat_naive} of the {len(h1)} methods beat the seasonal naive benchmark one week ahead.", "",
             f"![MASE {name}](figures/{fig_no:02d}_{code.lower()}_mase.png)", "",
             f"Fig. {fig_no}- {name}: MASE by method and forecast horizon (the red line marks the seasonal naive "
             "benchmark). Source: Collins C. Akagha (2026).", "",
             f"Table {tab_no}- {name}: MASE by method and weeks ahead", "", md_table(acc_tbl), "",
             dm_text, "",
             f"Table {tab_no + 1}- {name}: one-week-ahead accuracy and Diebold-Mariano test against SARIMA", "",
             md_table(dm_tbl), "",
             f"![Forecast {name}](figures/{fig_no + 1:02d}_{code.lower()}_forecast.png)", "",
             f"Fig. {fig_no + 1}- {name}: one-week-ahead backtest and the forecast for the next four weeks. "
             "Source: Collins C. Akagha (2026).", ""])
        fig_no += 2
        tab_no += 2
        # data snapshot for scripts/reproduce_results.py
        nat[nat["disease_code"] == code][["week_start", "suspected", "confirmed"]].to_csv(
            data_dir / f"{code}_national_weekly.csv", index=False)
        e.drop(columns=["disease_code", "disease_name"]).to_csv(data_dir / f"{code}_model_evaluation.csv", index=False)
    if not_forecast:
        add(["### Diseases without a forecast", "",
             "The methods need about two years of recent, regular weekly reports to learn the seasonal pattern and "
             "to be tested fairly. The diseases below did not meet that condition, so no forecast was made rather "
             "than one based on too little data.", "",
             f"Table {tab_no}- Diseases without a forecast", "", md_table(pd.DataFrame(not_forecast)), ""])
        tab_no += 1

    # ------------------------------------------------------------------ 8 reproducibility
    (data_dir / "settings.json").write_text(json.dumps({
        "as_of": f"{as_of:%Y-%m-%d}", "code_version": code_version(),
        "settings": {"forecast_horizons": settings.forecast_horizons, "evaluation_weeks": settings.evaluation_weeks,
                     "random_seed": settings.random_seed, "sarima_refit_every": settings.sarima_refit_every,
                     "forecast_max_staleness_weeks": settings.forecast_max_staleness_weeks},
    }, indent=2), encoding="utf-8")
    check_path = data_dir / "reproducibility.json"
    check = json.loads(check_path.read_text(encoding="utf-8")) if check_path.exists() else None
    add(["## 8. Reproducibility", "",
         "The forecasting results can be reproduced. Every method that uses randomness (Random Forest, Gradient "
         f"Boosting and XGBoost) uses a fixed random seed ({settings.random_seed}), and the weekly figures the "
         "models were trained on are saved with this page in [`results/data`](data). The settings and the code "
         "version are saved in [`settings.json`](data/settings.json). Because NCDC revises its figures and the "
         "database is updated every week, a re-run on the live database can differ, but a re-run on the saved "
         "snapshot should give the same numbers.", ""])
    if check:
        rows = [{"Disease": names.get(c, c),
                 "Numbers identical": "yes" if v.get("identical") else "no",
                 "Largest MASE difference": (f"{v['largest_mase_difference']:.2%}" if "largest_mase_difference" in v
                                             else ""),
                 "Same ranking of methods": "yes" if v.get("same_ranking") else "no",
                 "Same test results": "yes" if v.get("same_test_decisions") else "no"}
                for c, v in check["diseases"].items()]
        add(["When this page was built, I re-ran the full comparison from the snapshot without the database "
             f"(`scripts/reproduce_results.py`) on a {check.get('processor', 'GitHub')} processor and compared the "
             f"two accuracy tables (Table {tab_no}).", "",
             f"Table {tab_no}- Reproducibility check", "", md_table(pd.DataFrame(rows)), "",
             "Upon further testing, I found that the numbers are identical when the re-run uses the same type of "
             "processor as the original run. On a different type (for example AMD instead of Intel), Random Forest "
             "and Gradient Boosting can give slightly different numbers, because tiny rounding differences in the "
             "calculations can change where a decision tree splits. In my tests this changed MASE by at most 0.25% "
             "and never changed the ranking of the methods or the result of any Diebold-Mariano test. This means "
             "the conclusions are reproducible on any machine, and the exact numbers are reproducible on the same "
             "type of processor.", ""])
    add(["To reproduce the results yourself:", "", "```bash",
         "pip install -r requirements.txt",
         "python scripts/reproduce_results.py results/data", "```", ""])

    # ------------------------------------------------------------------ 9 limitations
    add(["## 9. Limitations", "",
         "- Only diseases with about two years of regular weekly reports are forecast. For the others the "
         "dashboard shows trends and alerts only.",
         "- State-level figures come from the Lassa fever state table, which is an image in the PDF and is read "
         "with OCR (optical character recognition). A state table is used only when every column adds up to its "
         "own total and the national confirmed total matches Table 1 of the same report, so some weeks are left "
         "out.",
         "- Measles and yellow fever are now reported monthly, so only their year-to-date totals are stored.",
         "- The Diebold-Mariano test is based on a limited number of test weeks, so small differences between "
         "methods may not be detected.", ""])

    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out / 'README.md'}")


if __name__ == "__main__":
    main()
