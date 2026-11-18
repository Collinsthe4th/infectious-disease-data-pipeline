# Docs index

**Read the relevant file here before making a non-trivial change.** These documents describe how the system is meant to work; the thesis (Chapter 3) is the specification they implement.

| Folder | Holds | Read it when |
|---|---|---|
| [general/](general/) | Glossary, CONTRIBUTING, CHANGELOG | You are new, or a term is unclear |
| [blueprint/](blueprint/blueprint.md) | Blueprint and ADRs (one decision per file) | You change structure, a dependency or the design |
| [product/](product/) | Requirements (SRS) and roadmap | You add or drop a feature |
| [engineering/](engineering/) | Development guide, database schema, parser coverage on real reports, configuration, coding standards | You write code, add a setting or a template |
| [qa/](qa/) | Test strategy | You add or change a test |
| [operations/](operations/) | Backfill and scheduled-run runbook, scheduling options | You load history or run the pipeline every week |
| [security/](security/README.md) | Data ethics, sources, credentials | You touch data sources, logging or secrets |

ADRs are numbered and never renumbered or deleted. A reversal is a new ADR that supersedes the old one.
