"""Layer 2 - Ingestion.

Idempotent crawlers for NCDC weekly epidemiological reports and situation
reports (2021-2026), plus HDX, WHO AFRO, ReliefWeb and ERA5 clients. Every
downloaded file is SHA-256 hashed; a hash already present in
``raw.documents`` is skipped, so re-running ingestion creates no duplicates.
"""
