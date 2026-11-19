"""Print a few read-only views of the database, to check figures by eye. Nothing is changed."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.storage.db import get_engine  # noqa: E402

pd.set_option("display.width", 200)
pd.set_option("display.max_rows", 200)
QUERIES = {
    "Lassa national, last 20 weeks": """
        SELECT week_start, epi_week, suspected, confirmed, deaths, cum_confirmed
          FROM curated.v_weekly_cases WHERE disease_code='LASSA' AND admin_level=0
         ORDER BY week_start DESC LIMIT 20""",
    "Latest live Lassa forecasts": """
        SELECT f.model_name, f.horizon, t.week_start AS target, f.predicted, f.created_at
          FROM analytics.forecast f JOIN curated.dim_epi_week t ON t.week_id=f.target_week_id
         WHERE NOT f.is_backtest AND f.run_id=(SELECT run_id FROM analytics.forecast ORDER BY created_at DESC LIMIT 1)
         ORDER BY f.model_name, f.horizon""",
    "Lassa state rows per year": """
        SELECT epi_year, count(*) AS state_rows, count(DISTINCT week_start) AS weeks
          FROM curated.v_weekly_cases WHERE disease_code='LASSA' AND admin_level=1 GROUP BY 1 ORDER BY 1""",
}
with get_engine().connect() as conn:
    for title, sql in QUERIES.items():
        print(f"\n=== {title}")
        print(pd.read_sql(text(sql), conn).to_string(index=False))
