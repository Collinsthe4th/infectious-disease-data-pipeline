# Coding standards

- Python 3.10+, type hints on public functions, a docstring on every module saying which layer and thesis section it implements.
- One job per function; transformations are pure functions over DataFrames so they can be unit-tested.
- Never turn missing into zero. Never drop a row silently: quarantine it with a rule id and reason.
- All database writes for one report happen in one transaction (`engine.begin()`).
- SQL lives next to the code that uses it (SQLAlchemy `text()`), parameters always bound, never formatted into strings from data.
- New settings go in `src/common/config.py` **and** [configuration.md](configuration.md).
- Schema changes: edit `schema.sql` and `models.py` together; the integration test compares them. After the first real backfill, add forward-only scripts in `src/storage/migrations/`.
- Lint with `ruff check src tests dashboard scripts`.
