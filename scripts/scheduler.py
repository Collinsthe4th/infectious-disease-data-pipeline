"""Tiny scheduler for when Airflow is not available (Section 3.3.4 fallback).

Runs the full pipeline every Monday at 06:00 Africa/Lagos and a light daily
check (ingest + process, no analytics) on the other days. A failed run is
retried up to three times with 10, 20 and 40 minute waits.

    docker compose --profile scheduler up -d
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Africa/Lagos")
RUN_AT_HOUR = 6
WAITS_MIN = (10, 20, 40)


def next_run(now: datetime) -> datetime:
    target = now.replace(hour=RUN_AT_HOUR, minute=0, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


def run(mode: str) -> int:
    cmd = [sys.executable, "-m", "src.orchestration.cli", "--trigger", "cron", "init-db"]
    subprocess.run(cmd, check=False)
    cmd = [sys.executable, "-m", "src.orchestration.cli", "--trigger", "cron", "run", "--mode", mode]
    for attempt, wait in enumerate((0, *WAITS_MIN)):
        if wait:
            print(f"retry {attempt} in {wait} min", flush=True)
            time.sleep(wait * 60)
        code = subprocess.run(cmd, check=False).returncode
        if code == 0:
            return 0
    return code


def main() -> None:
    while True:
        now = datetime.now(TZ)
        when = next_run(now)
        print(f"next run {when:%a %d %b %H:%M %Z}", flush=True)
        time.sleep(max(1, (when - now).total_seconds()))
        run("weekly" if when.weekday() == 0 else "daily_check")


if __name__ == "__main__":
    main()
