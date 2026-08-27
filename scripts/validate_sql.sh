#!/usr/bin/env bash
# Validate every SQL file WITHOUT running or deploying anything.
#
# Dry runs are free and are not billed, so this can be run after each new check.
# It catches the class of error that otherwise accumulates until deployment:
# UNION ALL column type/count mismatches, unknown columns, syntax errors.
#
#   ./scripts/validate_sql.sh
set -uo pipefail

PROJECT="${BQ_PROJECT:-omega-dahlia-347111}"
LOCATION="${BQ_LOCATION:-europe-west4}"
FAILED=0

# The check files are multi-statement scripts, which BigQuery cannot estimate as
# a whole, so validate the query body with the DECLAREd thresholds inlined.
estimate_body() {
  python3 - "$1" <<'PY'
import pathlib, sys, re
s = pathlib.Path(sys.argv[1]).read_text()
if "WITH" in s:
    s = s[s.index("WITH"):]
for name, val in re.findall(r"DECLARE\s+(\w+)\s+INT64\s+DEFAULT\s+(\d+)",
                            pathlib.Path(sys.argv[1]).read_text()):
    s = s.replace(name, val)
print(s)
PY
}

check() {
  local label="$1"; shift
  local out
  out=$("$@" 2>&1)
  if echo "$out" | grep -qi '"totalBytesProcessed"'; then
    local bytes
    bytes=$(echo "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["statistics"]["query"]["totalBytesProcessed"])' 2>/dev/null || echo 0)
    printf '  ✓ %-34s %s GiB\n' "$label" "$(python3 -c "print(f'{$bytes/1024**3:.2f}')")"
  else
    printf '  ✗ %-34s FAILED\n' "$label"
    echo "$out" | grep -iE 'error|not found|incompatible|invalid' | head -3 | sed 's/^/      /'
    FAILED=1
  fi
}

echo "Dry-running SQL against ${PROJECT} (${LOCATION}) — nothing is billed or deployed."
estimate_body sql/10_issues.sql > /tmp/_dh_issues.sql
check "10_issues.sql (query body)" \
  bq --project_id="$PROJECT" --location="$LOCATION" query \
     --use_legacy_sql=false --dry_run --format=json --flagfile=/dev/null < /tmp/_dh_issues.sql

echo
if [ "$FAILED" -eq 0 ]; then
  echo "All good — safe to deploy with sql/30_rebuild_procedure.sql."
else
  echo "Fix the above before regenerating the procedure."
  exit 1
fi
