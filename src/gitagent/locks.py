"""Per-file write locks with TTL-based stale lock reclamation.

``write_file`` / ``edit_file`` / ``delete_file`` acquire the lock for the
target file at the start and ALWAYS release it in a ``finally``. If another
active agent holds a fresh lock, the write is *rejected* (never applied) and
the caller is expected to re-plan using the informed ``read`` payload.

Locks are optimistic: no writer queues, no waiting inside the MCP server.
A lock older than the session TTL (default 15s) is considered orphaned by a
crash and is reclaimed.
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime

from .db import Database, get_db

DEFAULT_TTL_SECONDS = 15


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _is_stale(acquired_at: str, ttl_seconds: int) -> bool:
    try:
        acquired = datetime.fromisoformat(acquired_at)
        return (datetime.now(UTC) - acquired).total_seconds() > ttl_seconds
    except (ValueError, TypeError):
        # Unreadable timestamp -> assume stale.
        return True


def acquire(
    file: str,
    holder_agent: str,
    session_id: str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    db: Database | None = None,
) -> dict:
    """Try to acquire the lock for *file*.

    Returns ``{"status": "held", "token": token}`` on success (new, reclaimed,
    or idempotent re-entry by the same holder).

    Returns ``{"status": "held_elsewhere", "blocked_by": {...}}`` when a fresh
    lock is held by another agent — the write must NOT be applied.
    """
    db = db or get_db()
    conn = db.conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM locks WHERE file = ?", (file,)
        ).fetchone()
        now = _now()

        if row is None:
            token = secrets.token_hex(8)
            conn.execute(
                """INSERT INTO locks (file, holder_agent, session_id, token,
                   acquired_at) VALUES (?, ?, ?, ?, ?)""",
                (file, holder_agent, session_id, token, now),
            )
            conn.commit()
            return {"status": "held", "token": token}

        lock = dict(row)
        if lock["holder_agent"] == holder_agent and lock["session_id"] == session_id:
            # Idempotent re-entry: keep the existing token.
            conn.commit()
            return {"status": "held", "token": lock["token"]}

        if _is_stale(lock["acquired_at"], ttl_seconds):
            token = secrets.token_hex(8)
            conn.execute(
                """UPDATE locks SET holder_agent = ?, session_id = ?, token = ?,
                   acquired_at = ? WHERE file = ?""",
                (holder_agent, session_id, token, now, file),
            )
            conn.commit()
            return {"status": "held", "token": token}

        # Fresh lock held by another agent: do not wait, do not write.
        conn.rollback()
        return {"status": "held_elsewhere", "blocked_by": lock}
    except Exception:
        conn.rollback()
        raise


def release(file: str, token: str, *, db: Database | None = None) -> None:
    """Release the lock iff *token* still matches (never another holder's)."""
    db = db or get_db()
    db.execute(
        "DELETE FROM locks WHERE file = ? AND token = ?", (file, token)
    )
    db.commit()