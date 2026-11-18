# Project structure

Each `src/` package maps to one layer of the seven-layer architecture (Chapter 3, Fig. 3.2).

```
infectious-disease-data-pipeline/
├── Dockerfile · docker-compose.yml · .dockerignore     containers: db, app, dashboard, scheduler
├── requirements.txt · requirements-dev.txt · pyproject.toml · .env.example
├── .github/workflows/ci.yml                 unit + PostgreSQL integration + dashboard + Docker build
├── .github/workflows/ncdc-parse-check.yml   weekly parse of real NCDC reports
├── src/
│   ├── common/        config.py · logging_setup.py · epiweek.py · diseases.py · text.py
│   ├── ingestion/  L2 ncdc_crawler.py · http.py (polite client) · hashing.py · inbox.py · hdx.py
│   ├── extraction/ L4 pdf_reader.py · classify.py · templates/ · parsers.py · standardise.py
│   │                  transform.py · manual_csv.py · extract.py (entry point)
│   ├── validation/ L4 rules.py (Table 3.4) · engine.py
│   ├── storage/    L5 schema.sql · models.py · repository.py · db.py · migrations/
│   ├── analytics/  L6 series.py · decomposition.py · anomaly.py · forecasting.py · evaluation.py · run.py
│   ├── orchestration/ pipeline.py (steps) · cli.py · dags/disease_pipeline_dag.py
│   └── demo/          synthetic.py · pdf_writer.py
├── dashboard/      L7 app.py · data.py · ui.py · pages/1_Trends … 5_Pipeline_Health
├── tests/unit/        no database
├── tests/integration/ PostgreSQL: schema, end-to-end demo, idempotency, dashboard pages
├── data/
│   ├── raw/        L3 original files by source (git-ignored)
│   ├── inbox/         hand-placed PDFs / CSVs
│   ├── quarantine/    duplicates and the manual download queue
│   ├── curated/parquet/  exports for DuckDB
│   └── reference/     nga_states.csv · nga_location_aliases.csv (committed)
├── notebooks/         01_explore_with_duckdb.ipynb
├── scripts/           scheduler.py
└── docs/              general · blueprint (+ADRs) · product · engineering · qa · operations · security
```
