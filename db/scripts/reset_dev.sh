#!/usr/bin/env bash
# Destrói e recria o banco local do zero (apenas dev)
set -e

DB_NAME="${POSTGRES_DB:-mktplace_control}"
DB_USER="${POSTGRES_USER:-user}"
DB_HOST="${POSTGRES_HOST:-localhost}"
DB_PORT="${POSTGRES_PORT:-5432}"

echo "==> ATENÇÃO: resetando banco $DB_NAME em $DB_HOST..."
read -rp "Confirma? (s/N) " confirm
[[ "$confirm" != "s" ]] && echo "Abortado." && exit 0

psql -h "$DB_HOST" -p "$DB_PORT" -U postgres \
  -c "DROP DATABASE IF EXISTS $DB_NAME;"

bash "$(dirname "$0")/setup_local.sh"
echo "==> Reset completo."
