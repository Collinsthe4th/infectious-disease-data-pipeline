# 0005 Validation rules as plain pandas functions (Great Expectations optional)

Status: accepted

**Context.** Chapter 3 says the rules are "written as Great Expectations expectations, with pytest tests for the more complex rules".

**Decision.** The prototype implements the 12 rules of Table 3.4 as small functions in `src/validation/rules.py`, each returning issues with rule id and severity, and an engine that routes errors to quarantine and warnings to flags. Every rule has a pytest test. Rule ids and codes match the `ops.validation_rule` catalogue.

**Why.** Several rules need context that expectation suites do not express easily (the previous week's cumulative total from the database, the disease-specific CFR denominator, state sums against the national row). Great Expectations' API also changed substantially between major versions, which is a risk for a thesis that must be reproducible. Plain functions are transparent and fast.

**Consequences.** The rules are declared once as Python functions, each covered by pytest tests, and the simplest ones are repeated as database constraints. A Great Expectations suite for the column-level rules (1, 2, 8) is listed in the roadmap as a later extension.
