"""session_media_dirs — server-generated media directory per session

Revision ID: 6media_dirs
Revises: 5session_notes
Create Date: 2026-10-01

Additive. Maps a session to the opaque directory name its generated images live
under.

The directory name is NOT the session id, deliberately. ``POST /sessions/import``
accepts a client-supplied ``session.id`` (routes/sessions.py), ids are opaque
TEXT, and a value like "../../.." would become a filesystem path. A
server-generated lowercase-hex name removes the input from the path entirely.
Lowercase also matters: APFS is case-insensitive by default, so two ids
differing only in case would collapse into one directory and a reset would
destroy both.

A side table rather than a column on ``chat_sessions``: SessionRepository has no
``_ensure_table``, so an ALTER TABLE there would have no dual cover and the
column would silently not exist wherever ``init_db()`` swallows the migration.

Dual-covered by MediaRepository._ensure_table for Docker/test envs that skip
alembic.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = '6media_dirs'
down_revision: str | None = '5session_notes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'session_media_dirs',
        sa.Column('session_id', sa.Text(), nullable=False),
        sa.Column('media_dir', sa.Text(), nullable=False),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('session_id'),
        sa.UniqueConstraint('media_dir', name='uq_session_media_dirs_media_dir'),
    )


def downgrade() -> None:
    op.drop_table('session_media_dirs')
