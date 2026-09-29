"""Isolated detector experiments, without project observations or assessments."""

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "detector_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("file_key", sa.String(200), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=True),
        sa.Column("sample_seconds", sa.Float(), nullable=False),
        sa.Column("requested_model_sha", sa.String(64), nullable=False),
        sa.Column("model", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("processed_frames", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_detector_runs_status", "detector_runs", ["status"])
    op.create_table(
        "detector_frames",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id", sa.String(36), sa.ForeignKey("detector_runs.id"), nullable=False
        ),
        sa.Column("sample_index", sa.Integer(), nullable=False),
        sa.Column("offset_seconds", sa.Float(), nullable=False),
        sa.Column("detections", sa.JSON(), nullable=False),
        sa.Column("quality", sa.JSON(), nullable=False),
        sa.Column("image_key", sa.String(250), nullable=False),
        sa.UniqueConstraint("run_id", "sample_index"),
    )
    op.create_index("ix_detector_frames_run_id", "detector_frames", ["run_id"])


def downgrade():
    op.drop_table("detector_frames")
    op.drop_table("detector_runs")
