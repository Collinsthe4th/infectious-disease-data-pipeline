# 0003 Revisions are captured by a database trigger

Status: accepted

**Context.** Chapter 3 says "before a value in the fact table is updated, the script compares the incoming value with the stored value" and writes the change to `revision_history`.

**Decision.** The comparison is done by an `AFTER UPDATE` trigger on `curated.fact_weekly_cases`. For every changed measure it writes one row with old and new value, absolute and relative change (generated columns), old and new report, revision lag in weeks, and the run id (`SET LOCAL pipeline.run_id`).

**Why.** The audit trail cannot be skipped by a bug or by a manual SQL update, which is the same reason Chapter 3 gives for database constraints. Upserts stay simple.

**Consequences.** The outcome is the one Chapter 3 describes; only the mechanism differs. Two related rules are also enforced in the merge: a value missing from a new report never erases a stored value, and an older report never overwrites figures from a newer one.
