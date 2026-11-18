# 0002 PostgreSQL star schema, DuckDB and Parquet as an analysis helper

Status: accepted

**Context.** The database must reject invalid rows even if a script has a bug, keep an audit trail, and stay cheap to run.

**Decision.** PostgreSQL holds a Kimball star schema: `fact_weekly_cases` (grain: disease × location × epi week) with `dim_disease`, `dim_location`, `dim_epi_week`, `dim_source_report`, plus `revision_history`. Important validation rules are repeated as CHECK, UNIQUE and FOREIGN KEY constraints. After each run the curated tables are exported to Parquet so notebooks can query them with DuckDB.

**Why.** PostgreSQL is open source, transactional and enforces constraints. A star schema keeps queries simple for the dashboard. DuckDB keeps heavy notebook queries off the main database (Raasveldt & Mühleisen, 2019).

**Consequences.** `schema.sql` is the single source of truth; `models.py` mirrors it and an integration test fails if they drift. Schema changes after first release go in `src/storage/migrations/` as forward-only scripts.
