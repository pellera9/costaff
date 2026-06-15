"""Baseline schema — the full current CoStaff core schema.

Built straight from the SQLAlchemy models so the baseline always matches the
code. A fresh database materialises every table from ``upgrade()``; an
existing pre-alembic database is *stamped* to this revision instead of
re-running it (see ``core.database._bootstrap_schema``).

Subsequent schema changes get their own incremental revision files
(``0002_*`` etc.) with explicit ``op.*`` operations.
"""
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from core.models import Base

    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    from core.models import Base

    Base.metadata.drop_all(bind=op.get_bind())
