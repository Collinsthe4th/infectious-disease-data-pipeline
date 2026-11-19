# Free Airflow in your browser (GitHub Codespaces)

Real Apache Airflow 3.3, the same version as on Astronomer, running on a free GitHub cloud computer. Nothing to install on Windows.

**Cost:** free within GitHub's monthly Codespaces allowance (personal accounts get a set number of free core-hours each month; this Codespace uses 4 cores, so it counts 4 core-hours per hour of use). It **stops** when you close it or after 30 minutes idle, so Airflow runs only while you use it. The always-on weekly run is the GitHub workflow **Load data into Supabase**, which runs every Monday by itself.

## One-time setup (2 minutes)
1. GitHub (your profile picture) → **Settings → Codespaces → Secrets → New secret**
   - Name `DATABASE_URL`, value: your Supabase Session pooler string
   - Repository access: choose `infectious-disease-data-pipeline`

## Start Airflow
1. Open the repository on GitHub → green **Code** button → **Codespaces** tab → **Create codespace on main**.
2. Wait ~5 minutes the first time (it installs Airflow and the pipeline; later starts take about a minute).
3. Open the **Ports** tab at the bottom → click the globe icon next to **8080 (Airflow)**.

You will see three DAGs. Turn on `ncdc_disease_pipeline` and press ▶ to run it now; click the run to watch the six boxes turn green, and click any box → **Logs** to see what it did.

If Airflow did not start, in the Codespace terminal run: `bash scripts/airflow_local/start.sh`

## When you are done
Codespaces → **Stop codespace** (or just close the tab; it stops after 30 minutes idle). Delete it when you no longer need it.

## Same thing on your own Windows laptop (optional)
Install WSL (Ubuntu), then inside Ubuntu: clone the repo, `export DATABASE_URL=...`, `bash scripts/airflow_local/start.sh`, open http://localhost:8080.
