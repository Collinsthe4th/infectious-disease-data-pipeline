# Parser coverage on real NCDC reports

Checked with the **NCDC parse check** workflow (`parse-check --download 3 --spread`), which downloads reports spread across each disease's listing on ncdc.gov.ng and parses them without a database. Figures below were compared by eye with the report text. Last checked: 18 Nov 2026.

| Disease | Report (epi week) | Layout | Result |
|---|---|---|---|
| Lassa fever | 2026-W44, 2022-W06 | Table 1 "Current week" / "<year> Cumulative" | ✅ weekly + year-to-date (suspected, confirmed, deaths, CFR) |
| Cholera | 2025-W09, 2022-W04 | Two captioned summary tables (current, cumulative) | ✅ weekly + year-to-date (suspected, deaths, CFR) |
| CSM | 2025-W14 | Table with "Epi Week 14" and season cumulative | ✅ weekly; season total (2024/2025) deliberately **not** stored as year-to-date |
| Diphtheria | 2026-W03 | Table 1 current week / cumulative | ✅ weekly + year-to-date |
| Diphtheria | 2025-W04 | Highlight sentences | ✅ weekly; outbreak total since 2022 deliberately not stored |
| Diphtheria | 2023-W03 | Infographic, weeks 1-3 total only | ➡️ manual queue (no single-week figures) |
| Mpox | 2024-W44 | Highlight sentences with "Cumulative Summary" | ✅ weekly + year-to-date |
| Mpox | 2023-W05 | "2023 Week 5 … Current/Cumulative" rows | ✅ weekly + year-to-date + CFR |
| Measles | Sept 2026 (W40) | **Monthly** report, Table 1 | ✅ year-to-date only (monthly figures never stored as weekly) |
| Measles | July 2024 (W31) | Monthly infographic | ➡️ manual queue |
| Yellow fever | May 2025 (W22) | **Monthly** sitrep, highlight sentences | ✅ year-to-date suspected |
| Reports before 2021 | various | older layouts | outside the study window → `ignored` |

## Known limits (to address in Phase 2)
- **State-level figures** are read only from layouts whose columns say "weekly" or "cumulative" explicitly (the synthetic layout and future verified templates). Real NCDC state tables often hold outbreak-to-date totals without saying so, so they are skipped rather than guessed. National series, which the forecasting and alerting use, are complete.
- **Monthly reports** (measles, yellow fever since 2024-25) give year-to-date totals at month ends. Weekly series for those diseases will have gaps; the dashboard shows them as gaps.
- **Highlight sentences** are a last resort and every value read that way carries a parse note; spot-check them.
- Run the parse check with a larger sample (`per_disease` = 10) before the backfill and record the hit rate for RQ1.
