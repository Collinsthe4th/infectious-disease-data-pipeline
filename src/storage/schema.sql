-- =============================================================================
--  schema.sql
--  Design and Implementation of an Automated Data Pipeline That Monitors and
--  Analyzes Infectious Disease Trends in Nigeria  (Miva Open University)
--
--  Target: PostgreSQL 14+   (uses gen_random_uuid(), generated columns)
--
--  Storage zones (Chapter 3, Sections 3.3.1-3.3.3):
--    ops       - pipeline run log, validation rule catalogue, quarantine,
--                data-quality flags (orchestration + monitoring)
--    raw       - immutable register of every downloaded file (SHA-256 keyed)
--    staging   - validated rows waiting to be merged into the curated layer
--    curated   - Kimball star schema + revision history (analysis-ready)
--    analytics - outputs of STL, forecasting, evaluation and alerting jobs
--
--  Two-level schema enforcement (Section 3.5.1): the Python validation layer
--  applies all 12 rules in Table 3.4; this file repeats the most important
--  ones as NOT NULL / CHECK / UNIQUE / FOREIGN KEY constraints so that the
--  database rejects bad rows even if a script has a bug.
--
--  The script is idempotent: it can be re-run safely on an existing database.
-- =============================================================================

SET client_min_messages = warning;

BEGIN;

CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS curated;
CREATE SCHEMA IF NOT EXISTS analytics;

-- -----------------------------------------------------------------------------
-- Shared helper: keep updated_at current on every UPDATE
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION ops.touch_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;


-- =============================================================================
--  OPS: orchestration, monitoring and data quality
-- =============================================================================

-- One row per pipeline run (Section 3.3.4). Feeds the run-success-rate metric
-- (RQ1) and the dashboard's pipeline health panel.
CREATE TABLE IF NOT EXISTS ops.pipeline_run_log (
    run_id                   uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    run_type                 text         NOT NULL
        CHECK (run_type IN ('scheduled', 'daily_check', 'backfill', 'manual')),
    dag_id                   text,
    trigger_source           text         NOT NULL DEFAULT 'cron'
        CHECK (trigger_source IN ('airflow', 'cron', 'cli')),
    started_at               timestamptz  NOT NULL DEFAULT now(),
    ended_at                 timestamptz,
    status                   text         NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'success', 'partial', 'failed')),
    files_discovered         integer      NOT NULL DEFAULT 0 CHECK (files_discovered >= 0),
    files_new                integer      NOT NULL DEFAULT 0 CHECK (files_new >= 0),
    files_skipped_duplicate  integer      NOT NULL DEFAULT 0 CHECK (files_skipped_duplicate >= 0),
    files_manual_queue       integer      NOT NULL DEFAULT 0 CHECK (files_manual_queue >= 0),
    rows_extracted           integer      NOT NULL DEFAULT 0 CHECK (rows_extracted >= 0),
    rows_passed              integer      NOT NULL DEFAULT 0 CHECK (rows_passed >= 0),
    rows_warned              integer      NOT NULL DEFAULT 0 CHECK (rows_warned >= 0),
    rows_quarantined         integer      NOT NULL DEFAULT 0 CHECK (rows_quarantined >= 0),
    revisions_detected       integer      NOT NULL DEFAULT 0 CHECK (revisions_detected >= 0),
    attempt_number           smallint     NOT NULL DEFAULT 1 CHECK (attempt_number BETWEEN 1 AND 4),
    code_version             text,        -- git commit SHA of the deployed code
    error_message            text,
    CONSTRAINT ck_run_end_after_start CHECK (ended_at IS NULL OR ended_at >= started_at),
    CONSTRAINT ck_run_finished_has_end CHECK (status = 'running' OR ended_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_run_log_started ON ops.pipeline_run_log (started_at DESC);
CREATE INDEX IF NOT EXISTS ix_run_log_status  ON ops.pipeline_run_log (status);

-- Catalogue of the 12 validation rules in Table 3.4 (seeded below).
CREATE TABLE IF NOT EXISTS ops.validation_rule (
    rule_id      smallint  PRIMARY KEY CHECK (rule_id BETWEEN 1 AND 12),
    rule_code    text      NOT NULL UNIQUE,
    description  text      NOT NULL,
    category     text      NOT NULL
        CHECK (category IN ('representational', 'intrinsic', 'contextual_timeliness',
                            'contextual_completeness')),
    severity     text      NOT NULL CHECK (severity IN ('error', 'warning')),
    action       text      NOT NULL
);


-- =============================================================================
--  RAW: immutable register of downloaded / hand-placed files
-- =============================================================================

CREATE TABLE IF NOT EXISTS raw.documents (
    document_id       bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sha256            char(64)     NOT NULL UNIQUE
        CHECK (sha256 ~ '^[0-9a-f]{64}$'),                     -- idempotency key
    source_name       text         NOT NULL
        CHECK (source_name IN ('ncdc', 'hdx', 'who_afro', 'reliefweb', 'era5', 'manual_inbox')),
    source_url        text,                                    -- NULL for inbox files
    file_path         text         NOT NULL,                   -- path inside data/raw/
    file_name         text         NOT NULL,
    content_type      text,
    file_size_bytes   bigint       CHECK (file_size_bytes >= 0),
    has_text_layer    boolean,                                 -- NULL until checked
    page_count        integer      CHECK (page_count >= 0),
    downloaded_at     timestamptz  NOT NULL DEFAULT now(),
    run_id            uuid         NOT NULL REFERENCES ops.pipeline_run_log (run_id),
    processing_status text         NOT NULL DEFAULT 'new'
        CHECK (processing_status IN ('new', 'parsed', 'parse_failed',
                                     'manual_queue', 'manually_entered', 'ignored')),
    status_reason     text,
    -- metadata read from the NCDC listing page by the crawler
    listing_title     text,          -- e.g. 'An update of Lassa fever outbreak in Nigeria for Week 44'
    listing_date      date,          -- upload date when the listing shows one (| ddmmyy)
    disease_hint      text,          -- disease_code of the listing category
    -- TRUE only for files produced by the demo generator; never real NCDC data
    is_synthetic      boolean      NOT NULL DEFAULT false,
    CONSTRAINT ck_doc_url_unless_inbox
        CHECK (source_name = 'manual_inbox' OR source_url IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_documents_status ON raw.documents (processing_status);
CREATE INDEX IF NOT EXISTS ix_documents_run    ON raw.documents (run_id);
-- The same URL may legitimately serve a re-issued file with a new hash, so
-- source_url is indexed but NOT unique; sha256 is the deduplication key.
CREATE INDEX IF NOT EXISTS ix_documents_url    ON raw.documents (source_url);


-- =============================================================================
--  CURATED: dimension tables
-- =============================================================================

-- Seven priority diseases (seeded below).
CREATE TABLE IF NOT EXISTS curated.dim_disease (
    disease_id        smallint  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    disease_code      text      NOT NULL UNIQUE CHECK (disease_code ~ '^[A-Z_]+$'),
    disease_name      text      NOT NULL UNIQUE,
    icd10_code        text,
    -- CFR denominator NCDC uses for this disease (Section 3.5.3):
    -- 'confirmed' for e.g. Lassa fever, 'suspected' for e.g. cholera.
    cfr_denominator   text      NOT NULL DEFAULT 'confirmed'
        CHECK (cfr_denominator IN ('confirmed', 'suspected')),
    is_forecast_target boolean  NOT NULL DEFAULT false
);

-- National (level 0), state (level 1) and LGA (level 2) units keyed on HDX
-- p-codes from the Nigeria COD-AB dataset.
CREATE TABLE IF NOT EXISTS curated.dim_location (
    location_id         integer   GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    admin_level         smallint  NOT NULL CHECK (admin_level IN (0, 1, 2)),
    pcode               text      NOT NULL UNIQUE CHECK (pcode ~ '^NG[0-9]*$'),
    parent_location_id  integer   REFERENCES curated.dim_location (location_id),
    country_name        text      NOT NULL DEFAULT 'Nigeria',
    state_name          text,
    lga_name            text,
    geo_zone            text
        CHECK (geo_zone IS NULL OR geo_zone IN ('North Central', 'North East', 'North West',
                                                'South East', 'South South', 'South West')),
    population          bigint    CHECK (population IS NULL OR population > 0),
    population_year     smallint  CHECK (population_year IS NULL OR population_year BETWEEN 2006 AND 2035),
    population_source   text,
    CONSTRAINT ck_loc_level_shape CHECK (
        (admin_level = 0 AND parent_location_id IS NULL AND state_name IS NULL AND lga_name IS NULL)
     OR (admin_level = 1 AND parent_location_id IS NOT NULL AND state_name IS NOT NULL AND lga_name IS NULL)
     OR (admin_level = 2 AND parent_location_id IS NOT NULL AND state_name IS NOT NULL AND lga_name IS NOT NULL)
    ),
    CONSTRAINT uq_loc_state_lga UNIQUE NULLS NOT DISTINCT (admin_level, state_name, lga_name)
);
CREATE INDEX IF NOT EXISTS ix_location_parent ON curated.dim_location (parent_location_id);

-- Alias dictionary used for name standardisation (Section 3.4.4, step 4;
-- validation rule 9). e.g. 'FCT', 'Abuja' -> Federal Capital Territory.
CREATE TABLE IF NOT EXISTS curated.dim_location_alias (
    alias_normalised  text     PRIMARY KEY
        CHECK (alias_normalised = lower(btrim(alias_normalised))),
    location_id       integer  NOT NULL REFERENCES curated.dim_location (location_id) ON DELETE CASCADE,
    alias_source      text     NOT NULL DEFAULT 'manual'
);
CREATE INDEX IF NOT EXISTS ix_alias_location ON curated.dim_location_alias (location_id);

-- Epidemiological (ISO) weeks. week_id = epi_year*100 + epi_week, e.g. 202405,
-- so it is human-readable and sorts chronologically.
CREATE TABLE IF NOT EXISTS curated.dim_epi_week (
    week_id     integer   PRIMARY KEY,
    epi_year    smallint  NOT NULL CHECK (epi_year BETWEEN 2015 AND 2040),
    epi_week    smallint  NOT NULL CHECK (epi_week BETWEEN 1 AND 53),            -- rule 8
    week_start  date      NOT NULL UNIQUE,
    week_end    date      NOT NULL UNIQUE,
    month       smallint  NOT NULL CHECK (month BETWEEN 1 AND 12),             -- month of week_start
    quarter     smallint  NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    -- Nigeria's Lassa fever peak season is roughly December-April
    is_dry_season boolean NOT NULL,
    CONSTRAINT uq_epi_year_week   UNIQUE (epi_year, epi_week),
    CONSTRAINT ck_week_id_matches CHECK (week_id = epi_year * 100 + epi_week),
    CONSTRAINT ck_week_monday     CHECK (EXTRACT(ISODOW FROM week_start) = 1),
    CONSTRAINT ck_week_length     CHECK (week_end = week_start + 6),
    CONSTRAINT ck_week_iso_year   CHECK (EXTRACT(ISOYEAR FROM week_start) = epi_year),
    CONSTRAINT ck_week_iso_week   CHECK (EXTRACT(WEEK FROM week_start) = epi_week)
);

-- Parsed metadata of every report that contributed figures.
CREATE TABLE IF NOT EXISTS curated.dim_source_report (
    report_id        integer      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    report_code      text         NOT NULL UNIQUE,   -- e.g. NCDC-SITREP-LASSA-2024-W05
    -- not unique: one multi-disease or manual-entry file can yield several reports
    document_id      bigint       NOT NULL REFERENCES raw.documents (document_id),
    report_type      text         NOT NULL
        CHECK (report_type IN ('weekly_epi_report', 'situation_report',
                               'who_afro_bulletin', 'manual_entry')),
    publisher        text         NOT NULL DEFAULT 'NCDC',
    disease_id       smallint     REFERENCES curated.dim_disease (disease_id), -- NULL = multi-disease WER
    week_id          integer      NOT NULL REFERENCES curated.dim_epi_week (week_id),
    published_date   date,
    printed_week_start date,     -- dates as printed, kept for rule 8 cross-check
    printed_week_end   date,
    template_id      text,        -- layout template that parsed it
    extraction_engine text        CHECK (extraction_engine IN ('pdfplumber', 'camelot', 'manual')),
    title            text,
    is_synthetic     boolean      NOT NULL DEFAULT false,
    created_at       timestamptz  NOT NULL DEFAULT now(),
    -- days between end of epi week and publication (rule 11, timeliness)
    CONSTRAINT ck_printed_dates CHECK (printed_week_end IS NULL OR printed_week_start IS NULL
                                       OR printed_week_end >= printed_week_start)
);
CREATE INDEX IF NOT EXISTS ix_report_week    ON curated.dim_source_report (week_id);
CREATE INDEX IF NOT EXISTS ix_report_disease ON curated.dim_source_report (disease_id);
CREATE INDEX IF NOT EXISTS ix_report_document ON curated.dim_source_report (document_id);


-- =============================================================================
--  CURATED: fact tables
-- =============================================================================

-- Grain: one row per disease x location x epidemiological week (Fig. 3.3).
-- report_id always points to the most recent report that supplied the values;
-- older values are preserved in curated.revision_history.
CREATE TABLE IF NOT EXISTS curated.fact_weekly_cases (
    fact_id          bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    disease_id       smallint      NOT NULL REFERENCES curated.dim_disease (disease_id),
    location_id      integer       NOT NULL REFERENCES curated.dim_location (location_id),
    week_id          integer       NOT NULL REFERENCES curated.dim_epi_week (week_id),
    report_id        integer       NOT NULL REFERENCES curated.dim_source_report (report_id),

    -- weekly (new) counts; NULL = not reported (never coerced to 0)
    suspected        integer       CHECK (suspected  IS NULL OR suspected  >= 0),   -- rule 2
    confirmed        integer       CHECK (confirmed  IS NULL OR confirmed  >= 0),   -- rule 2
    deaths           integer       CHECK (deaths     IS NULL OR deaths     >= 0),   -- rule 2
    -- year-to-date totals as printed
    cum_suspected    integer       CHECK (cum_suspected IS NULL OR cum_suspected >= 0),
    cum_confirmed    integer       CHECK (cum_confirmed IS NULL OR cum_confirmed >= 0),
    cum_deaths       integer       CHECK (cum_deaths    IS NULL OR cum_deaths    >= 0),

    cfr_pct          numeric(6,2)  CHECK (cfr_pct      IS NULL OR cfr_pct      BETWEEN 0 AND 100),
    cfr_reported     numeric(6,2)  CHECK (cfr_reported IS NULL OR cfr_reported BETWEEN 0 AND 100),

    is_provisional           boolean  NOT NULL DEFAULT true,   -- 4 most recent weeks
    is_derived_from_cumulative boolean NOT NULL DEFAULT false, -- weekly = cum_t - cum_{t-1}
    quality_flags            text[]   NOT NULL DEFAULT '{}',   -- warning rule codes, e.g. {R05_CFR_MISMATCH}

    loaded_at        timestamptz   NOT NULL DEFAULT now(),
    updated_at       timestamptz   NOT NULL DEFAULT now(),
    last_run_id      uuid          REFERENCES ops.pipeline_run_log (run_id),

    CONSTRAINT uq_fact_grain UNIQUE (disease_id, location_id, week_id),
    -- rule 3: confirmed <= suspected
    CONSTRAINT ck_confirmed_le_suspected
        CHECK (confirmed IS NULL OR suspected IS NULL OR confirmed <= suspected),
    CONSTRAINT ck_cum_confirmed_le_cum_suspected
        CHECK (cum_confirmed IS NULL OR cum_suspected IS NULL OR cum_confirmed <= cum_suspected),
    -- rule 4 (DB backstop): deaths cannot exceed the larger available case
    -- count. The exact per-disease denominator check runs in Python, because
    -- a CHECK constraint cannot look up dim_disease.cfr_denominator.
    CONSTRAINT ck_deaths_le_cases
        CHECK (deaths IS NULL OR deaths <= GREATEST(COALESCE(suspected, 0), COALESCE(confirmed, 0))
               OR (suspected IS NULL AND confirmed IS NULL)),
    CONSTRAINT ck_cum_deaths_le_cases
        CHECK (cum_deaths IS NULL OR cum_deaths <= GREATEST(COALESCE(cum_suspected, 0), COALESCE(cum_confirmed, 0))
               OR (cum_suspected IS NULL AND cum_confirmed IS NULL)),
    -- a fact row must carry at least one figure
    CONSTRAINT ck_fact_has_value
        CHECK (COALESCE(suspected, confirmed, deaths, cum_suspected, cum_confirmed, cum_deaths) IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_fact_week            ON curated.fact_weekly_cases (week_id);
CREATE INDEX IF NOT EXISTS ix_fact_location        ON curated.fact_weekly_cases (location_id);
CREATE INDEX IF NOT EXISTS ix_fact_report          ON curated.fact_weekly_cases (report_id);
CREATE INDEX IF NOT EXISTS ix_fact_disease_week    ON curated.fact_weekly_cases (disease_id, week_id);

DROP TRIGGER IF EXISTS trg_fact_touch ON curated.fact_weekly_cases;
CREATE TRIGGER trg_fact_touch
    BEFORE UPDATE ON curated.fact_weekly_cases
    FOR EACH ROW EXECUTE FUNCTION ops.touch_updated_at();

-- Weekly climate covariates per state for the Lassa fever models (ERA5).
CREATE TABLE IF NOT EXISTS curated.fact_weekly_climate (
    location_id    integer       NOT NULL REFERENCES curated.dim_location (location_id),
    week_id        integer       NOT NULL REFERENCES curated.dim_epi_week (week_id),
    rainfall_mm    numeric(8,2)  CHECK (rainfall_mm IS NULL OR rainfall_mm >= 0),
    temp_c         numeric(5,2)  CHECK (temp_c IS NULL OR temp_c BETWEEN -10 AND 55),
    source         text          NOT NULL DEFAULT 'era5',
    document_id    bigint        REFERENCES raw.documents (document_id),
    loaded_at      timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (location_id, week_id)
);


-- =============================================================================
--  CURATED: revision history (audit trail, answers RQ2)
-- =============================================================================
-- One row per changed measure. Written automatically by the trigger below
-- whenever an UPSERT changes a stored figure, so the audit trail cannot be
-- skipped by application code. The pipeline sets the run id per transaction:
--     SET LOCAL pipeline.run_id = '<uuid>';
CREATE TABLE IF NOT EXISTS curated.revision_history (
    revision_id     bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fact_id         bigint       NOT NULL REFERENCES curated.fact_weekly_cases (fact_id) ON DELETE CASCADE,
    disease_id      smallint     NOT NULL REFERENCES curated.dim_disease (disease_id),
    location_id     integer      NOT NULL REFERENCES curated.dim_location (location_id),
    week_id         integer      NOT NULL REFERENCES curated.dim_epi_week (week_id),
    field_name      text         NOT NULL
        CHECK (field_name IN ('suspected', 'confirmed', 'deaths',
                              'cum_suspected', 'cum_confirmed', 'cum_deaths',
                              'cfr_pct', 'cfr_reported')),
    old_value       numeric,
    new_value       numeric,
    abs_change      numeric      GENERATED ALWAYS AS (new_value - old_value) STORED,
    rel_change      numeric      GENERATED ALWAYS AS (
                        CASE WHEN old_value IS NULL OR old_value = 0 OR new_value IS NULL THEN NULL
                             ELSE (new_value - old_value) / old_value END) STORED,
    old_report_id   integer      NOT NULL REFERENCES curated.dim_source_report (report_id),
    new_report_id   integer      NOT NULL REFERENCES curated.dim_source_report (report_id),
    -- weeks between the original report's epi week and the revising report's
    revision_lag_weeks integer,
    run_id          uuid         REFERENCES ops.pipeline_run_log (run_id),
    detected_at     timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT ck_revision_changed CHECK (old_value IS DISTINCT FROM new_value)
);
CREATE INDEX IF NOT EXISTS ix_revision_fact    ON curated.revision_history (fact_id);
CREATE INDEX IF NOT EXISTS ix_revision_grain   ON curated.revision_history (disease_id, location_id, week_id);
CREATE INDEX IF NOT EXISTS ix_revision_report  ON curated.revision_history (new_report_id);

CREATE OR REPLACE FUNCTION curated.capture_fact_revision()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_run_id   uuid;
    v_lag      integer;
    v_field    text;
    v_old      numeric;
    v_new      numeric;
BEGIN
    v_run_id := NULLIF(current_setting('pipeline.run_id', true), '')::uuid;

    -- weeks between the fact's epi week and the epi week of the revising report
    SELECT (nw.week_start - ow.week_start) / 7
      INTO v_lag
      FROM curated.dim_source_report nr
      JOIN curated.dim_epi_week nw ON nw.week_id = nr.week_id
      JOIN curated.dim_epi_week ow ON ow.week_id = OLD.week_id
     WHERE nr.report_id = NEW.report_id;

    FOR v_field, v_old, v_new IN
        SELECT * FROM (VALUES
            ('suspected',     OLD.suspected::numeric,     NEW.suspected::numeric),
            ('confirmed',     OLD.confirmed::numeric,     NEW.confirmed::numeric),
            ('deaths',        OLD.deaths::numeric,        NEW.deaths::numeric),
            ('cum_suspected', OLD.cum_suspected::numeric, NEW.cum_suspected::numeric),
            ('cum_confirmed', OLD.cum_confirmed::numeric, NEW.cum_confirmed::numeric),
            ('cum_deaths',    OLD.cum_deaths::numeric,    NEW.cum_deaths::numeric),
            ('cfr_pct',       OLD.cfr_pct,                NEW.cfr_pct),
            ('cfr_reported',  OLD.cfr_reported,           NEW.cfr_reported)
        ) AS f(field_name, old_value, new_value)
        WHERE f.old_value IS DISTINCT FROM f.new_value
    LOOP
        INSERT INTO curated.revision_history
            (fact_id, disease_id, location_id, week_id, field_name,
             old_value, new_value, old_report_id, new_report_id,
             revision_lag_weeks, run_id)
        VALUES
            (OLD.fact_id, OLD.disease_id, OLD.location_id, OLD.week_id, v_field,
             v_old, v_new, OLD.report_id, NEW.report_id, v_lag, v_run_id);
    END LOOP;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_fact_revision ON curated.fact_weekly_cases;
CREATE TRIGGER trg_fact_revision
    AFTER UPDATE ON curated.fact_weekly_cases
    FOR EACH ROW
    WHEN (OLD.suspected     IS DISTINCT FROM NEW.suspected
       OR OLD.confirmed     IS DISTINCT FROM NEW.confirmed
       OR OLD.deaths        IS DISTINCT FROM NEW.deaths
       OR OLD.cum_suspected IS DISTINCT FROM NEW.cum_suspected
       OR OLD.cum_confirmed IS DISTINCT FROM NEW.cum_confirmed
       OR OLD.cum_deaths    IS DISTINCT FROM NEW.cum_deaths
       OR OLD.cfr_pct       IS DISTINCT FROM NEW.cfr_pct
       OR OLD.cfr_reported  IS DISTINCT FROM NEW.cfr_reported)
    EXECUTE FUNCTION curated.capture_fact_revision();


-- =============================================================================
--  STAGING: validated rows waiting to be merged (Section 3.3.2, step 4)
-- =============================================================================
-- Uses natural keys (codes, not surrogate ids) so extraction code does not
-- need to look up dimension ids. Rows are deleted after a successful merge.
CREATE TABLE IF NOT EXISTS staging.weekly_cases (
    staging_id       bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id           uuid         NOT NULL REFERENCES ops.pipeline_run_log (run_id),
    report_id        integer      NOT NULL REFERENCES curated.dim_source_report (report_id),
    disease_code     text         NOT NULL,
    pcode            text         NOT NULL,
    epi_year         smallint     NOT NULL,
    epi_week         smallint     NOT NULL CHECK (epi_week BETWEEN 1 AND 53),
    suspected        integer      CHECK (suspected  IS NULL OR suspected  >= 0),
    confirmed        integer      CHECK (confirmed  IS NULL OR confirmed  >= 0),
    deaths           integer      CHECK (deaths     IS NULL OR deaths     >= 0),
    cum_suspected    integer      CHECK (cum_suspected IS NULL OR cum_suspected >= 0),
    cum_confirmed    integer      CHECK (cum_confirmed IS NULL OR cum_confirmed >= 0),
    cum_deaths       integer      CHECK (cum_deaths    IS NULL OR cum_deaths    >= 0),
    cfr_pct          numeric(6,2),
    cfr_reported     numeric(6,2),
    is_derived_from_cumulative boolean NOT NULL DEFAULT false,
    quality_flags    text[]       NOT NULL DEFAULT '{}',
    source_page      integer,
    created_at       timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT uq_staging_grain UNIQUE (report_id, disease_code, pcode, epi_year, epi_week) -- rule 10
);


-- =============================================================================
--  OPS: quarantine and data-quality flags
-- =============================================================================

-- Error-level failures (rules 1-4, 8-10) and unparseable reports.
CREATE TABLE IF NOT EXISTS ops.quarantine (
    quarantine_id    bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id           uuid         NOT NULL REFERENCES ops.pipeline_run_log (run_id),
    document_id      bigint       NOT NULL REFERENCES raw.documents (document_id),
    report_id        integer      REFERENCES curated.dim_source_report (report_id),
    rule_id          smallint     REFERENCES ops.validation_rule (rule_id), -- NULL = parse failure
    scope            text         NOT NULL CHECK (scope IN ('row', 'table', 'document')),
    failure_reason   text         NOT NULL,
    payload          jsonb,       -- the offending row / table preview as extracted
    source_page      integer,
    quarantined_at   timestamptz  NOT NULL DEFAULT now(),
    review_status    text         NOT NULL DEFAULT 'open'
        CHECK (review_status IN ('open', 'corrected_and_loaded', 'confirmed_invalid', 'ignored')),
    reviewed_at      timestamptz,
    review_note      text,
    CONSTRAINT ck_quarantine_review CHECK (review_status = 'open' OR reviewed_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_quarantine_open ON ops.quarantine (review_status) WHERE review_status = 'open';
CREATE INDEX IF NOT EXISTS ix_quarantine_rule ON ops.quarantine (rule_id);
CREATE INDEX IF NOT EXISTS ix_quarantine_run  ON ops.quarantine (run_id);

-- Warning-level results (rules 5, 6, 7, 11, 12) and WHO AFRO cross-check
-- mismatches (Section 3.4.1). Rows are loaded; the flag is recorded here.
CREATE TABLE IF NOT EXISTS ops.data_quality_flag (
    flag_id          bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id           uuid         NOT NULL REFERENCES ops.pipeline_run_log (run_id),
    rule_id          smallint     REFERENCES ops.validation_rule (rule_id),
    flag_type        text         NOT NULL,   -- e.g. R05_CFR_MISMATCH, WHO_AFRO_MISMATCH, R12_MISSING_WEEK
    fact_id          bigint       REFERENCES curated.fact_weekly_cases (fact_id) ON DELETE CASCADE,
    report_id        integer      REFERENCES curated.dim_source_report (report_id),
    disease_id       smallint     REFERENCES curated.dim_disease (disease_id),
    location_id      integer      REFERENCES curated.dim_location (location_id),
    week_id          integer      REFERENCES curated.dim_epi_week (week_id),
    detail           jsonb        NOT NULL DEFAULT '{}'::jsonb,
    flagged_at       timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_dq_flag_type  ON ops.data_quality_flag (flag_type);
CREATE INDEX IF NOT EXISTS ix_dq_flag_grain ON ops.data_quality_flag (disease_id, location_id, week_id);
-- a missing week (rule 12) is recorded once, not on every run
CREATE UNIQUE INDEX IF NOT EXISTS uq_dq_gap_once
    ON ops.data_quality_flag (flag_type, disease_id, location_id, week_id)
    WHERE flag_type = 'R12_COMPLETENESS';


-- =============================================================================
--  ANALYTICS: model outputs (written by Layer 6, read by the dashboard)
-- =============================================================================

-- STL decomposition components (RQ3).
CREATE TABLE IF NOT EXISTS analytics.stl_component (
    run_id        uuid      NOT NULL REFERENCES ops.pipeline_run_log (run_id),
    disease_id    smallint  NOT NULL REFERENCES curated.dim_disease (disease_id),
    location_id   integer   NOT NULL REFERENCES curated.dim_location (location_id),
    week_id       integer   NOT NULL REFERENCES curated.dim_epi_week (week_id),
    series_measure text     NOT NULL DEFAULT 'confirmed'
        CHECK (series_measure IN ('suspected', 'confirmed', 'deaths')),
    observed      numeric,
    trend         numeric,
    seasonal      numeric,
    remainder     numeric,
    is_interpolated boolean NOT NULL DEFAULT false,
    PRIMARY KEY (run_id, disease_id, location_id, week_id, series_measure)
);

-- Point and interval forecasts. is_backtest = true for rolling-origin
-- evaluation forecasts, false for live forward forecasts shown on the dashboard.
CREATE TABLE IF NOT EXISTS analytics.forecast (
    forecast_id     bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id          uuid         NOT NULL REFERENCES ops.pipeline_run_log (run_id),
    model_name      text         NOT NULL
        CHECK (model_name IN ('seasonal_naive', 'sarima', 'negative_binomial',
                              'random_forest', 'gradient_boosting', 'xgboost', 'svr')),
    model_version   text,
    disease_id      smallint     NOT NULL REFERENCES curated.dim_disease (disease_id),
    location_id     integer      NOT NULL REFERENCES curated.dim_location (location_id),
    origin_week_id  integer      NOT NULL REFERENCES curated.dim_epi_week (week_id),
    target_week_id  integer      NOT NULL REFERENCES curated.dim_epi_week (week_id),
    horizon         smallint     NOT NULL CHECK (horizon BETWEEN 1 AND 4),
    predicted       numeric      NOT NULL CHECK (predicted >= 0),
    lower_80        numeric,
    upper_80        numeric,
    lower_95        numeric,
    upper_95        numeric,
    actual          numeric,     -- filled in once the target week is observed
    is_backtest     boolean      NOT NULL DEFAULT false,
    params          jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT ck_forecast_target_after_origin CHECK (target_week_id > origin_week_id),
    CONSTRAINT ck_forecast_interval_80 CHECK (lower_80 IS NULL OR upper_80 IS NULL OR lower_80 <= upper_80),
    CONSTRAINT ck_forecast_interval_95 CHECK (lower_95 IS NULL OR upper_95 IS NULL OR lower_95 <= upper_95),
    CONSTRAINT uq_forecast UNIQUE (run_id, model_name, disease_id, location_id, origin_week_id, horizon, is_backtest)
);
CREATE INDEX IF NOT EXISTS ix_forecast_lookup
    ON analytics.forecast (disease_id, location_id, target_week_id, model_name);

-- Rolling-origin accuracy summary and Diebold-Mariano test results (RQ4).
CREATE TABLE IF NOT EXISTS analytics.model_evaluation (
    evaluation_id      bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id             uuid         NOT NULL REFERENCES ops.pipeline_run_log (run_id),
    model_name         text         NOT NULL,
    disease_id         smallint     NOT NULL REFERENCES curated.dim_disease (disease_id),
    location_id        integer      NOT NULL REFERENCES curated.dim_location (location_id),
    horizon            smallint     NOT NULL CHECK (horizon BETWEEN 1 AND 4),
    test_start_week_id integer      NOT NULL REFERENCES curated.dim_epi_week (week_id),
    test_end_week_id   integer      NOT NULL REFERENCES curated.dim_epi_week (week_id),
    n_origins          integer      NOT NULL CHECK (n_origins > 0),
    mae                numeric      CHECK (mae  IS NULL OR mae  >= 0),
    rmse               numeric      CHECK (rmse IS NULL OR rmse >= 0),
    mase               numeric      CHECK (mase IS NULL OR mase >= 0),
    dm_reference_model text,        -- model compared against (e.g. 'sarima')
    dm_statistic       numeric,
    dm_p_value         numeric      CHECK (dm_p_value IS NULL OR dm_p_value BETWEEN 0 AND 1),
    evaluated_at       timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT ck_eval_window CHECK (test_end_week_id >= test_start_week_id),
    CONSTRAINT uq_evaluation UNIQUE (run_id, model_name, disease_id, location_id, horizon, dm_reference_model)
);

-- Outbreak alerts: EARS C1-C3 and SARIMA residual z-score (RQ3).
CREATE TABLE IF NOT EXISTS analytics.alert (
    alert_id       bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id         uuid         NOT NULL REFERENCES ops.pipeline_run_log (run_id),
    method         text         NOT NULL
        CHECK (method IN ('EARS_C1', 'EARS_C2', 'EARS_C3', 'SARIMA_RESID_Z')),
    disease_id     smallint     NOT NULL REFERENCES curated.dim_disease (disease_id),
    location_id    integer      NOT NULL REFERENCES curated.dim_location (location_id),
    week_id        integer      NOT NULL REFERENCES curated.dim_epi_week (week_id),
    observed       numeric      NOT NULL CHECK (observed >= 0),
    expected       numeric,     -- baseline mean or SARIMA forecast
    statistic      numeric      NOT NULL,
    threshold      numeric      NOT NULL,   -- 3 for EARS, 2 for residual z
    is_alert       boolean      GENERATED ALWAYS AS (statistic > threshold) STORED,
    created_at     timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT uq_alert UNIQUE (method, disease_id, location_id, week_id)
);
CREATE INDEX IF NOT EXISTS ix_alert_active ON analytics.alert (week_id DESC) WHERE is_alert;


-- =============================================================================
--  VIEWS for the dashboard, Parquet export and notebooks
-- =============================================================================

DROP VIEW IF EXISTS curated.v_weekly_cases;
CREATE VIEW curated.v_weekly_cases AS
SELECT
    f.fact_id,
    d.disease_code,
    d.disease_name,
    l.admin_level,
    l.pcode,
    COALESCE(l.lga_name, l.state_name, l.country_name) AS location_name,
    l.state_name,
    l.lga_name,
    l.geo_zone,
    w.week_id,
    w.epi_year,
    w.epi_week,
    w.week_start,
    w.week_end,
    f.suspected,
    f.confirmed,
    f.deaths,
    f.cum_suspected,
    f.cum_confirmed,
    f.cum_deaths,
    f.cfr_pct,
    f.cfr_reported,
    l.population,
    CASE WHEN l.population IS NULL OR f.confirmed IS NULL THEN NULL
         ELSE round(f.confirmed::numeric * 100000 / l.population, 4) END AS incidence_per_100k,
    c.rainfall_mm,
    c.temp_c,
    f.is_provisional,
    f.is_derived_from_cumulative,
    f.quality_flags,
    r.report_code,
    r.report_type,
    r.published_date,
    r.is_synthetic,
    f.updated_at
FROM curated.fact_weekly_cases f
JOIN curated.dim_disease        d ON d.disease_id  = f.disease_id
JOIN curated.dim_location       l ON l.location_id = f.location_id
JOIN curated.dim_epi_week       w ON w.week_id     = f.week_id
JOIN curated.dim_source_report  r ON r.report_id   = f.report_id
LEFT JOIN curated.fact_weekly_climate c
       ON c.location_id = f.location_id AND c.week_id = f.week_id;

-- Run health summary for the dashboard's pipeline health panel (RQ1).
CREATE OR REPLACE VIEW ops.v_run_health AS
SELECT
    date_trunc('week', started_at)::date              AS run_week,
    count(*)                                          AS runs,
    count(*) FILTER (WHERE status = 'success')        AS successful_runs,
    round(100.0 * count(*) FILTER (WHERE status = 'success') / NULLIF(count(*), 0), 1)
                                                      AS success_rate_pct,
    sum(files_new)                                    AS files_new,
    sum(rows_passed)                                  AS rows_passed,
    sum(rows_quarantined)                             AS rows_quarantined,
    sum(revisions_detected)                           AS revisions_detected,
    avg(EXTRACT(EPOCH FROM (ended_at - started_at)))  AS avg_duration_s
FROM ops.pipeline_run_log
WHERE run_type <> 'backfill'
GROUP BY 1;


-- =============================================================================
--  HELPER FUNCTIONS
-- =============================================================================

-- Populate dim_epi_week for every ISO week between two dates (idempotent).
CREATE OR REPLACE FUNCTION curated.populate_epi_weeks(p_from date, p_to date)
RETURNS integer LANGUAGE sql AS $$
    WITH mondays AS (
        SELECT gs::date AS week_start
        FROM generate_series(date_trunc('week', p_from), p_to, interval '7 days') gs
    ), ins AS (
        INSERT INTO curated.dim_epi_week
            (week_id, epi_year, epi_week, week_start, week_end, month, quarter, is_dry_season)
        SELECT
            (EXTRACT(ISOYEAR FROM week_start)::int * 100 + EXTRACT(WEEK FROM week_start)::int),
            EXTRACT(ISOYEAR FROM week_start)::smallint,
            EXTRACT(WEEK FROM week_start)::smallint,
            week_start,
            week_start + 6,
            EXTRACT(MONTH FROM week_start)::smallint,
            EXTRACT(QUARTER FROM week_start)::smallint,
            EXTRACT(MONTH FROM week_start) IN (12, 1, 2, 3, 4)
        FROM mondays
        ON CONFLICT (week_id) DO NOTHING
        RETURNING 1
    )
    SELECT count(*)::int FROM ins;
$$;

-- Re-flag the four most recent weeks per disease as provisional (Table 3.3).
CREATE OR REPLACE FUNCTION curated.refresh_provisional_flags(p_weeks integer DEFAULT 4)
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE
    v_updated integer;
BEGIN
    WITH ranked AS (
        SELECT f.fact_id,
               dense_rank() OVER (PARTITION BY f.disease_id ORDER BY f.week_id DESC) AS rnk
        FROM curated.fact_weekly_cases f
    )
    UPDATE curated.fact_weekly_cases f
       SET is_provisional = (r.rnk <= p_weeks)
      FROM ranked r
     WHERE f.fact_id = r.fact_id
       AND f.is_provisional IS DISTINCT FROM (r.rnk <= p_weeks);
    GET DIAGNOSTICS v_updated = ROW_COUNT;
    RETURN v_updated;
END;
$$;

-- Resolve any free-text state/LGA name to a location_id via the alias table
-- (returns NULL when unmatched -> validation rule 9 quarantines the row).
CREATE OR REPLACE FUNCTION curated.resolve_location(p_name text)
RETURNS integer LANGUAGE sql STABLE AS $$
    SELECT location_id
      FROM curated.dim_location_alias
     WHERE alias_normalised = lower(btrim(regexp_replace(p_name, '\s+', ' ', 'g')));
$$;


-- =============================================================================
--  SEED DATA
-- =============================================================================

INSERT INTO ops.validation_rule (rule_id, rule_code, description, category, severity, action) VALUES
 (1,  'R01_SCHEMA',            'All required columns are present with the expected data types',           'representational',        'error',   'Quarantine the table'),
 (2,  'R02_NON_NEGATIVE',      'Case and death counts are whole numbers of zero or more',                 'intrinsic',               'error',   'Quarantine the row'),
 (3,  'R03_CONFIRMED_LE_SUSP', 'Confirmed cases are not more than suspected cases',                       'intrinsic',               'error',   'Quarantine the row'),
 (4,  'R04_DEATHS_LE_CASES',   'Deaths are not more than the case count the report uses for its CFR',     'intrinsic',               'error',   'Quarantine the row'),
 (5,  'R05_CFR_MISMATCH',      'Recalculated CFR is within 0.1 percentage points of the printed CFR',     'intrinsic',               'warning', 'Load and flag'),
 (6,  'R06_CUM_DECREASE',      'A year-to-date total is not lower than the previous week''s total',       'intrinsic',               'warning', 'Load and record as a revision'),
 (7,  'R07_STATE_SUM',         'State figures add up to the national total',                              'intrinsic',               'warning', 'Load and flag'),
 (8,  'R08_EPI_WEEK',          'Epidemiological week is between 1 and 53 and matches the printed dates',  'representational',        'error',   'Quarantine the row'),
 (9,  'R09_LOCATION',          'State name matches the HDX reference list after alias mapping',           'representational',        'error',   'Quarantine the row'),
 (10, 'R10_DUPLICATE',         'Only one row per disease, location and week in each report',              'intrinsic',               'error',   'Keep the first, quarantine the rest'),
 (11, 'R11_TIMELINESS',        'Report arrives within 14 days of the end of its epidemiological week',    'contextual_timeliness',   'warning', 'Load and flag'),
 (12, 'R12_COMPLETENESS',      'No expected week is missing from a series',                               'contextual_completeness', 'warning', 'Record as a gap')
ON CONFLICT (rule_id) DO UPDATE
   SET rule_code = EXCLUDED.rule_code, description = EXCLUDED.description,
       category = EXCLUDED.category, severity = EXCLUDED.severity, action = EXCLUDED.action;

-- cfr_denominator defaults follow how NCDC situation reports usually state
-- CFR; confirm each one against the report footnotes during template setup.
INSERT INTO curated.dim_disease (disease_code, disease_name, icd10_code, cfr_denominator, is_forecast_target) VALUES
 ('LASSA',      'Lassa fever',                         'A96.2', 'confirmed', true),
 ('CHOLERA',    'Cholera',                             'A00',   'suspected', false),
 ('MEASLES',    'Measles',                             'B05',   'suspected', false),
 ('CSM',        'Cerebrospinal meningitis',            'A39.0', 'suspected', false),
 ('YELLOW_FEVER','Yellow fever',                       'A95',   'confirmed', false),
 ('DIPHTHERIA', 'Diphtheria',                          'A36',   'suspected', false),
 ('MPOX',       'Mpox',                                'B04',   'confirmed', false)
ON CONFLICT (disease_code) DO NOTHING;

-- National row; states and LGAs are loaded from the HDX COD-AB file by
-- src/storage/seed_reference.py.
INSERT INTO curated.dim_location (admin_level, pcode, country_name)
VALUES (0, 'NG', 'Nigeria')
ON CONFLICT (pcode) DO NOTHING;

INSERT INTO curated.dim_location_alias (alias_normalised, location_id, alias_source)
SELECT a, location_id, 'seed'
FROM curated.dim_location, unnest(ARRAY['nigeria', 'national', 'total', 'national total']) AS a
WHERE pcode = 'NG'
ON CONFLICT (alias_normalised) DO NOTHING;

-- Study period 2021-2026 plus one buffer year either side.
SELECT curated.populate_epi_weeks(DATE '2020-01-01', DATE '2027-12-31');

COMMIT;
