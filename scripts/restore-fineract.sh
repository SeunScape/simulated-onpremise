#!/usr/bin/env bash
# Restore the default tenant database (fineract_default) from a dump onto the source Droplet.
# Usage: scripts/restore-fineract.sh backups/fineract-<stamp>-fineract_default.dump [droplet-ip]
#
# Only fineract_default is restored. fineract_tenants holds the tenant DB password encrypted with
# the master password, and both are regenerated per server in .env, so the fresh Droplet keeps its own.
set -euo pipefail

DUMP="${1:?usage: $0 <fineract_default dump> [droplet-ip]}"
[ -s "$DUMP" ] || { echo "dump not found or empty: $DUMP" >&2; exit 1; }
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/fineract_source}"
SSH_USER="opsadmin"
REMOTE_DIR="fineract"

IP="${2:-$(terraform -chdir="$REPO_ROOT/terraform" output -raw droplet_ip)}"
SSH=(ssh -i "$SSH_KEY" -o IdentitiesOnly=yes "$SSH_USER@$IP")

echo ">> Stopping Fineract"
"${SSH[@]}" "cd ~/$REMOTE_DIR && docker compose stop fineract"

echo ">> Recreating fineract_default"
"${SSH[@]}" "cd ~/$REMOTE_DIR && docker compose exec -T db psql -v ON_ERROR_STOP=1 -U root -d postgres" <<'SQL'
DROP DATABASE IF EXISTS fineract_default WITH (FORCE);
CREATE DATABASE fineract_default;
GRANT ALL PRIVILEGES ON DATABASE fineract_default TO postgres;
\c fineract_default
GRANT ALL ON SCHEMA public TO postgres;
SQL

echo ">> Restoring $DUMP"
"${SSH[@]}" "cd ~/$REMOTE_DIR && docker compose exec -T db pg_restore --exit-on-error -U root -d fineract_default" < "$DUMP"

echo ">> Starting Fineract and waiting for healthy"
"${SSH[@]}" "cd ~/$REMOTE_DIR && docker compose start fineract && \
  s=\$(date +%s); until [ \"\$(docker inspect -f '{{.State.Health.Status}}' \$(docker compose ps -q fineract))\" = healthy ]; do \
    [ \$(( \$(date +%s) - s )) -gt 600 ] && { echo 'not healthy after 600s' >&2; exit 1; }; sleep 10; done; \
  docker compose exec -T db psql -U root -d fineract_default -tAc \
    \"select 'clients=' || (select count(*) from m_client) || ' loans=' || (select count(*) from m_loan) || ' loan_txns=' || (select count(*) from m_loan_transaction)\""
