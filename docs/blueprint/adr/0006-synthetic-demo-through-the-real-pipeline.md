# 0006 A synthetic demo dataset, loaded through the real pipeline

Status: accepted

**Context.** The system must be demonstrable before (or without) a full historical crawl, and examiners need a quick way to see every feature work.

**Decision.** `pipeline demo` writes synthetic CSV files and NCDC-style PDFs into `data/inbox`, then runs the normal pipeline. The files contain realistic seasonality, a few deliberately invalid rows (one per error rule type) and a batch of late corrections, so quarantine and revision tracking are exercised. Every file name starts with `SYNTHETIC_`, which sets `is_synthetic = true` on the document and report rows; the dashboard shows a warning banner whenever such rows exist.

**Why.** Testing the real code path is more convincing than loading a fixture directly into tables.

**Consequences.** Synthetic numbers must never be cited. Use a fresh database (`docker compose down -v`) before loading real data, or the banner will stay on.
