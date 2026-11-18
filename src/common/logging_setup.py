"""Structured logging (Section 3.5.2).

Every line carries time, level, run id, report id, step and message, so a run
can be reconstructed from the log alone. Output is one JSON object per line.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)
_report: contextvars.ContextVar[str | None] = contextvars.ContextVar("report", default=None)
_step: contextvars.ContextVar[str | None] = contextvars.ContextVar("step", default=None)


def set_context(*, run_id: str | None = None, report: str | None = None, step: str | None = None) -> None:
    """Set the fields attached to every following log line in this context."""
    if run_id is not None:
        _run_id.set(run_id)
    if report is not None:
        _report.set(report)
    if step is not None:
        _step.set(step)


def clear_report() -> None:
    _report.set(None)


class JsonFormatter(logging.Formatter):
    """Render a record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "run_id": _run_id.get(),
            "report": _report.get(),
            "step": _step.get(),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        extra = getattr(record, "data", None)
        if extra:
            payload["data"] = extra
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Configure the root logger once per process."""
    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("pdfminer", "urllib3", "camelot", "matplotlib", "cmdstanpy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
