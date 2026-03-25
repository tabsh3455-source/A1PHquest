"""initial schema baseline

Revision ID: 20260323_0001
Revises:
Create Date: 2026-03-23 12:00:00
"""

from __future__ import annotations

from alembic import op

from app import models  # noqa: F401  # ensure all tables are registered on metadata
from app.db import Base

# revision identifiers, used by Alembic.
revision = "20260323_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Create baseline schema from SQLAlchemy metadata.

    This keeps initial migration aligned with current model layer while we
    establish Alembic versioning for future incremental revisions.
    """
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
