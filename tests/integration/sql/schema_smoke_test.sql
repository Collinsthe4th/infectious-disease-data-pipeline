-- Behavioural smoke test for src/storage/schema.sql.
-- Runs inside a transaction and rolls back, so it leaves no data behind.
-- Usage: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f tests/integration/sql/schema_smoke_test.sql
SET client_min_messages = notice;
BEGIN;

DO $$
DECLARE
    v_run   uuid;
    v_run2  uuid;
    v_ng    integer;
    v_edo   integer;
    v_doc1  bigint;
    v_doc2  bigint;
    v_rep1  integer;
    v_rep2  integer;
    v_lassa smallint;
    v_fact  bigint;
    v_n     integer;
    v_ok    boolean;
BEGIN
    -- fixtures --------------------------------------------------------------
    INSERT INTO ops.pipeline_run_log (run_type, trigger_source) VALUES ('backfill', 'cli') RETURNING run_id INTO v_run;
    INSERT INTO ops.pipeline_run_log (run_type, trigger_source) VALUES ('scheduled', 'cron') RETURNING run_id INTO v_run2;
    SELECT location_id INTO v_ng FROM curated.dim_location WHERE pcode = 'NG';
    INSERT INTO curated.dim_location (admin_level, pcode, parent_location_id, state_name, geo_zone, population, population_year)
    VALUES (1, 'NG012', v_ng, 'Edo', 'South South', 4777000, 2022)
    ON CONFLICT (pcode) DO UPDATE SET population = EXCLUDED.population, population_year = EXCLUDED.population_year
    RETURNING location_id INTO v_edo;
    INSERT INTO curated.dim_location_alias VALUES ('edo', v_edo, 'test'), ('edo state', v_edo, 'test')
    ON CONFLICT (alias_normalised) DO NOTHING;
    -- the test uses week 2024-W05: clear anything a previous load put there (rolled back at the end)
    DELETE FROM curated.fact_weekly_cases WHERE week_id IN (202405, 202406);
    DELETE FROM analytics.alert WHERE week_id = 202405;
    SELECT disease_id INTO v_lassa FROM curated.dim_disease WHERE disease_code = 'LASSA';

    INSERT INTO raw.documents (sha256, source_name, source_url, file_path, file_name, run_id)
    VALUES (repeat('a', 64), 'ncdc', 'https://ncdc.gov.ng/x/w5.pdf', 'raw/ncdc/w5.pdf', 'w5.pdf', v_run)
    RETURNING document_id INTO v_doc1;
    INSERT INTO raw.documents (sha256, source_name, source_url, file_path, file_name, run_id)
    VALUES (repeat('b', 64), 'ncdc', 'https://ncdc.gov.ng/x/w7.pdf', 'raw/ncdc/w7.pdf', 'w7.pdf', v_run2)
    RETURNING document_id INTO v_doc2;
    INSERT INTO curated.dim_source_report (report_code, document_id, report_type, disease_id, week_id, extraction_engine)
    VALUES ('NCDC-SITREP-LASSA-2024-W05', v_doc1, 'situation_report', v_lassa, 202405, 'pdfplumber') RETURNING report_id INTO v_rep1;
    INSERT INTO curated.dim_source_report (report_code, document_id, report_type, disease_id, week_id, extraction_engine)
    VALUES ('NCDC-SITREP-LASSA-2024-W07', v_doc2, 'situation_report', v_lassa, 202407, 'pdfplumber') RETURNING report_id INTO v_rep2;

    -- 1. idempotent ingestion: duplicate SHA-256 is rejected -----------------
    BEGIN
        INSERT INTO raw.documents (sha256, source_name, source_url, file_path, file_name, run_id)
        VALUES (repeat('a', 64), 'ncdc', 'https://ncdc.gov.ng/x/copy.pdf', 'p', 'f', v_run);
        RAISE EXCEPTION 'FAIL: duplicate sha256 accepted';
    EXCEPTION WHEN unique_violation THEN NULL; END;

    -- 2. alias resolution (rule 9 support) -----------------------------------
    ASSERT curated.resolve_location('  EDO   State ') = v_edo, 'alias resolution failed';
    ASSERT curated.resolve_location('Atlantis') IS NULL, 'unknown name resolved';

    -- 3. first load ----------------------------------------------------------
    PERFORM set_config('pipeline.run_id', v_run::text, true);
    INSERT INTO curated.fact_weekly_cases (disease_id, location_id, week_id, report_id,
                                           suspected, confirmed, deaths, cfr_pct, last_run_id)
    VALUES (v_lassa, v_edo, 202405, v_rep1, 40, 12, 2, 16.67, v_run)
    RETURNING fact_id INTO v_fact;
    SELECT count(*) INTO v_n FROM curated.revision_history WHERE fact_id = v_fact;
    ASSERT v_n = 0, 'insert must not create revisions';

    -- 4. upsert with revised figures -> revision rows -------------------------
    PERFORM set_config('pipeline.run_id', v_run2::text, true);
    INSERT INTO curated.fact_weekly_cases AS f (disease_id, location_id, week_id, report_id,
                                                suspected, confirmed, deaths, cfr_pct, last_run_id)
    VALUES (v_lassa, v_edo, 202405, v_rep2, 41, 15, 2, 13.33, v_run2)
    ON CONFLICT (disease_id, location_id, week_id) DO UPDATE
       SET report_id = EXCLUDED.report_id, suspected = EXCLUDED.suspected,
           confirmed = EXCLUDED.confirmed, deaths = EXCLUDED.deaths,
           cfr_pct = EXCLUDED.cfr_pct, last_run_id = EXCLUDED.last_run_id;

    SELECT count(*) INTO v_n FROM curated.revision_history WHERE fact_id = v_fact;
    ASSERT v_n = 3, format('expected 3 revisions (suspected, confirmed, cfr_pct), got %s', v_n);
    SELECT (abs_change = 3 AND rel_change = 0.25 AND old_report_id = v_rep1 AND new_report_id = v_rep2
            AND revision_lag_weeks = 2 AND run_id = v_run2)
      INTO v_ok FROM curated.revision_history WHERE fact_id = v_fact AND field_name = 'confirmed';
    ASSERT v_ok, 'revision row content wrong';

    -- 5. constraint backstops (Table 3.4) ------------------------------------
    BEGIN  -- rule 2: negative count
        INSERT INTO curated.fact_weekly_cases (disease_id, location_id, week_id, report_id, confirmed)
        VALUES (v_lassa, v_edo, 202406, v_rep1, -1);
        RAISE EXCEPTION 'FAIL: negative count accepted';
    EXCEPTION WHEN check_violation THEN NULL; END;
    BEGIN  -- rule 3: confirmed > suspected
        INSERT INTO curated.fact_weekly_cases (disease_id, location_id, week_id, report_id, suspected, confirmed)
        VALUES (v_lassa, v_edo, 202406, v_rep1, 5, 9);
        RAISE EXCEPTION 'FAIL: confirmed > suspected accepted';
    EXCEPTION WHEN check_violation THEN NULL; END;
    BEGIN  -- rule 4 backstop: deaths > cases
        INSERT INTO curated.fact_weekly_cases (disease_id, location_id, week_id, report_id, suspected, confirmed, deaths)
        VALUES (v_lassa, v_edo, 202406, v_rep1, 5, 3, 7);
        RAISE EXCEPTION 'FAIL: deaths > cases accepted';
    EXCEPTION WHEN check_violation THEN NULL; END;
    BEGIN  -- rule 8: week 54 does not exist
        INSERT INTO curated.dim_epi_week VALUES (202454, 2024, 54, DATE '2025-01-06', DATE '2025-01-12', 1, 1, true);
        RAISE EXCEPTION 'FAIL: epi week 54 accepted';
    EXCEPTION WHEN check_violation THEN NULL; END;
    BEGIN  -- rule 10: duplicate grain
        INSERT INTO curated.fact_weekly_cases (disease_id, location_id, week_id, report_id, confirmed)
        VALUES (v_lassa, v_edo, 202405, v_rep1, 1);
        RAISE EXCEPTION 'FAIL: duplicate grain accepted';
    EXCEPTION WHEN unique_violation THEN NULL; END;
    BEGIN  -- empty fact row
        INSERT INTO curated.fact_weekly_cases (disease_id, location_id, week_id, report_id)
        VALUES (v_lassa, v_edo, 202406, v_rep1);
        RAISE EXCEPTION 'FAIL: empty fact row accepted';
    EXCEPTION WHEN check_violation THEN NULL; END;

    -- 6. epi-week calendar matches ISO 8601 ----------------------------------
    ASSERT (SELECT week_start FROM curated.dim_epi_week WHERE week_id = 202101) = DATE '2021-01-04', 'ISO 2021-W01';
    ASSERT (SELECT week_start FROM curated.dim_epi_week WHERE week_id = 202053) = DATE '2020-12-28', 'ISO 2020-W53';
    SELECT count(*) INTO v_n FROM curated.dim_epi_week WHERE epi_year BETWEEN 2021 AND 2026;
    ASSERT v_n = 313, format('expected 313 ISO weeks 2021-2026, got %s', v_n);

    -- 7. provisional flags + views + generated alert flag --------------------
    PERFORM curated.refresh_provisional_flags();
    ASSERT (SELECT incidence_per_100k FROM curated.v_weekly_cases WHERE fact_id = v_fact) = round(15 * 100000.0 / 4777000, 4),
           'incidence in view';
    INSERT INTO analytics.alert (run_id, method, disease_id, location_id, week_id, observed, expected, statistic, threshold)
    VALUES (v_run2, 'EARS_C1', v_lassa, v_edo, 202405, 15, 4, 3.4, 3);
    ASSERT (SELECT is_alert FROM analytics.alert WHERE week_id = 202405), 'alert flag';

    -- 8. quarantine references the rule catalogue ----------------------------
    INSERT INTO ops.quarantine (run_id, document_id, report_id, rule_id, scope, failure_reason, payload)
    VALUES (v_run, v_doc1, v_rep1, 3, 'row', 'confirmed (9) > suspected (5)', '{"state":"Edo"}');
    ASSERT (SELECT count(*) FROM ops.validation_rule) = 12, '12 rules seeded';

    RAISE NOTICE 'schema smoke test: ALL CHECKS PASSED';
END;
$$;

ROLLBACK;
