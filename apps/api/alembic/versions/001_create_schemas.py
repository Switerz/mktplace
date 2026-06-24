"""create schemas

Revision ID: 001
Revises:
Create Date: 2026-06-16
"""
from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMAS = ["raw", "staging", "marts", "app", "audit"]


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")


def downgrade() -> None:
    for schema in reversed(SCHEMAS):
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
