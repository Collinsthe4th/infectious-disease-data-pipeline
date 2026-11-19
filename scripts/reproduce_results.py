"""Re-run the forecasting comparison from the saved data snapshot, without the database.

The results report saves, for every forecast disease, the national weekly figures the
models were trained on (results/data/<CODE>_national_weekly.csv), the accuracy table the
pipeline produced (results/data/<CODE>_model_evaluation.csv) and the settings used
(results/data/settings.json). This script trains and tests the same models on the same
figures with the same settings and random seed, and compares the two accuracy tables.

    python scripts/reproduce_results.py [results/data]

Writes results/data/reproducibility.json and exits with an error if the numbers differ.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.analytics.run import evaluate_models, prepare_forecast_series  # noqa: E402
from src.common.config import get_settings  # noqa: E402

TOLERANCE = 1e-6  # relative; anything larger means the result was not reproduced
COMPARED = ("mae", "rmse", "mase", "dm_statistic", "dm_p_value")


def main() -> int:
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "results/data")
    saved = json.loads((folder / "settings.json").read_text(encoding="utf-8"))
    settings = dataclasses.replace(get_settings(), **saved["settings"])
    as_of = pd.Timestamp(saved["as_of"])
    report = {"as_of": saved["as_of"], "code_version": saved.get("code_version"), "diseases": {}}
    ok_all = True
    for path in sorted(folder.glob("*_national_weekly.csv")):
        code = path.name.split("_national_weekly")[0]
        frame = pd.read_csv(path, parse_dates=["week_start"])
        y, measure, why = prepare_forecast_series(frame, code, settings, today=as_of)
        if y is None:
            report["diseases"][code] = {"reproduced": False, "reason": why}
            ok_all = False
            continue
        _, evaluation, _ = evaluate_models(y, settings)
        evaluation.to_csv(folder / f"{code}_reproduced_evaluation.csv", index=False)
        original = pd.read_csv(folder / f"{code}_model_evaluation.csv")
        keys = ["model_name", "horizon", "dm_reference_model"]
        merged = original.merge(evaluation, on=keys, suffixes=("_saved", "_rerun"), how="outer", indicator=True)
        worst = 0.0
        for col in COMPARED:
            a, b = merged[f"{col}_saved"].astype(float), merged[f"{col}_rerun"].astype(float)
            both_missing = a.isna() & b.isna()
            diff = ((a - b).abs() / a.abs().clip(lower=1e-9)).where(~both_missing, 0.0).fillna(float("inf"))
            worst = max(worst, float(diff.max()))
        same = worst <= TOLERANCE and (merged["_merge"] == "both").all()
        if not same:
            show = merged[keys + ["mase_saved", "mase_rerun", "dm_p_value_saved", "dm_p_value_rerun"]].copy()
            show["rel"] = ((show["mase_saved"] - show["mase_rerun"]).abs() / show["mase_saved"].abs()).round(6)
            print(show.sort_values("rel", ascending=False).head(12).to_string(index=False))
        ok_all &= same
        report["diseases"][code] = {"reproduced": bool(same), "measure": measure, "rows_compared": int(len(merged)),
                                    "largest_relative_difference": worst, "weeks_used": int(len(y))}
        print(f"{code}: {'reproduced' if same else 'DIFFERENT'} "
              f"({len(merged)} rows, largest relative difference {worst:.2e})")
    (folder / "reproducibility.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
