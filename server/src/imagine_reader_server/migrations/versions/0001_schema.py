"""schema: books, their preparation jobs, narration clips, picture scenes, and the spending ledger

Revision ID: 0001
Revises:
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "books",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.String(length=300), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=True),
        sa.Column("narration_characters", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sketch_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("shared", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("picture_style", sa.String(length=16), nullable=False, server_default="pencil"),
        sa.Column("picture_words", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("book_id", sa.String(length=32), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_jobs_book_id", "jobs", ["book_id"])
    op.create_index("ix_jobs_state", "jobs", ["state"])
    op.create_table(
        "narration_clips",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("book_id", sa.String(length=32), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_id", sa.String(length=16), nullable=False),
        sa.Column("sentence_id", sa.String(length=16), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("voice", sa.String(length=64), nullable=False),
        sa.Column("cache_key", sa.String(length=32), nullable=False),
        sa.Column("pause_after", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.UniqueConstraint("book_id", "sentence_id", "voice"),
    )
    op.create_index("ix_narration_clips_book_id", "narration_clips", ["book_id"])
    op.create_index("ix_narration_clips_cache_key", "narration_clips", ["cache_key"])
    op.create_table(
        "scenes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("book_id", sa.String(length=32), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan", sa.String(length=64), nullable=False),
        sa.Column("scene_id", sa.String(length=32), nullable=False),
        sa.Column("section_id", sa.String(length=16), nullable=False),
        sa.Column("first_sentence", sa.Integer(), nullable=False),
        sa.Column("last_sentence", sa.Integer(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("cache_key", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("rendered_style", sa.String(length=16), nullable=True),
        sa.Column("drawn_at", sa.DateTime(), nullable=True),
        sa.Column("draw_seconds", sa.Float(), nullable=True),
        sa.UniqueConstraint("book_id", "plan", "scene_id"),
    )
    op.create_index("ix_scenes_book_id", "scenes", ["book_id"])
    op.create_index("ix_scenes_cache_key", "scenes", ["cache_key"])
    op.create_index("ix_scenes_book_section_plan", "scenes", ["book_id", "section_id", "plan"])
    op.create_table(
        "spending",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("book_id", sa.String(length=32), nullable=False),
        sa.Column("usd", sa.Float(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_spending_day", "spending", ["day"])


def downgrade() -> None:
    for table in ("spending", "scenes", "narration_clips", "jobs", "books"):
        op.drop_table(table)
