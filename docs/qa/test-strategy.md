# Test strategy

Read this before adding or changing a test.

## Unit tests (`tests/unit`, no database, run in seconds)
- **Epi weeks**: ISO week boundaries, 53-week years, week 1 starting in December.
- **Standardisation**: number parsing (thousand separators, footnotes, dash = missing), header mapping, every alias in the reference CSV resolves to the right HDX p-code.
- **Classification**: the real Lassa fever title page (week 44, 2026), year-crossing weeks, week from the listing title, old disease names.
- **PDF extraction**: synthetic PDFs in the NCDC layout → exact national and state figures; scanned/blank PDF and non-PDF go to the manual queue; manual CSV gives one report per disease-week.
- **Transformations**: weekly from cumulative (batch, database lookup, week 1, gaps, negative differences), CFR, short-gap interpolation with a mask.
- **Validation**: each of the 12 rules on a minimal frame, plus an engine test that one report's errors go to quarantine and warnings become flags.
- **Analytics**: EARS values against hand calculation, residual z uses only the past, metrics, Diebold-Mariano direction, seasonal naive, features never look ahead, rolling origin gives every model the same origins and **changing future data never changes an earlier forecast** (leakage test).
- **Ingestion**: listing parsing on HTML shaped like ncdc.gov.ng, retries with 10/20 s waits, giving up, robots.txt, idempotent download, non-PDF rejection, inbox scan.
- **Demo**: simulated data is internally consistent (states sum to national, cumulative totals).

## Integration tests (`tests/integration`, PostgreSQL in CI)
- `schema.sql` applies twice; ORM models match the DDL column for column; seeds (7 diseases, 12 rules, 313 weeks 2021-2026).
- SQL smoke script: duplicate hash rejected, constraint backstops for rules 2/3/4/8/10, revision trigger writes old/new values and both report ids.
- **End-to-end demo** (2021-W01 to 2023-W30): run status `success`, every document parsed, PDF reports loaded with `lassa_sitrep_v1`, exactly one quarantined row per broken rule (2, 3, 4, 8, 9), no duplicate grain, state sums equal national, revisions recorded, STL/alerts/forecasts/evaluations written.
- **Idempotency**: the same files again → zero new files, no change in facts or revisions.
- **Newest report wins**: a report for an earlier week never overwrites a later one.
- **Dashboard**: every page renders headlessly (Streamlit `AppTest`) with no exception.

## Real-data check (`NCDC parse check` workflow)
Downloads current reports from ncdc.gov.ng across the listing and parses them without a database. Runs weekly, on parser changes, and on demand. It is the early warning that NCDC changed a layout.

## Out of scope
The truth of NCDC's numbers (we check internal consistency, not reality); visual design (checked by eye); real ERA5/HDX downloads in CI (need keys or are slow).
