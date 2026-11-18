"""SQLAlchemy 2.0 ORM models for the pipeline database.

``schema.sql`` is the single source of truth for the DDL: it also defines the
triggers (revision capture, ``updated_at``), helper functions, views and seed
data that an ORM cannot express cleanly. These models map the same tables so
that Python code can query and upsert with type-checked attributes.

``tests/integration/test_schema_models.py`` reflects a live database built from
``schema.sql`` and fails if a model column drifts from the DDL.

Storage zones
-------------
``ops``       run log, validation-rule catalogue, quarantine, data-quality flags
``raw``       immutable register of downloaded files (SHA-256 keyed)
``staging``   validated rows awaiting merge into ``curated``
``curated``   star schema: ``fact_weekly_cases`` + dimensions + revision history
``analytics`` STL components, forecasts, model evaluation, alerts
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

__all__ = [
    "Base",
    "PipelineRunLog",
    "ValidationRule",
    "RawDocument",
    "DimDisease",
    "DimLocation",
    "DimLocationAlias",
    "DimEpiWeek",
    "DimSourceReport",
    "FactWeeklyCases",
    "FactWeeklyClimate",
    "RevisionHistory",
    "StagingWeeklyCases",
    "Quarantine",
    "DataQualityFlag",
    "StlComponent",
    "Forecast",
    "ModelEvaluation",
    "Alert",
    "PRIORITY_DISEASES",
    "MODEL_NAMES",
    "ALERT_METHODS",
]

#: Disease codes seeded into ``curated.dim_disease``.
PRIORITY_DISEASES: tuple[str, ...] = (
    "LASSA",
    "CHOLERA",
    "MEASLES",
    "CSM",
    "YELLOW_FEVER",
    "DIPHTHERIA",
    "MPOX",
)

#: Allowed values of ``analytics.forecast.model_name``.
MODEL_NAMES: tuple[str, ...] = (
    "seasonal_naive",
    "sarima",
    "negative_binomial",
    "random_forest",
    "gradient_boosting",
    "xgboost",
    "svr",
)

#: Allowed values of ``analytics.alert.method``.
ALERT_METHODS: tuple[str, ...] = ("EARS_C1", "EARS_C2", "EARS_C3", "SARIMA_RESID_Z")

_NOW = text("now()")


def _in(column: str, values: tuple[str, ...]) -> str:
    """Build a SQL ``column IN (...)`` expression for a CHECK constraint."""
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def _non_negative(*columns: str) -> list[CheckConstraint]:
    """CHECK constraints enforcing validation rule 2 (counts >= 0 or NULL)."""
    return [CheckConstraint(f"{c} IS NULL OR {c} >= 0", name=f"ck_{c}_non_negative") for c in columns]


class Base(DeclarativeBase):
    """Declarative base shared by all models."""

    type_annotation_map = {
        dict[str, Any]: JSONB,
        uuid.UUID: UUID(as_uuid=True),
    }


# =============================================================================
#  OPS
# =============================================================================


class PipelineRunLog(Base):
    """One row per pipeline run; feeds the run-success-rate metric (RQ1)."""

    __tablename__ = "pipeline_run_log"
    __table_args__ = (
        CheckConstraint(_in("run_type", ("scheduled", "daily_check", "backfill", "manual"))),
        CheckConstraint(_in("trigger_source", ("airflow", "cron", "cli"))),
        CheckConstraint(_in("status", ("running", "success", "partial", "failed"))),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_run_end_after_start"),
        CheckConstraint("status = 'running' OR ended_at IS NOT NULL", name="ck_run_finished_has_end"),
        {"schema": "ops"},
    )

    run_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    run_type: Mapped[str] = mapped_column(Text)
    dag_id: Mapped[Optional[str]] = mapped_column(Text)
    trigger_source: Mapped[str] = mapped_column(Text, server_default="cron")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, server_default="running")
    files_discovered: Mapped[int] = mapped_column(Integer, server_default="0")
    files_new: Mapped[int] = mapped_column(Integer, server_default="0")
    files_skipped_duplicate: Mapped[int] = mapped_column(Integer, server_default="0")
    files_manual_queue: Mapped[int] = mapped_column(Integer, server_default="0")
    rows_extracted: Mapped[int] = mapped_column(Integer, server_default="0")
    rows_passed: Mapped[int] = mapped_column(Integer, server_default="0")
    rows_warned: Mapped[int] = mapped_column(Integer, server_default="0")
    rows_quarantined: Mapped[int] = mapped_column(Integer, server_default="0")
    revisions_detected: Mapped[int] = mapped_column(Integer, server_default="0")
    attempt_number: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    code_version: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)


class ValidationRule(Base):
    """Catalogue of the 12 validation rules in Table 3.4 (seeded by schema.sql)."""

    __tablename__ = "validation_rule"
    __table_args__ = (
        CheckConstraint("rule_id BETWEEN 1 AND 12"),
        CheckConstraint(
            _in(
                "category",
                ("representational", "intrinsic", "contextual_timeliness", "contextual_completeness"),
            )
        ),
        CheckConstraint(_in("severity", ("error", "warning"))),
        {"schema": "ops"},
    )

    rule_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    rule_code: Mapped[str] = mapped_column(Text, unique=True)
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)


# =============================================================================
#  RAW
# =============================================================================


class RawDocument(Base):
    """Immutable register of every downloaded or hand-placed source file.

    ``sha256`` is the idempotency key: ingestion skips any file whose hash is
    already present, so re-running a crawl never stores a duplicate.
    """

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'"),
        CheckConstraint(_in("source_name", ("ncdc", "hdx", "who_afro", "reliefweb", "era5", "manual_inbox"))),
        CheckConstraint(
            _in(
                "processing_status",
                ("new", "parsed", "parse_failed", "manual_queue", "manually_entered", "ignored"),
            )
        ),
        CheckConstraint(
            "source_name = 'manual_inbox' OR source_url IS NOT NULL", name="ck_doc_url_unless_inbox"
        ),
        {"schema": "raw"},
    )

    document_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    source_name: Mapped[str] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(Text, index=True)
    file_path: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(Text)
    content_type: Mapped[Optional[str]] = mapped_column(Text)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    has_text_layer: Mapped[Optional[bool]] = mapped_column(Boolean)
    page_count: Mapped[Optional[int]] = mapped_column(Integer)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ops.pipeline_run_log.run_id"))
    processing_status: Mapped[str] = mapped_column(Text, server_default="new")
    status_reason: Mapped[Optional[str]] = mapped_column(Text)
    listing_title: Mapped[Optional[str]] = mapped_column(Text)
    listing_date: Mapped[Optional[date]] = mapped_column(Date)
    disease_hint: Mapped[Optional[str]] = mapped_column(Text)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    run: Mapped[PipelineRunLog] = relationship()


# =============================================================================
#  CURATED - dimensions
# =============================================================================


class DimDisease(Base):
    """The seven priority diseases."""

    __tablename__ = "dim_disease"
    __table_args__ = (
        CheckConstraint("disease_code ~ '^[A-Z_]+$'"),
        CheckConstraint(_in("cfr_denominator", ("confirmed", "suspected"))),
        {"schema": "curated"},
    )

    disease_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    disease_code: Mapped[str] = mapped_column(Text, unique=True)
    disease_name: Mapped[str] = mapped_column(Text, unique=True)
    icd10_code: Mapped[Optional[str]] = mapped_column(Text)
    cfr_denominator: Mapped[str] = mapped_column(Text, server_default="confirmed")
    is_forecast_target: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class DimLocation(Base):
    """National (0), state (1) and LGA (2) units keyed on HDX p-codes."""

    __tablename__ = "dim_location"
    __table_args__ = (
        CheckConstraint("admin_level IN (0, 1, 2)"),
        CheckConstraint("pcode ~ '^NG[0-9]*$'"),
        CheckConstraint(
            "(admin_level = 0 AND parent_location_id IS NULL AND state_name IS NULL AND lga_name IS NULL)"
            " OR (admin_level = 1 AND parent_location_id IS NOT NULL AND state_name IS NOT NULL"
            " AND lga_name IS NULL)"
            " OR (admin_level = 2 AND parent_location_id IS NOT NULL AND state_name IS NOT NULL"
            " AND lga_name IS NOT NULL)",
            name="ck_loc_level_shape",
        ),
        UniqueConstraint(
            "admin_level", "state_name", "lga_name",
            name="uq_loc_state_lga", postgresql_nulls_not_distinct=True,
        ),
        {"schema": "curated"},
    )

    location_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_level: Mapped[int] = mapped_column(SmallInteger)
    pcode: Mapped[str] = mapped_column(Text, unique=True)
    parent_location_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("curated.dim_location.location_id"), index=True
    )
    country_name: Mapped[str] = mapped_column(Text, server_default="Nigeria")
    state_name: Mapped[Optional[str]] = mapped_column(Text)
    lga_name: Mapped[Optional[str]] = mapped_column(Text)
    geo_zone: Mapped[Optional[str]] = mapped_column(Text)
    population: Mapped[Optional[int]] = mapped_column(BigInteger)
    population_year: Mapped[Optional[int]] = mapped_column(SmallInteger)
    population_source: Mapped[Optional[str]] = mapped_column(Text)

    parent: Mapped[Optional[DimLocation]] = relationship(remote_side=[location_id])
    aliases: Mapped[list[DimLocationAlias]] = relationship(
        back_populates="location", cascade="all, delete-orphan"
    )

    @property
    def display_name(self) -> str:
        """Most specific human-readable name for this unit."""
        return self.lga_name or self.state_name or self.country_name


class DimLocationAlias(Base):
    """Alias dictionary for name standardisation, e.g. 'fct', 'abuja' -> FCT."""

    __tablename__ = "dim_location_alias"
    __table_args__ = (
        CheckConstraint("alias_normalised = lower(btrim(alias_normalised))"),
        {"schema": "curated"},
    )

    alias_normalised: Mapped[str] = mapped_column(Text, primary_key=True)
    location_id: Mapped[int] = mapped_column(
        ForeignKey("curated.dim_location.location_id", ondelete="CASCADE"), index=True
    )
    alias_source: Mapped[str] = mapped_column(Text, server_default="manual")

    location: Mapped[DimLocation] = relationship(back_populates="aliases")


class DimEpiWeek(Base):
    """ISO epidemiological weeks; ``week_id = epi_year * 100 + epi_week``."""

    __tablename__ = "dim_epi_week"
    __table_args__ = (
        CheckConstraint("epi_year BETWEEN 2015 AND 2040"),
        CheckConstraint("epi_week BETWEEN 1 AND 53"),
        CheckConstraint("month BETWEEN 1 AND 12"),
        CheckConstraint("quarter BETWEEN 1 AND 4"),
        UniqueConstraint("epi_year", "epi_week", name="uq_epi_year_week"),
        CheckConstraint("week_id = epi_year * 100 + epi_week", name="ck_week_id_matches"),
        CheckConstraint("EXTRACT(ISODOW FROM week_start) = 1", name="ck_week_monday"),
        CheckConstraint("week_end = week_start + 6", name="ck_week_length"),
        CheckConstraint("EXTRACT(ISOYEAR FROM week_start) = epi_year", name="ck_week_iso_year"),
        CheckConstraint("EXTRACT(WEEK FROM week_start) = epi_week", name="ck_week_iso_week"),
        {"schema": "curated"},
    )

    week_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    epi_year: Mapped[int] = mapped_column(SmallInteger)
    epi_week: Mapped[int] = mapped_column(SmallInteger)
    week_start: Mapped[date] = mapped_column(Date, unique=True)
    week_end: Mapped[date] = mapped_column(Date, unique=True)
    month: Mapped[int] = mapped_column(SmallInteger)
    quarter: Mapped[int] = mapped_column(SmallInteger)
    is_dry_season: Mapped[bool] = mapped_column(Boolean)

    @staticmethod
    def make_week_id(epi_year: int, epi_week: int) -> int:
        """Return the surrogate key for an ISO year/week pair, e.g. (2024, 5) -> 202405."""
        if not 1 <= epi_week <= 53:
            raise ValueError(f"epi_week must be 1-53, got {epi_week}")
        return epi_year * 100 + epi_week


class DimSourceReport(Base):
    """Parsed metadata of every report that contributed figures."""

    __tablename__ = "dim_source_report"
    __table_args__ = (
        CheckConstraint(
            _in("report_type", ("weekly_epi_report", "situation_report", "who_afro_bulletin", "manual_entry"))
        ),
        CheckConstraint(_in("extraction_engine", ("pdfplumber", "camelot", "manual"))),
        CheckConstraint(
            "printed_week_end IS NULL OR printed_week_start IS NULL OR printed_week_end >= printed_week_start",
            name="ck_printed_dates",
        ),
        {"schema": "curated"},
    )

    report_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_code: Mapped[str] = mapped_column(Text, unique=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("raw.documents.document_id"), index=True)
    report_type: Mapped[str] = mapped_column(Text)
    publisher: Mapped[str] = mapped_column(Text, server_default="NCDC")
    disease_id: Mapped[Optional[int]] = mapped_column(ForeignKey("curated.dim_disease.disease_id"), index=True)
    week_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_epi_week.week_id"), index=True)
    published_date: Mapped[Optional[date]] = mapped_column(Date)
    printed_week_start: Mapped[Optional[date]] = mapped_column(Date)
    printed_week_end: Mapped[Optional[date]] = mapped_column(Date)
    template_id: Mapped[Optional[str]] = mapped_column(Text)
    extraction_engine: Mapped[Optional[str]] = mapped_column(Text)
    title: Mapped[Optional[str]] = mapped_column(Text)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)

    document: Mapped[RawDocument] = relationship()
    disease: Mapped[Optional[DimDisease]] = relationship()
    week: Mapped[DimEpiWeek] = relationship()


# =============================================================================
#  CURATED - facts
# =============================================================================


class FactWeeklyCases(Base):
    """Grain: one row per disease x location x epidemiological week.

    ``report_id`` points to the most recent report that supplied the values.
    A database trigger copies any changed measure into ``revision_history``
    whenever this row is updated, so upserts never lose the old figure.
    """

    __tablename__ = "fact_weekly_cases"
    __table_args__ = (
        UniqueConstraint("disease_id", "location_id", "week_id", name="uq_fact_grain"),
        *_non_negative("suspected", "confirmed", "deaths", "cum_suspected", "cum_confirmed", "cum_deaths"),
        CheckConstraint("cfr_pct IS NULL OR cfr_pct BETWEEN 0 AND 100", name="ck_cfr_pct_range"),
        CheckConstraint("cfr_reported IS NULL OR cfr_reported BETWEEN 0 AND 100", name="ck_cfr_reported_range"),
        CheckConstraint(
            "confirmed IS NULL OR suspected IS NULL OR confirmed <= suspected", name="ck_confirmed_le_suspected"
        ),
        CheckConstraint(
            "cum_confirmed IS NULL OR cum_suspected IS NULL OR cum_confirmed <= cum_suspected",
            name="ck_cum_confirmed_le_cum_suspected",
        ),
        CheckConstraint(
            "deaths IS NULL OR deaths <= GREATEST(COALESCE(suspected, 0), COALESCE(confirmed, 0))"
            " OR (suspected IS NULL AND confirmed IS NULL)",
            name="ck_deaths_le_cases",
        ),
        CheckConstraint(
            "cum_deaths IS NULL OR cum_deaths <= GREATEST(COALESCE(cum_suspected, 0), COALESCE(cum_confirmed, 0))"
            " OR (cum_suspected IS NULL AND cum_confirmed IS NULL)",
            name="ck_cum_deaths_le_cases",
        ),
        CheckConstraint(
            "COALESCE(suspected, confirmed, deaths, cum_suspected, cum_confirmed, cum_deaths) IS NOT NULL",
            name="ck_fact_has_value",
        ),
        {"schema": "curated"},
    )

    fact_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    disease_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_disease.disease_id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_location.location_id"), index=True)
    week_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_epi_week.week_id"), index=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_source_report.report_id"), index=True)

    suspected: Mapped[Optional[int]] = mapped_column(Integer)
    confirmed: Mapped[Optional[int]] = mapped_column(Integer)
    deaths: Mapped[Optional[int]] = mapped_column(Integer)
    cum_suspected: Mapped[Optional[int]] = mapped_column(Integer)
    cum_confirmed: Mapped[Optional[int]] = mapped_column(Integer)
    cum_deaths: Mapped[Optional[int]] = mapped_column(Integer)
    cfr_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    cfr_reported: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))

    is_provisional: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    is_derived_from_cumulative: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    quality_flags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))

    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    last_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ops.pipeline_run_log.run_id"))

    disease: Mapped[DimDisease] = relationship()
    location: Mapped[DimLocation] = relationship()
    week: Mapped[DimEpiWeek] = relationship()
    report: Mapped[DimSourceReport] = relationship()
    revisions: Mapped[list[RevisionHistory]] = relationship(
        back_populates="fact", order_by="RevisionHistory.revision_id"
    )

    #: Measures compared by the revision trigger, in the order it checks them.
    MEASURES: tuple[str, ...] = (
        "suspected", "confirmed", "deaths",
        "cum_suspected", "cum_confirmed", "cum_deaths",
        "cfr_pct", "cfr_reported",
    )


class FactWeeklyClimate(Base):
    """Weekly ERA5 rainfall and temperature per state (Lassa fever covariates)."""

    __tablename__ = "fact_weekly_climate"
    __table_args__ = (
        CheckConstraint("rainfall_mm IS NULL OR rainfall_mm >= 0"),
        CheckConstraint("temp_c IS NULL OR temp_c BETWEEN -10 AND 55"),
        {"schema": "curated"},
    )

    location_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_location.location_id"), primary_key=True)
    week_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_epi_week.week_id"), primary_key=True)
    rainfall_mm: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2))
    temp_c: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    source: Mapped[str] = mapped_column(Text, server_default="era5")
    document_id: Mapped[Optional[int]] = mapped_column(ForeignKey("raw.documents.document_id"))
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


class RevisionHistory(Base):
    """Audit trail of NCDC revisions: one row per changed measure (answers RQ2).

    Rows are written by the ``trg_fact_revision`` trigger, not by application
    code. ``abs_change`` and ``rel_change`` are generated columns.
    """

    __tablename__ = "revision_history"
    __table_args__ = (
        CheckConstraint(_in("field_name", FactWeeklyCases.MEASURES)),
        CheckConstraint("old_value IS DISTINCT FROM new_value", name="ck_revision_changed"),
        {"schema": "curated"},
    )

    revision_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fact_id: Mapped[int] = mapped_column(
        ForeignKey("curated.fact_weekly_cases.fact_id", ondelete="CASCADE"), index=True
    )
    disease_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_disease.disease_id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_location.location_id"))
    week_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_epi_week.week_id"))
    field_name: Mapped[str] = mapped_column(Text)
    old_value: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    new_value: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    abs_change: Mapped[Optional[Decimal]] = mapped_column(
        Numeric, Computed("new_value - old_value", persisted=True)
    )
    rel_change: Mapped[Optional[Decimal]] = mapped_column(
        Numeric,
        Computed(
            "CASE WHEN old_value IS NULL OR old_value = 0 OR new_value IS NULL THEN NULL"
            " ELSE (new_value - old_value) / old_value END",
            persisted=True,
        ),
    )
    old_report_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_source_report.report_id"))
    new_report_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_source_report.report_id"), index=True)
    revision_lag_weeks: Mapped[Optional[int]] = mapped_column(Integer)
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ops.pipeline_run_log.run_id"))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)

    fact: Mapped[FactWeeklyCases] = relationship(back_populates="revisions")


# =============================================================================
#  STAGING
# =============================================================================


class StagingWeeklyCases(Base):
    """Validated rows waiting to be merged into ``curated.fact_weekly_cases``.

    Uses natural keys (``disease_code``, ``pcode``, ``epi_year``/``epi_week``)
    so extraction code never needs dimension surrogate ids.
    """

    __tablename__ = "weekly_cases"
    __table_args__ = (
        CheckConstraint("epi_week BETWEEN 1 AND 53"),
        *_non_negative("suspected", "confirmed", "deaths", "cum_suspected", "cum_confirmed", "cum_deaths"),
        UniqueConstraint("report_id", "disease_code", "pcode", "epi_year", "epi_week", name="uq_staging_grain"),
        {"schema": "staging"},
    )

    staging_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ops.pipeline_run_log.run_id"))
    report_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_source_report.report_id"))
    disease_code: Mapped[str] = mapped_column(Text)
    pcode: Mapped[str] = mapped_column(Text)
    epi_year: Mapped[int] = mapped_column(SmallInteger)
    epi_week: Mapped[int] = mapped_column(SmallInteger)
    suspected: Mapped[Optional[int]] = mapped_column(Integer)
    confirmed: Mapped[Optional[int]] = mapped_column(Integer)
    deaths: Mapped[Optional[int]] = mapped_column(Integer)
    cum_suspected: Mapped[Optional[int]] = mapped_column(Integer)
    cum_confirmed: Mapped[Optional[int]] = mapped_column(Integer)
    cum_deaths: Mapped[Optional[int]] = mapped_column(Integer)
    cfr_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    cfr_reported: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2))
    is_derived_from_cumulative: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    quality_flags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    source_page: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


# =============================================================================
#  OPS - quarantine and data-quality flags
# =============================================================================


class Quarantine(Base):
    """Error-level validation failures and unparseable reports, held for review."""

    __tablename__ = "quarantine"
    __table_args__ = (
        CheckConstraint(_in("scope", ("row", "table", "document"))),
        CheckConstraint(
            _in("review_status", ("open", "corrected_and_loaded", "confirmed_invalid", "ignored"))
        ),
        CheckConstraint("review_status = 'open' OR reviewed_at IS NOT NULL", name="ck_quarantine_review"),
        {"schema": "ops"},
    )

    quarantine_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ops.pipeline_run_log.run_id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("raw.documents.document_id"))
    report_id: Mapped[Optional[int]] = mapped_column(ForeignKey("curated.dim_source_report.report_id"))
    rule_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ops.validation_rule.rule_id"), index=True)
    scope: Mapped[str] = mapped_column(Text)
    failure_reason: Mapped[str] = mapped_column(Text)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    source_page: Mapped[Optional[int]] = mapped_column(Integer)
    quarantined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    review_status: Mapped[str] = mapped_column(Text, server_default="open")
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[Optional[str]] = mapped_column(Text)

    rule: Mapped[Optional[ValidationRule]] = relationship()


class DataQualityFlag(Base):
    """Warning-level results (rules 5, 6, 7, 11, 12) and WHO AFRO mismatches."""

    __tablename__ = "data_quality_flag"
    __table_args__ = ({"schema": "ops"},)

    flag_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ops.pipeline_run_log.run_id"))
    rule_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ops.validation_rule.rule_id"))
    flag_type: Mapped[str] = mapped_column(Text, index=True)
    fact_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("curated.fact_weekly_cases.fact_id", ondelete="CASCADE")
    )
    report_id: Mapped[Optional[int]] = mapped_column(ForeignKey("curated.dim_source_report.report_id"))
    disease_id: Mapped[Optional[int]] = mapped_column(ForeignKey("curated.dim_disease.disease_id"))
    location_id: Mapped[Optional[int]] = mapped_column(ForeignKey("curated.dim_location.location_id"))
    week_id: Mapped[Optional[int]] = mapped_column(ForeignKey("curated.dim_epi_week.week_id"))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    flagged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


# =============================================================================
#  ANALYTICS
# =============================================================================


class StlComponent(Base):
    """STL trend / seasonal / remainder components per series (RQ3)."""

    __tablename__ = "stl_component"
    __table_args__ = (
        CheckConstraint(_in("series_measure", ("suspected", "confirmed", "deaths"))),
        {"schema": "analytics"},
    )

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ops.pipeline_run_log.run_id"), primary_key=True)
    disease_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_disease.disease_id"), primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_location.location_id"), primary_key=True)
    week_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_epi_week.week_id"), primary_key=True)
    series_measure: Mapped[str] = mapped_column(Text, primary_key=True, server_default="confirmed")
    observed: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    trend: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    seasonal: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    remainder: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    is_interpolated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class Forecast(Base):
    """Point and interval forecasts, 1-4 weeks ahead.

    ``is_backtest`` is true for rolling-origin evaluation forecasts and false
    for live forward forecasts shown on the dashboard.
    """

    __tablename__ = "forecast"
    __table_args__ = (
        CheckConstraint(_in("model_name", MODEL_NAMES)),
        CheckConstraint("horizon BETWEEN 1 AND 4"),
        CheckConstraint("predicted >= 0"),
        CheckConstraint("target_week_id > origin_week_id", name="ck_forecast_target_after_origin"),
        CheckConstraint(
            "lower_80 IS NULL OR upper_80 IS NULL OR lower_80 <= upper_80", name="ck_forecast_interval_80"
        ),
        CheckConstraint(
            "lower_95 IS NULL OR upper_95 IS NULL OR lower_95 <= upper_95", name="ck_forecast_interval_95"
        ),
        UniqueConstraint(
            "run_id", "model_name", "disease_id", "location_id", "origin_week_id", "horizon", "is_backtest",
            name="uq_forecast",
        ),
        {"schema": "analytics"},
    )

    forecast_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ops.pipeline_run_log.run_id"))
    model_name: Mapped[str] = mapped_column(Text)
    model_version: Mapped[Optional[str]] = mapped_column(Text)
    disease_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_disease.disease_id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_location.location_id"))
    origin_week_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_epi_week.week_id"))
    target_week_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_epi_week.week_id"))
    horizon: Mapped[int] = mapped_column(SmallInteger)
    predicted: Mapped[Decimal] = mapped_column(Numeric)
    lower_80: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    upper_80: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    lower_95: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    upper_95: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    actual: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    is_backtest: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


class ModelEvaluation(Base):
    """Rolling-origin accuracy (MAE, RMSE, MASE) and Diebold-Mariano results (RQ4)."""

    __tablename__ = "model_evaluation"
    __table_args__ = (
        CheckConstraint("horizon BETWEEN 1 AND 4"),
        CheckConstraint("n_origins > 0"),
        CheckConstraint("mae IS NULL OR mae >= 0"),
        CheckConstraint("rmse IS NULL OR rmse >= 0"),
        CheckConstraint("mase IS NULL OR mase >= 0"),
        CheckConstraint("dm_p_value IS NULL OR dm_p_value BETWEEN 0 AND 1"),
        CheckConstraint("test_end_week_id >= test_start_week_id", name="ck_eval_window"),
        UniqueConstraint(
            "run_id", "model_name", "disease_id", "location_id", "horizon", "dm_reference_model",
            name="uq_evaluation",
        ),
        {"schema": "analytics"},
    )

    evaluation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ops.pipeline_run_log.run_id"))
    model_name: Mapped[str] = mapped_column(Text)
    disease_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_disease.disease_id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_location.location_id"))
    horizon: Mapped[int] = mapped_column(SmallInteger)
    test_start_week_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_epi_week.week_id"))
    test_end_week_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_epi_week.week_id"))
    n_origins: Mapped[int] = mapped_column(Integer)
    mae: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    rmse: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    mase: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    dm_reference_model: Mapped[Optional[str]] = mapped_column(Text)
    dm_statistic: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    dm_p_value: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


class Alert(Base):
    """EARS C1-C3 and SARIMA residual z-score outbreak signals (RQ3).

    ``is_alert`` is generated by the database as ``statistic > threshold``.
    """

    __tablename__ = "alert"
    __table_args__ = (
        CheckConstraint(_in("method", ALERT_METHODS)),
        CheckConstraint("observed >= 0"),
        UniqueConstraint("method", "disease_id", "location_id", "week_id", name="uq_alert"),
        {"schema": "analytics"},
    )

    alert_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ops.pipeline_run_log.run_id"))
    method: Mapped[str] = mapped_column(Text)
    disease_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_disease.disease_id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_location.location_id"))
    week_id: Mapped[int] = mapped_column(ForeignKey("curated.dim_epi_week.week_id"))
    observed: Mapped[Decimal] = mapped_column(Numeric)
    expected: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    statistic: Mapped[Decimal] = mapped_column(Numeric)
    threshold: Mapped[Decimal] = mapped_column(Numeric)
    is_alert: Mapped[Optional[bool]] = mapped_column(
        Boolean, Computed("statistic > threshold", persisted=True)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
