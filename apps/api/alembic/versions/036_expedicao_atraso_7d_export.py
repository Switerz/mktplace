"""Atraso de 7 dias no resumo do export da Kokeshi (EXPEDICAO-ATRASO-7D).

A 035 deu a `marts.expedicao_refresh_run` as quatro colunas do atraso de 7
dias (regra oficial da Shopee: atrasados / pedidos com prazo na janela). O
resumo da carga pelo export do Seller Centre (`marts.expedicao_refresh_run_export`,
034) foi criado `LIKE` a tabela ANTES da 035 e ficou sem elas: a Kokeshi saia
sem % na tela. Mesmas colunas, mesmo tipo, todas nulas, sem default (so'
catalogo, sem reescrever). O publicador so' grava se as quatro existirem.

Revision ID: 036
Revises: 035
"""
from __future__ import annotations

from alembic import op

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None

TABELA = "marts.expedicao_refresh_run_export"
COLUNAS = (
    ("due_7d_count", "INTEGER"),
    ("late_7d_count", "INTEGER"),
    ("late_7d_window_start", "DATE"),
    ("late_7d_window_end", "DATE"),
)


def upgrade() -> None:
    for nome, tipo in COLUNAS:
        op.execute(f"ALTER TABLE {TABELA} ADD COLUMN IF NOT EXISTS {nome} {tipo}")


def downgrade() -> None:
    for nome, _tipo in reversed(COLUNAS):
        op.execute(f"ALTER TABLE {TABELA} DROP COLUMN IF EXISTS {nome}")
