"""Object classification and workbook applicability, 19 September 2026."""

import json
from pathlib import Path
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade():
    types = op.create_table(
        "project_types",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
    )
    works = op.create_table(
        "catalog_works",
        sa.Column("code", sa.String(60), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
    )
    links = op.create_table(
        "project_type_works",
        sa.Column(
            "project_type_id",
            sa.String(32),
            sa.ForeignKey("project_types.id"),
            primary_key=True,
        ),
        sa.Column(
            "work_code",
            sa.String(60),
            sa.ForeignKey("catalog_works.code"),
            primary_key=True,
        ),
    )
    seed = json.loads(
        (
            Path(__file__).resolve().parents[2] / "seed/work_applicability_v1.json"
        ).read_text(encoding="utf-8")
    )
    op.bulk_insert(types, seed["project_types"])
    op.bulk_insert(
        works,
        [{k: w[k] for k in ("code", "title", "source_row")} for w in seed["works"]],
    )
    op.bulk_insert(
        links,
        [
            {"project_type_id": t, "work_code": w["code"]}
            for w in seed["works"]
            for t in w["project_type_ids"]
        ],
    )
    if op.get_bind().dialect.name == "sqlite":
        # Nullable ADD COLUMN preserves existing references without rebuilding projects.
        op.execute(
            "ALTER TABLE projects ADD COLUMN project_type_id VARCHAR(32) REFERENCES project_types(id)"
        )
    else:
        op.add_column(
            "projects",
            sa.Column(
                "project_type_id",
                sa.String(32),
                sa.ForeignKey("project_types.id", name="fk_projects_type"),
                nullable=True,
            ),
        )
    op.create_index("ix_projects_project_type_id", "projects", ["project_type_id"])
    op.add_column("projects", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("projects", sa.Column("longitude", sa.Float(), nullable=True))


def downgrade():
    op.drop_column("projects", "longitude")
    op.drop_column("projects", "latitude")
    op.drop_index("ix_projects_project_type_id", "projects")
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("fk_projects_type", "projects", type_="foreignkey")
    op.drop_column("projects", "project_type_id")
    op.drop_table("project_type_works")
    op.drop_table("catalog_works")
    op.drop_table("project_types")
