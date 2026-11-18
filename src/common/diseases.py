"""Catalogue of the seven priority diseases.

``ncdc_category`` is the ``cat`` id of the disease's situation-report listing
on ncdc.gov.ng/diseases/sitreps (checked November 2026). ``title_patterns``
identify the disease from a report title. ``cfr_denominator`` mirrors
``curated.dim_disease`` and must be confirmed against each report family.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Disease:
    code: str
    name: str
    ncdc_category: int | None
    title_patterns: tuple[str, ...]
    cfr_denominator: str = "confirmed"  # 'confirmed' or 'suspected'

    def matches(self, text: str) -> bool:
        return any(re.search(p, text, re.IGNORECASE) for p in self.title_patterns)


DISEASES: dict[str, Disease] = {
    d.code: d
    for d in (
        Disease("LASSA", "Lassa fever", 5, (r"lassa",), "confirmed"),
        Disease("CHOLERA", "Cholera", 7, (r"cholera",), "suspected"),
        Disease("MEASLES", "Measles", 11, (r"measles",), "suspected"),
        Disease("CSM", "Cerebrospinal meningitis", 6, (r"meningitis", r"\bcsm\b"), "suspected"),
        Disease("YELLOW_FEVER", "Yellow fever", 10, (r"yellow\s*fever",), "confirmed"),
        Disease("DIPHTHERIA", "Diphtheria", 18, (r"diphtheria",), "suspected"),
        Disease("MPOX", "Mpox", 8, (r"\bmpox\b", r"monkey\s*pox"), "confirmed"),
    )
}


def detect_disease(text: str) -> Disease | None:
    """Return the first disease whose title pattern appears in ``text``."""
    for disease in DISEASES.values():
        if disease.matches(text):
            return disease
    return None
