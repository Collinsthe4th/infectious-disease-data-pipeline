"""Runtime configuration, read once from environment variables.

Every setting has a safe default so the pipeline runs locally with no setup.
Override any of them in a ``.env`` file (see ``.env.example``) or the shell.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parents[2]


def _normalise_url(url: str) -> str:
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value not in (None, "") else default


def _env_list(name: str, default: list[str]) -> list[str]:
    value = os.environ.get(name)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    """All tunable settings for one pipeline process."""

    database_url: str = field(
        default_factory=lambda: _normalise_url(os.environ.get(
            "DATABASE_URL", "postgresql+psycopg://pipeline:pipeline@localhost:5432/disease_surveillance"
        ))
    )
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DATA_DIR", REPO_ROOT / "data")))

    # --- ingestion (Section 3.4.3) -------------------------------------------
    ncdc_base_url: str = field(default_factory=lambda: os.environ.get("NCDC_BASE_URL", "https://ncdc.gov.ng"))
    user_agent: str = field(
        default_factory=lambda: os.environ.get(
            "USER_AGENT",
            "MivaThesisDiseasePipeline/0.1 (+https://github.com/Collinsthe4th/infectious-disease-data-pipeline)",
        )
    )
    request_delay_s: float = field(default_factory=lambda: _env_float("REQUEST_DELAY_S", 5.0))
    request_timeout_s: float = field(default_factory=lambda: _env_float("REQUEST_TIMEOUT_S", 60.0))
    max_retries: int = field(default_factory=lambda: _env_int("MAX_RETRIES", 3))
    backoff_base_s: float = field(default_factory=lambda: _env_float("BACKOFF_BASE_S", 10.0))
    study_start_year: int = field(default_factory=lambda: _env_int("STUDY_START_YEAR", 2021))
    study_end_year: int = field(default_factory=lambda: _env_int("STUDY_END_YEAR", 2026))

    # --- validation (Table 3.4) ----------------------------------------------
    cfr_tolerance_pp: float = field(default_factory=lambda: _env_float("CFR_TOLERANCE_PP", 0.1))
    timeliness_days: int = field(default_factory=lambda: _env_int("TIMELINESS_DAYS", 14))
    provisional_weeks: int = field(default_factory=lambda: _env_int("PROVISIONAL_WEEKS", 4))

    # --- analytics (Section 3.5.4) -------------------------------------------
    # Diseases to try to forecast ("ALL" = all seven); each must also pass forecast_eligibility().
    forecast_diseases: list[str] = field(default_factory=lambda: _env_list("FORECAST_DISEASES", ["ALL"]))
    # No live forecast from data older than this many weeks (forecast_eligibility).
    forecast_max_staleness_weeks: int = field(default_factory=lambda: _env_int("FORECAST_MAX_STALENESS_WEEKS", 26))
    forecast_locations: list[str] = field(
        default_factory=lambda: _env_list("FORECAST_LOCATIONS", ["NG"])  # p-codes; NG = national
    )
    forecast_horizons: int = field(default_factory=lambda: _env_int("FORECAST_HORIZONS", 4))
    evaluation_weeks: int = field(default_factory=lambda: _env_int("EVALUATION_WEEKS", 52))
    sarima_refit_every: int = field(default_factory=lambda: _env_int("SARIMA_REFIT_EVERY", 13))
    ears_threshold: float = field(default_factory=lambda: _env_float("EARS_THRESHOLD", 3.0))
    residual_z_threshold: float = field(default_factory=lambda: _env_float("RESIDUAL_Z_THRESHOLD", 2.0))
    random_seed: int = field(default_factory=lambda: _env_int("RANDOM_SEED", 42))

    # --- derived paths -------------------------------------------------------
    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def quarantine_dir(self) -> Path:
        return self.data_dir / "quarantine"

    @property
    def parquet_dir(self) -> Path:
        return self.data_dir / "curated" / "parquet"

    @property
    def reference_dir(self) -> Path:
        return self.data_dir / "reference"


def get_settings() -> Settings:
    """Return fresh settings (re-reads the environment; cheap to call)."""
    _load_dotenv()
    return Settings()


def _load_dotenv(path: Path | None = None) -> None:
    """Minimal ``.env`` loader so python-dotenv is not a hard dependency."""
    env_file = path or REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
