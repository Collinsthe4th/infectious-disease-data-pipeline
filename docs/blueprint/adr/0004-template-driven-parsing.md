# 0004 Templates find tables by header words, not page coordinates

Status: accepted

**Context.** NCDC layouts change between years and between diseases. Coordinate-based templates break silently when a table moves.

**Decision.** A template (`src/extraction/templates`) names the blocks to read (the national summary table with "Current week" and "<year> Cumulative" rows; the state table; for weekly epidemiological reports, one row per disease) and how to read them. Columns are matched by header words ("Suspected", "Confirmed", "Deaths", "CFR", "Cumulative", "Week 44"). pdfplumber reads ruled tables first, then a text-line fallback, then Camelot stream mode. Templates are tried most specific first; if none yields figures the document goes to the manual queue and the run continues.

**Why.** Header words survive most layout changes; failures are loud (manual queue + quarantine row) instead of silent.

**Consequences.** `lassa_sitrep_v1` was checked against the real week 44, 2026 report; the generic template for the other six diseases must be confirmed with the *NCDC parse check* workflow, which downloads current reports and parses them. When NCDC changes a layout, add a template with a `valid_from` date rather than editing the old one.
