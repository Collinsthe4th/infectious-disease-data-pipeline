"""Small text helpers shared by several layers."""

from __future__ import annotations


def normalise_alias(name: object) -> str:
    """Lower-case, trim and collapse whitespace: the key used by the alias table."""
    return " ".join(str(name).strip().lower().split())
