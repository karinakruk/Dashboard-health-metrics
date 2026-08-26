"""Generate sql/30_rebuild_procedure.sql from the check files.

Wrapping the checks in a BigQuery stored procedure means the SQL has exactly
one definition (these files), while the daily driver — Apps Script — only has
to issue `CALL data_health.rebuild()`. No SQL is duplicated into JavaScript,
and there is no second schedule to keep aligned with the Sheet refresh.

    PYTHONPATH=. python scripts/build_procedure.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "sql" / "30_rebuild_procedure.sql"

HEADER = """-- ============================================================================
-- GENERATED FILE — do not edit by hand.
--   Source, in run order:
--     10_issues.sql   rebuild the current issue set
--     15_history.sql  append it to issue_history (before anything overwrites it)
--     25_movement.sql diff the two newest runs -> fixed / new / persisting
--     20_summary.sql  per-rule counts, joined to that movement
--   Rebuild: PYTHONPATH=. python scripts/build_procedure.py
--
-- Creates data_health.rebuild(), which recomputes every check. Run this file
-- ONCE in the BigQuery console; after that the daily Apps Script trigger calls
-- the procedure, so the checks and the Sheet refresh happen in one ordered step.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS data_health
  OPTIONS(location = 'europe-west4');

CREATE OR REPLACE PROCEDURE data_health.rebuild()
BEGIN
"""

FOOTER = """
END;
"""


def body(path: Path) -> tuple[list[str], list[str]]:
    """Split a check file into its DECLAREs and the rest.

    Inside a procedure body every DECLARE has to come first, so they are
    hoisted ahead of the statements.
    """
    declares, rest = [], []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("DECLARE "):
            declares.append(line)
        elif stripped.startswith("CREATE SCHEMA"):
            continue  # created outside the procedure
        else:
            rest.append(line)
    return declares, rest


def main() -> None:
    all_declares: list[str] = []
    all_statements: list[str] = []
    for name in ("10_issues.sql", "15_history.sql", "25_movement.sql", "20_summary.sql"):
        declares, rest = body(ROOT / "sql" / name)
        all_declares += declares
        all_statements += [f"", f"  -- ── from {name} ──"] + rest

    indented = "\n".join(
        ("  " + line if line.strip() else line) for line in all_statements
    )
    OUT.write_text(
        HEADER
        + "\n".join("  " + d.strip() for d in all_declares)
        + "\n"
        + indented
        + FOOTER
    )
    print(f"Wrote {OUT} ({len(OUT.read_text().splitlines())} lines)")


if __name__ == "__main__":
    main()
