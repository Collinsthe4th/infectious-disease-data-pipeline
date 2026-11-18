# Start here: the project in plain words

## The idea in one picture

Every week the pipeline does five jobs, in order. That chain of jobs is what an **orchestrator** (Airflow) runs and watches for you.

```
 1. COLLECT        2. READ            3. CHECK            4. SAVE              5. ANALYSE & SHOW
 download NCDC  →  pull the numbers → apply 12 rules   →  store in the      →  trends, forecasts,
 report PDFs       out of the PDF     (bad rows held      Supabase database    alerts → dashboard
                   tables             back for review)
```

## Where to look

| I want to… | Go to |
|---|---|
| **See the analysed data and model results** | [`results/README.md`](results/README.md): charts and tables, rebuilt by GitHub automatically |
| See the interactive dashboard | Your Streamlit app (setup below) |
| Understand what each file does | The table below |

## Which file does what (the ones you might edit)

| Step | File | Edit it to… |
|---|---|---|
| 1. Collect | `src/ingestion/ncdc_crawler.py` | change which NCDC pages are read |
| 2. Read | `src/extraction/parsers.py`, `src/extraction/classify.py` | fix how a report layout is read |
| 3. Check | `src/validation/rules.py` | change one of the 12 rules (thresholds are in `src/common/config.py`) |
| 4. Save | `src/storage/schema.sql` | change the database tables |
| 5. Analyse | `src/analytics/forecasting.py`, `anomaly.py` | change the forecast models or alert methods |
| 5. Show | `dashboard/app.py`, `dashboard/pages/*.py` | change what the dashboard displays |
| The chain itself | `src/orchestration/pipeline.py` | change the order of the steps |
| Airflow version of the chain | `src/orchestration/dags/disease_pipeline_dag.py` | |

Everything else (tests, Docker files, docs) supports these files; you can ignore it.

## Connecting your Supabase database (one time, about 5 minutes)

1. **Get the right connection string.** In Supabase, open your project → **Connect** → choose **Session pooler** (not "Direct connection": GitHub and Streamlit cannot reach the direct one). It looks like
   `postgresql://postgres.<your-project-ref>:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres`
   Replace `[YOUR-PASSWORD]` with your database password.
2. **Give it to GitHub.** Repository → Settings → Secrets and variables → Actions → **New repository secret**. Name `DATABASE_URL`, value the string from step 1.
3. **Fill the database.** Actions tab → **Load data into Supabase** → Run workflow → choose `demo` (or `real-trial` for a few real reports).
4. **Give it to Streamlit.** In share.streamlit.io: your app → Settings → **Secrets**, paste
   ```
   DATABASE_URL = "the same string as step 1"
   ```
   Main file path: `dashboard/app.py`. Save; the app restarts and shows the data.

Never paste the password into a file in the repository, an issue or a chat.
