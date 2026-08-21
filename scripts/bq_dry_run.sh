#!/usr/bin/env bash
# Estimate what the checks will cost BEFORE running them.
# BigQuery on-demand billing is per byte *scanned*, so this is the number that
# matters. Nothing is executed and nothing is billed by a dry run.
set -euo pipefail

PROJECT="${BQ_PROJECT:-omega-dahlia-347111}"
PRICE_PER_TIB="${BQ_PRICE_PER_TIB:-6.25}"   # confirm against your billing console

echo "Dry-running sql/10_issues.sql against project ${PROJECT}…"

# --dry_run reports bytes processed without executing.
BYTES=$(bq --project_id="${PROJECT}" query \
  --use_legacy_sql=false --dry_run --format=json \
  < sql/10_issues.sql \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["statistics"]["query"]["totalBytesProcessed"])')

python3 - "$BYTES" "$PRICE_PER_TIB" <<'PY'
import sys
b, price = int(sys.argv[1]), float(sys.argv[2])
tib = b / 1024**4
print(f"  bytes scanned : {b:,}")
print(f"  = {b/1024**3:,.2f} GiB ({tib:.6f} TiB)")
print(f"  est. cost/run : ${tib*price:,.4f}  (at ${price}/TiB)")
print(f"  est. cost/month at daily refresh: ${tib*price*30:,.2f}")
print("\nNote: the monthly free query allowance may cover this entirely.")
PY
