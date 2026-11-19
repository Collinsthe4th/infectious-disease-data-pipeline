-- Remove every trace of SYNTHETIC demo data while keeping real NCDC data.
-- Real figures that a demo report overwrote are deleted too, and the real
-- documents they came from are set back to 'new' so the next run reads them again.
-- Everything happens in ONE transaction: all of it, or nothing.

BEGIN;

CREATE TEMP TABLE syn_reports ON COMMIT DROP AS
    SELECT report_id FROM curated.dim_source_report WHERE is_synthetic;
CREATE TEMP TABLE syn_docs ON COMMIT DROP AS
    SELECT document_id FROM raw.documents WHERE is_synthetic;
CREATE TEMP TABLE syn_runs ON COMMIT DROP AS
    SELECT DISTINCT run_id FROM raw.documents WHERE is_synthetic;

-- real documents whose figures were overwritten by a demo report
CREATE TEMP TABLE reread_docs ON COMMIT DROP AS
    SELECT DISTINCT r.document_id
      FROM curated.revision_history h
      JOIN curated.dim_source_report r ON r.report_id = h.old_report_id
     WHERE h.new_report_id IN (SELECT report_id FROM syn_reports)
       AND NOT r.is_synthetic;

DELETE FROM ops.data_quality_flag
 WHERE report_id IN (SELECT report_id FROM syn_reports)
    OR fact_id IN (SELECT fact_id FROM curated.fact_weekly_cases WHERE report_id IN (SELECT report_id FROM syn_reports))
    OR (flag_type = 'R12_COMPLETENESS');                       -- gaps are recomputed after the next load
DELETE FROM curated.revision_history
 WHERE new_report_id IN (SELECT report_id FROM syn_reports) OR old_report_id IN (SELECT report_id FROM syn_reports);
DELETE FROM curated.fact_weekly_cases WHERE report_id IN (SELECT report_id FROM syn_reports);
DELETE FROM ops.quarantine
 WHERE document_id IN (SELECT document_id FROM syn_docs) OR report_id IN (SELECT report_id FROM syn_reports);
DELETE FROM staging.weekly_cases WHERE report_id IN (SELECT report_id FROM syn_reports);
DELETE FROM curated.dim_source_report WHERE report_id IN (SELECT report_id FROM syn_reports);
DELETE FROM raw.documents WHERE document_id IN (SELECT document_id FROM syn_docs);

UPDATE raw.documents SET processing_status = 'new', status_reason = 're-read after removing synthetic demo data'
 WHERE document_id IN (SELECT document_id FROM reread_docs);

-- every model output was computed on mixed data: clear it, the next run recomputes it
DELETE FROM analytics.stl_component;
DELETE FROM analytics.alert;
DELETE FROM analytics.forecast;
DELETE FROM analytics.model_evaluation;

UPDATE ops.pipeline_run_log
   SET error_message = concat_ws(' | ', error_message, 'synthetic demo run: its data was removed')
 WHERE run_id IN (SELECT run_id FROM syn_runs);

COMMIT;
