"""Write a results page (results/README.md + charts) from whatever is in the database.

Used by the 'Results report' GitHub workflow after each database load, so the
analysed real NCDC data and the model results can be read on GitHub without
installing anything. Synthetic (demo) rows are never included.

    python scripts/make_results_report.py --out results
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.storage.db import get_engine  # noqa: E402

MODEL_LABELS = {"seasonal_naive": "Seasonal naive", "sarima": "SARIMA", "negative_binomial": "Negative binomial",
                "random_forest": "Random Forest", "gradient_boosting": "Gradient Boosting", "xgboost": "XGBoost",
                "svr": "SVR"}


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results")
    args = ap.parse_args()
    out = Path(args.out)
    fig_dir = out / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    with get_engine().connect() as conn:
        weekly = q(conn, "SELECT * FROM curated.v_weekly_cases WHERE NOT is_synthetic ORDER BY week_start")
        if weekly.empty:
            raise SystemExit("no real NCDC data in the database yet")
        stl = q(conn, """SELECT d.disease_code, w.week_start, s.observed, s.trend, s.seasonal
                          FROM analytics.stl_component s JOIN curated.dim_disease d USING (disease_id)
                          JOIN curated.dim_location l USING (location_id) JOIN curated.dim_epi_week w USING (week_id)
                         WHERE l.pcode = 'NG' ORDER BY w.week_start""")
        run_id = q(conn, "SELECT run_id::text AS r FROM analytics.forecast ORDER BY created_at DESC LIMIT 1")
        rid = run_id["r"].iloc[0] if not run_id.empty else None
        evaluation = q(conn, """SELECT model_name, horizon, n_origins, mae, rmse, mase, dm_reference_model,
                                       dm_statistic, dm_p_value FROM analytics.model_evaluation
                                 WHERE run_id = CAST(:r AS uuid)""", r=rid) if rid else pd.DataFrame()
        fc = q(conn, """SELECT f.model_name, f.is_backtest, f.horizon, t.week_start AS target, f.predicted, f.actual,
                               f.lower_80, f.upper_80, f.lower_95, f.upper_95
                          FROM analytics.forecast f JOIN curated.dim_epi_week t ON t.week_id = f.target_week_id
                         WHERE f.run_id = CAST(:r AS uuid)""", r=rid) if rid else pd.DataFrame()
        alerts = q(conn, """SELECT a.method, d.disease_name, COALESCE(l.state_name, 'Nigeria') AS location,
                                   w.week_start, a.observed, round(a.expected, 1) AS expected,
                                   round(a.statistic, 2) AS statistic
                              FROM analytics.alert a JOIN curated.dim_disease d USING (disease_id)
                              JOIN curated.dim_location l USING (location_id) JOIN curated.dim_epi_week w USING (week_id)
                             WHERE a.is_alert ORDER BY w.week_start DESC, a.statistic DESC""")
        runs = q(conn, """SELECT started_at, run_type, status, files_new, rows_extracted, rows_passed,
                                 rows_warned, rows_quarantined, revisions_detected,
                                 EXTRACT(EPOCH FROM (ended_at - started_at))::int AS seconds
                            FROM ops.pipeline_run_log ORDER BY started_at DESC LIMIT 12""")
        quarantine = q(conn, """SELECT COALESCE(r.rule_code, 'parse') AS rule, count(*) AS rows_held_back,
                                       min(q.failure_reason) AS example
                                  FROM ops.quarantine q LEFT JOIN ops.validation_rule r USING (rule_id)
                                 GROUP BY 1 ORDER BY 2 DESC""")
        flags = q(conn, "SELECT flag_type, count(*) AS n FROM ops.data_quality_flag GROUP BY 1 ORDER BY 2 DESC")
        revisions = q(conn, """SELECT d.disease_name, COALESCE(l.state_name, 'Nigeria') AS location,
                                      w.epi_year, w.epi_week, h.field_name, h.old_value, h.new_value,
                                      h.revision_lag_weeks
                                 FROM curated.revision_history h JOIN curated.dim_disease d USING (disease_id)
                                 JOIN curated.dim_location l USING (location_id) JOIN curated.dim_epi_week w USING (week_id)
                                ORDER BY h.revision_id""")

    nat = weekly[weekly["admin_level"] == 0].copy()
    nat["week_start"] = pd.to_datetime(nat["week_start"])

    # 1. every disease, national weekly cases
    has_weekly = nat.groupby("disease_name")[["suspected", "confirmed"]].apply(lambda g: g.notna().any().any())
    diseases = sorted(has_weekly[has_weekly].index)
    only_totals = sorted(has_weekly[~has_weekly].index)
    fig, axes = plt.subplots(len(diseases), 1, figsize=(11, 2.1 * len(diseases)), sharex=True, squeeze=False)
    for ax, name in zip(axes[:, 0], diseases):
        d = nat[nat["disease_name"] == name]
        col = "confirmed" if d["confirmed"].fillna(0).sum() > 0.3 * d["suspected"].fillna(0).sum() else "suspected"
        ax.bar(d["week_start"], d[col], width=6, color="#1f4e79")
        ax.set_title(f"{name} — weekly {col} cases (Nigeria)", fontsize=9, loc="left")
    fig.tight_layout()
    fig.savefig(fig_dir / "01_weekly_cases_all_diseases.png", dpi=110)
    plt.close(fig)

    # 2. Lassa STL
    ls = stl[stl["disease_code"] == "LASSA"].copy()
    if not ls.empty:
        ls["week_start"] = pd.to_datetime(ls["week_start"])
        fig, ax = plt.subplots(2, 1, figsize=(11, 5), sharex=True)
        ax[0].bar(ls["week_start"], ls["observed"], width=6, color="#9db7d5", label="observed")
        ax[0].plot(ls["week_start"], ls["trend"], color="#c0392b", lw=2.5, label="STL trend")
        ax[0].legend()
        ax[0].set_title("Lassa fever: weekly confirmed cases and trend", loc="left", fontsize=10)
        ax[1].plot(ls["week_start"], ls["seasonal"], color="#2c7a3f")
        ax[1].axhline(0, color="grey", lw=0.5)
        ax[1].set_title("Seasonal component (peaks each dry season, Dec–Apr)", loc="left", fontsize=10)
        fig.tight_layout()
        fig.savefig(fig_dir / "02_lassa_stl.png", dpi=110)
        plt.close(fig)

    # 3. model comparison + 4. forecast
    best = None
    if not evaluation.empty:
        acc = (evaluation.drop_duplicates(["model_name", "horizon"])
               .pivot_table(index="model_name", columns="horizon", values="mase"))
        acc.index = [MODEL_LABELS.get(m, m) for m in acc.index]
        acc = acc.sort_values(acc.columns[0])
        best = evaluation.drop_duplicates(["model_name", "horizon"]).query("horizon == 1").sort_values("mase").iloc[0]["model_name"]
        fig, ax = plt.subplots(figsize=(9, 4))
        acc.plot(kind="bar", ax=ax)
        ax.axhline(1, color="red", ls="--", lw=1)
        ax.set_ylabel("MASE (lower is better; < 1 beats the seasonal naive)")
        ax.set_title("Lassa fever forecast accuracy by model and horizon (weeks ahead)", loc="left", fontsize=10)
        ax.legend(title="weeks ahead")
        plt.xticks(rotation=20)
        fig.tight_layout()
        fig.savefig(fig_dir / "03_model_comparison_mase.png", dpi=110)
        plt.close(fig)

    if not fc.empty:
        fc["target"] = pd.to_datetime(fc["target"])
        live = fc[(~fc["is_backtest"]) & (fc["model_name"] == best)].sort_values("target")
        bt = fc[fc["is_backtest"] & (fc["horizon"] == 1)]
        lassa = nat[(nat["disease_code"] == "LASSA") & (nat["week_start"] >= bt["target"].min())]
        fig, ax = plt.subplots(figsize=(11, 4.2))
        ax.bar(lassa["week_start"], lassa["confirmed"], width=6, color="#9db7d5", label="observed")
        for name, g in bt.groupby("model_name"):
            if name in (best, "sarima", "seasonal_naive"):
                g = g.sort_values("target")
                ax.plot(g["target"], g["predicted"], lw=1.2, label=f"{MODEL_LABELS.get(name, name)} (1 week ahead, backtest)")
        if live["lower_95"].notna().any():
            ax.fill_between(live["target"], live["lower_95"], live["upper_95"], color="#e67e22", alpha=0.2, label="95% interval")
        ax.plot(live["target"], live["predicted"], "o-", color="#e67e22", lw=2.5, label=f"Next 4 weeks: {MODEL_LABELS.get(best, best)}")
        ax.legend(fontsize=7, loc="upper left")
        ax.set_title("Lassa fever: backtest and live forecast", loc="left", fontsize=10)
        fig.tight_layout()
        fig.savefig(fig_dir / "04_lassa_forecast.png", dpi=110)
        plt.close(fig)

    # page
    as_of = pd.Timestamp.now(tz="Africa/Lagos")
    coverage = (nat.groupby("disease_name")
                .agg(first_week=("week_start", "min"), last_week=("week_start", "max"), weeks=("week_start", "nunique"),
                     reports=("report_code", "nunique"))
                .reset_index().rename(columns={"disease_name": "disease"}))
    for c in ("first_week", "last_week"):
        coverage[c] = coverage[c].dt.strftime("%d %b %Y")
    lines = ["# Results", "",
             f"> **Real NCDC data, as of {as_of:%d %B %Y}.** Figures were read automatically from the Nigeria Centre "
             "for Disease Control and Prevention's situation reports and checked by the pipeline's validation rules. "
             "The four most recent weeks may still be revised by later reports. No synthetic (demo) data is included.", "",
             f"Generated by the *Results report* workflow after each database load. {len(weekly):,} figures for "
             f"{weekly['disease_name'].nunique()} diseases, {nat['week_start'].min():%d %b %Y} – "
             f"{nat['week_start'].max():%d %b %Y}.", "",
             "## 0. What is in the database", "", md_table(coverage), ""]
    if only_totals:
        lines += [f"Only running (year-to-date) totals are published in a readable form for: {', '.join(only_totals)}. "
                  "They are kept, but not shown as weekly charts.", ""]
    lines += ["## 1. Pipeline runs (RQ1)", "", "Most recent 12 runs:", "",
              md_table(runs.assign(started_at=runs["started_at"].astype(str).str[:16])), ""]
    lines += ["## 2. Data quality", "",
              "Rows held back for review (quarantine), by the rule that stopped them:", "",
              md_table(quarantine), "", "Warnings (row loaded, but flagged):", "", md_table(flags), ""]
    lines += ["## 3. Revisions detected (RQ2)", "",
              f"{len(revisions)} figures were changed by later reports. First 15:", "", md_table(revisions.head(15), "{:.0f}"), ""]
    lines += ["## 4. Trends (RQ3)", "", "![weekly cases](figures/01_weekly_cases_all_diseases.png)", "",
              "![Lassa STL](figures/02_lassa_stl.png)", ""]
    lines += ["## 5. Outbreak alerts (RQ3)", "", f"{len(alerts)} weeks crossed an alert threshold. Most recent 15:", "",
              md_table(alerts.head(15).assign(week_start=alerts.head(15)["week_start"].astype(str)), "{:.1f}"), ""]
    st = weekly[(weekly["admin_level"] == 1) & (weekly["disease_code"] == "LASSA")].copy()
    if not st.empty:
        st["week_start"] = pd.to_datetime(st["week_start"])
        yr = int(st["epi_year"].max())
        latest = (st[st["epi_year"] == yr].dropna(subset=["cum_confirmed"]).sort_values("week_start")
                  .groupby("location_name").tail(1))
        latest = latest.assign(per_100k=(latest["cum_confirmed"] * 100000 / latest["population"]).round(2))
        top = latest.sort_values("cum_confirmed", ascending=False).head(10)[
            ["location_name", "cum_confirmed", "per_100k"]].rename(
            columns={"location_name": "state", "cum_confirmed": f"confirmed {yr}", "per_100k": "per 100,000"})
        lines += ["## 5b. Lassa fever by state", "",
                  f"Confirmed cases so far in {yr} (Table 3 of the latest report, read by OCR and checked against "
                  "the national total). Top 10:", "", md_table(top, "{:.2f}"), ""]
    if not evaluation.empty:
        e = evaluation.copy()
        e["model_name"] = e["model_name"].map(lambda m: MODEL_LABELS.get(m, m))
        acc_tbl = e.drop_duplicates(["model_name", "horizon"]).pivot_table(index="model_name", columns="horizon",
                                                                         values="mase").round(3)
        acc_tbl.columns = [f"MASE {c} wk" for c in acc_tbl.columns]
        acc_tbl = acc_tbl.sort_values(acc_tbl.columns[0]).reset_index().rename(columns={"model_name": "model"})
        dm = e[(e["dm_reference_model"] == "sarima") & (e["horizon"] == 1)][["model_name", "mae", "rmse", "mase", "dm_statistic", "dm_p_value"]]
        lines += ["## 6. Lassa fever forecasting (RQ4)", "",
                  f"Rolling-origin evaluation over {int(e['n_origins'].max())} forecast origins. Best 1-week model: "
                  f"**{MODEL_LABELS.get(best, best)}**.", "", "![MASE](figures/03_model_comparison_mase.png)", "",
                  md_table(acc_tbl), "", "Diebold-Mariano test against SARIMA, 1 week ahead (negative = more accurate than SARIMA; "
                  "p < 0.05 = significant):", "", md_table(dm), "", "![forecast](figures/04_lassa_forecast.png)", ""]
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out / 'README.md'}")


if __name__ == "__main__":
    main()
