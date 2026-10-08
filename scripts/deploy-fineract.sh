#!/usr/bin/env bash
# Deploy Fineract, PostgreSQL and the Mifos web UI to the source Droplet.
# Usage: scripts/deploy-fineract.sh [droplet-ip]   (defaults to `terraform output droplet_ip`)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/fineract_source}"
SSH_USER="opsadmin"
REMOTE_DIR="fineract"

IP="${1:-$(terraform -chdir="$REPO_ROOT/terraform" output -raw droplet_ip)}"
SSH=(ssh -i "$SSH_KEY" -o IdentitiesOnly=yes "$SSH_USER@$IP")

echo ">> Copying deploy/ to $SSH_USER@$IP:~/$REMOTE_DIR"
"${SSH[@]}" "mkdir -p ~/$REMOTE_DIR"
tar -C "$REPO_ROOT/deploy" --exclude='.env' -czf - . | "${SSH[@]}" "tar -C ~/$REMOTE_DIR -xzf -"

echo ">> Generating server-side .env (only if missing) and starting services"
"${SSH[@]}" bash -s -- "$REMOTE_DIR" <<'REMOTE'
set -euo pipefail
cd ~/"$1"
if [ ! -f .env ]; then
  umask 077
  {
    echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
    echo "FINERACT_DB_PASS=$(openssl rand -hex 24)"
    echo "FINERACT_DEFAULT_MASTER_PASSWORD=$(openssl rand -hex 24)"
  } > .env
  echo "   generated .env"
fi
docker compose pull
docker compose up -d
docker compose ps
REMOTE
