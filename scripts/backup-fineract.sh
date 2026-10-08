#!/usr/bin/env bash
# Dump both Fineract databases from the source Droplet to backups/ (custom format, pg_dump -Fc).
# Usage: scripts/backup-fineract.sh [droplet-ip]   (defaults to `terraform output droplet_ip`)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/fineract_source}"
SSH_USER="opsadmin"
REMOTE_DIR="fineract"
DATABASES=(fineract_tenants fineract_default)

IP="${1:-$(terraform -chdir="$REPO_ROOT/terraform" output -raw droplet_ip)}"
SSH=(ssh -i "$SSH_KEY" -o IdentitiesOnly=yes "$SSH_USER@$IP")
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$REPO_ROOT/backups"

for db in "${DATABASES[@]}"; do
  out="$REPO_ROOT/backups/fineract-$STAMP-$db.dump"
  echo ">> Dumping $db -> ${out#"$REPO_ROOT"/}"
  "${SSH[@]}" "cd ~/$REMOTE_DIR && docker compose exec -T db pg_dump -Fc -U root $db" > "$out"
  # A truncated or empty dump has no readable table of contents.
  pg_restore --list "$out" > /dev/null 2>&1 || "${SSH[@]}" "cd ~/$REMOTE_DIR && docker compose exec -T db pg_restore --list" < "$out" > /dev/null
  echo "   $(du -h "$out" | cut -f1)"
done
