#!/usr/bin/env bash
# Capture the "before" baseline of the source environment into docs/baseline/.
# Exact count(*) per table (not pg_stat estimates), FK constraints, versions and server resources.
# Usage: scripts/capture-baseline.sh [droplet-ip]   (defaults to `terraform output droplet_ip`)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/fineract_source}"
SSH_USER="opsadmin"
REMOTE_DIR="fineract"

IP="${1:-$(terraform -chdir="$REPO_ROOT/terraform" output -raw droplet_ip)}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$REPO_ROOT/docs/baseline/baseline-$STAMP.md"
mkdir -p "$REPO_ROOT/docs/baseline"

ssh -i "$SSH_KEY" -o IdentitiesOnly=yes "$SSH_USER@$IP" bash -s -- "$REMOTE_DIR" "$STAMP" > "$OUT" <<'REMOTE'
set -euo pipefail
cd ~/"$1"
STAMP="$2"
# </dev/null: exec -T would otherwise read the rest of this script from bash -s stdin.
psql() { docker compose exec -T db psql -X -v ON_ERROR_STOP=1 -U root "$@" </dev/null; }

section() { printf '\n## %s\n\n```\n' "$1"; }
end() { printf '```\n'; }

echo "# Source environment baseline"
echo
echo "- Captured (UTC): $STAMP"
echo "- Host: $(hostname)"
echo "- Method: \`scripts/capture-baseline.sh\`; row counts are exact \`SELECT count(*)\` per table"

section "PostgreSQL version and settings"
psql -d postgres -tA -c "select version()"
psql -d postgres -tA -c "select 'wal_level=' || current_setting('wal_level')"
end

section "Database sizes (bytes)"
psql -d postgres --csv -c "select datname, pg_database_size(datname) as bytes, pg_size_pretty(pg_database_size(datname)) as size
  from pg_database where datname like 'fineract%' order by datname"
end

for db in fineract_tenants fineract_default; do
  section "Exact row counts: $db"
  psql -d "$db" --csv -c "select table_schema, table_name,
      (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name), false, true, '')))[1]::text::bigint as row_count
    from information_schema.tables
    where table_type = 'BASE TABLE' and table_schema not in ('pg_catalog', 'information_schema')
    order by table_schema, table_name"
  end

  section "Row count totals: $db"
  psql -d "$db" -tA -c "select 'tables=' || count(*) || ' total_rows=' || sum(
      (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name), false, true, '')))[1]::text::bigint)
    from information_schema.tables
    where table_type = 'BASE TABLE' and table_schema not in ('pg_catalog', 'information_schema')"
  end

  section "Foreign key constraints: $db"
  psql -d "$db" -tA -c "select 'count=' || count(*) from pg_constraint where contype = 'f'"
  psql -d "$db" --csv -c "select conrelid::regclass as table_name, conname, pg_get_constraintdef(oid) as definition
    from pg_constraint where contype = 'f' order by 1, 2"
  end
done

section "Container images"
docker compose ps --format '{{.Service}}|{{.Image}}|{{.Status}}'
docker inspect -f '{{.Name}}|{{.Config.Image}}|{{index .RepoDigests 0}}' $(docker compose ps -q) 2>/dev/null \
  || for c in $(docker compose ps -q); do docker inspect -f '{{.Name}}|{{.Config.Image}}' "$c"; done
end

section "Server resources"
echo "nproc: $(nproc)"
echo
free -h
echo
df -h /
end

cat <<'TXT'

## Backup state

- Method: manual `pg_dump -Fc` of both databases via `scripts/backup-fineract.sh`, run by the operator and
  copied to the operator's laptop.
- Schedule: none. No automated or off-site backups, no retention policy, no monitoring of backup success.
- Restore: `scripts/restore-fineract.sh`, tested once by hand on a fresh Droplet (2026-10-08). No routine
  restore testing, no defined RPO/RTO.
TXT
REMOTE

echo ">> Wrote ${OUT#"$REPO_ROOT"/}"
grep -E '^(tables=|count=|wal_level=|PostgreSQL)' "$OUT" | sed 's/^/   /'
