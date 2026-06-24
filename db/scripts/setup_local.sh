#!/usr/bin/env bash
# Cria o banco local e roda migrations + seeds
set -e

DB_NAME="${POSTGRES_DB:-mktplace_control}"
DB_USER="${POSTGRES_USER:-user}"
DB_PASS="${POSTGRES_PASSWORD:-password}"
DB_HOST="${POSTGRES_HOST:-localhost}"
DB_PORT="${POSTGRES_PORT:-5432}"

echo "==> Criando banco $DB_NAME..."
psql -h "$DB_HOST" -p "$DB_PORT" -U postgres \
  -c "CREATE DATABASE $DB_NAME;" 2>/dev/null || echo "(banco já existe)"

psql -h "$DB_HOST" -p "$DB_PORT" -U postgres \
  -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" 2>/dev/null || echo "(usuário já existe)"

psql -h "$DB_HOST" -p "$DB_PORT" -U postgres \
  -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"

echo "==> Rodando migrations..."
cd apps/api
alembic upgrade head

echo "==> Carregando seeds..."
PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
  -f ../../db/seeds/01_marketplaces.sql \
  -f ../../db/seeds/02_empresas_lojas.sql \
  -f ../../db/seeds/03_status_canonico.sql

echo "==> Banco pronto!"
