"""image_jobs — durable record of a generation, so a restart cannot lose one

Revision ID: 7image_jobs
Revises: 6media_dirs
Create Date: 2026-10-02

Additive. A generation takes ~331 s (measured) and runs as a detached
subprocess that deliberately outlives the coordinator. The row is the only
thing that connects "a process is running on this machine" to "somebody asked
for this, in this session, and is waiting for it" — the supervisor knows the
former and nothing else knows the latter.

Why the process identity is stored (``pid``, ``pgid``, ``proc_start``) and not
just a lease timestamp: after a restart we are not the subprocess's parent and
can never ``waitpid`` it, so the usual distributed-queue answer — expire the
lease and assume death — would be guessing. On one machine with a real
subprocess we can do better: ``supervisor.probe()`` reads the inherited
``flock`` and tells us RUNNING / FINISHED / DIED for certain. ``proc_start`` is
stored alongside ``pgid`` because a PID can be recycled, and signalling a
recycled PID would kill an unrelated process.

``heartbeat_at`` is kept anyway as the backstop for the case the lock cannot
cover: a job directory deleted out from under us, or a filesystem that cannot
report the lock. Lease expiry is the weaker instrument, so it is second.

``notified_at`` makes delivery idempotent. The completion path and the
reconciliation sweeper can both decide a job is finished, and without this
column a restart mid-delivery sends the image twice. The conditional
``UPDATE ... WHERE notified_at IS NULL`` is what makes the race safe; the
column is what makes the conditional possible.

Status is TEXT with a CHECK rather than an enum table: SQLite has no native
enum, and a CHECK keeps an invalid status out of the file rather than relying
on every writer being careful.

Dual-covered by ImageJobRepository._ensure_table, because ``init_db()``
swallows migration failures (``di/repositories.py``) and loads ``alembic.ini``
by RELATIVE path — so in any environment whose cwd is not the repo root, the
migration silently does not run and the repository's own CREATE is the only
thing that makes the table exist.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = '7image_jobs'
down_revision: str | None = '6media_dirs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'image_jobs',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('session_id', sa.Text(), nullable=False),
        sa.Column('persona_key', sa.Text(), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default='queued'),
        sa.Column('job_dir', sa.Text(), nullable=True),
        sa.Column('media_path', sa.Text(), nullable=True),
        sa.Column('pid', sa.Integer(), nullable=True),
        sa.Column('pgid', sa.Integer(), nullable=True),
        sa.Column('proc_start', sa.Float(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.Column('started_at', sa.Text(), nullable=True),
        sa.Column('finished_at', sa.Text(), nullable=True),
        sa.Column('heartbeat_at', sa.Text(), nullable=True),
        sa.Column('notified_at', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')",
            name='ck_image_jobs_status',
        ),
    )
    # The worker's hot query: "is anything queued?", asked every poll.
    op.create_index('ix_image_jobs_status', 'image_jobs', ['status'])
    # The gateway's hot query: "anything finished that nobody has been told
    # about?" — status AND notified_at together, because a partial index on
    # status alone still scans every succeeded job ever generated.
    op.create_index(
        'ix_image_jobs_pending_notify', 'image_jobs', ['status', 'notified_at']
    )


def downgrade() -> None:
    op.drop_index('ix_image_jobs_pending_notify', table_name='image_jobs')
    op.drop_index('ix_image_jobs_status', table_name='image_jobs')
    op.drop_table('image_jobs')
