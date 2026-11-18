# 0001 Layered batch pipeline with raw, staging and curated zones

Status: accepted

**Context.** NCDC publishes weekly. Reports are PDFs whose layout changes, and figures are revised later. The thesis needs traceability from every number back to its source.

**Decision.** A scheduled batch pipeline of seven layers (sources, ingestion, raw, transformation and validation, curated storage, analytics, presentation). Raw files are stored unchanged and registered by SHA-256; validated rows pass through a staging table before an upsert into the curated star schema.

**Why.** Weekly publication gives no benefit to streaming (Kleppmann, 2017). Separate zones follow the lakehouse idea of cheap immutable raw storage plus managed analysis-ready tables (Armbrust et al., 2021) and make every figure traceable.

**Consequences.** Re-processing is always possible from `data/raw`. Storage grows with every report (a few hundred MB for 2021-2026), which is acceptable.
