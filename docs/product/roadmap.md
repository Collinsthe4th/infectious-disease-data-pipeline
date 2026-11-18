# Roadmap

## Phase 1: working prototype ✅ (this release)
Crawler, inbox, parsing templates, validation, star schema with revision history, analytics, dashboard, Docker, CI, synthetic demo.

## Phase 2: real data (next)
1. Run the **NCDC parse check** workflow; fix or add templates until every disease parses.
2. Run `pipeline refresh-population`, commit the CSV, `pipeline init-db`.
3. Backfill 2021-2026 on a fresh database ([runbook](../operations/runbook.md)).
4. Hand-check a random sample of 30-50 reports to measure extraction accuracy (RQ1).
5. Start the weekly schedule and let it run for the monitored period (run success rate, RQ1).

## Phase 3: extensions named in Chapter 3
- ERA5 rainfall/temperature download (`cdsapi`), weekly state aggregation, lagged climate features for the Lassa models.
- WHO AFRO bulletin cross-check → `WHO_AFRO_MISMATCH` flags.
- ReliefWeb API check for missing or late reports.
- Great Expectations suite for the column-level rules (optional, see ADR 0005).
- Choropleth map from the HDX admin-1 GeoJSON.

## Ideas after the thesis
LGA-level parsing, e-mail/Telegram alert delivery, hosting the dashboard publicly with read-only credentials.
