"""Generate sql/30_rebuild_procedure.sql from the check files.

Wrapping the checks in a BigQuery stored procedure means the SQL has exactly
one definition (these files), while the daily driver — Apps Script — only has
to issue `CALL data_health.rebuild()`. No SQL is duplicated into JavaScript,
and there is no second schedule to keep aligned with the Sheet refresh.

    PYTHONPATH=. python scripts/build_procedure.py
"""

import hashlib
import re
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


def rule_fingerprints(issues_sql: str) -> dict[str, str]:
    """Hash each check's own SQL, so a definition change is detectable.

    Movement (fixed / newly flagged) compares row sets between runs. If a rule's
    definition changes, rows appear and vanish for reasons unrelated to data
    quality — a narrowed scope reads as hundreds "fixed". Recording a
    fingerprint per run lets the dashboard refuse to report movement across a
    definition change instead of presenting it as progress.

    Each check's SQL begins with its own rule id as the first projected literal,
    so the file splits cleanly on those markers.
    """
    body = issues_sql[issues_sql.index("the eight checks"):]
    marks = [(m.start(), m.group(1))
             for m in re.finditer(r"^\s*'([a-z_]+)'(?:\s+AS rule_id)?,", body, re.M)]
    out: dict[str, str] = {}
    for i, (start, rule_id) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(body)
        segment = body[start:end]
        # Comments and blank lines do not change behaviour, so exclude them —
        # otherwise reformatting a comment would look like a definition change.
        code = "\n".join(
            line.split("--")[0].rstrip()
            for line in segment.splitlines()
            if line.split("--")[0].strip()
        )
        out[rule_id] = hashlib.sha256(code.encode()).hexdigest()[:12]
    return out


def rule_versions_sql(fps: dict[str, str]) -> str:
    rows = ",\n    ".join(
        f"STRUCT('{rid}' AS rule_id, '{h}' AS rule_version)" for rid, h in sorted(fps.items()))
    return f"""
  -- ── generated: fingerprint of each check's SQL ──
  -- Lets the dashboard tell "this improved" apart from "this was redefined".
  CREATE OR REPLACE TABLE data_health.rule_versions AS
  SELECT * FROM UNNEST([
    {rows}
  ]);
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
            # Hoisting is line-based, so a DECLARE split across lines would have
            # its value left behind in the body — which BigQuery only reports as
            # a confusing syntax error much later. Fail here instead.
            code = stripped.split("--")[0].rstrip()
            if not code.endswith(";"):
                raise SystemExit(
                    f"{path.name}: DECLARE must be on one line (ends without ';'):\n  {stripped}")
            declares.append(line)
        elif stripped.startswith("CREATE SCHEMA"):
            continue  # created outside the procedure
        else:
            rest.append(line)
    return declares, rest


def main() -> None:
    issues_sql = (ROOT / "sql" / "10_issues.sql").read_text()
    fps = rule_fingerprints(issues_sql)
    if len(fps) < 2:
        raise SystemExit(f"expected a fingerprint per check, got {list(fps)}")

    all_declares: list[str] = []
    all_statements: list[str] = []
    for name in ("10_issues.sql", "15_history.sql", "25_movement.sql", "20_summary.sql"):
        declares, rest = body(ROOT / "sql" / name)
        all_declares += declares
        all_statements += [f"", f"  -- ── from {name} ──"] + rest
        if name == "10_issues.sql":
            all_statements += rule_versions_sql(fps).splitlines()

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
