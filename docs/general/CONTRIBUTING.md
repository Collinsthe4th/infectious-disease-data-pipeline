# Contributing

1. Read the [docs index](../README.md) and the file for the area you change.
2. Create a branch, make the change with tests, run `pytest tests/unit` (and `tests/integration` if you touched SQL).
3. Update docs in the same change: a new setting → [configuration](../engineering/configuration.md); a design decision → a new ADR; a user-visible change → [CHANGELOG](CHANGELOG.md).
4. Open a pull request; CI must be green.
