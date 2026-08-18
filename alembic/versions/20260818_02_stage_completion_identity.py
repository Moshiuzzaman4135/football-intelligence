"""Add stage completion delivery identity.

Revision ID: 20260818_02
Revises: 20260818_01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260818_02"
down_revision: str | None = "20260818_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_stages",
        sa.Column("completion_owner", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "job_stages",
        sa.Column("completion_predecessor_version", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_stages", "completion_predecessor_version")
    op.drop_column("job_stages", "completion_owner")
