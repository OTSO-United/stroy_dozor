"""Retire the draft workflow without approving or deleting historical user data."""

from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE plans SET status='archived' WHERE status='draft'")


def downgrade():
    # Historical archive remains an archive: it cannot safely be reclassified.
    pass
