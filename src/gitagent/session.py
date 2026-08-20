"""Session lifecycle: start, abort, get, list.

Multiple sessions can be open at the same time, all sharing ONE global
worktree (``.gitagent/worktree``). The target branch is fixed at the
worktree level: the first session that creates the worktree chooses it, and
later sessions inherit it (``start_session`` ignores a new target).

Aborting a session is logical; the worktree is only removed by the last open
session's abort.
"""
from __future__ import annotations

import re
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path

from . import gitwrap
from .db import Database, get_db
from .errors import GitAgentError


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sid() -> str:
    return f"s_{secrets.token_hex(4)}"


def list_sessions(db: Database | None = None) -> list[dict]:
    """Return all sessions, newest first."""
    db = db or get_db()
    rows = db.fetchall("SELECT * FROM session ORDER BY created_at DESC")
    return [dict(r) for r in rows]


def get_session(
    session_id: str | None = None, *, db: Database | None = None
) -> dict | None:
    """Return a session by id, or the single open session when None.

    When *session_id* is None and multiple sessions are open, raises — pass
    an explicit id instead.
    """
    db = db or get_db()
    if session_id is not None:
        row = db.fetchone("SELECT * FROM session WHERE id = ?", (session_id,))
        return dict(row) if row else None

    rows = db.fetchall("SELECT * FROM session WHERE state = 'open' ORDER BY created_at")
    if len(rows) == 1:
        return dict(rows[0])
    if len(rows) > 1:
        raise GitAgentError(
            "Multiple open sessions. Pass session_id explicitly."
        )
    return None


def start_session(
    feature: str,
    target_branch: str = "main",
    *,
    lock_ttl_seconds: int = 15,
    db: Database | None = None,
) -> dict:
    """Create a new session on the shared worktree.

    The first session deletes any stale worktree and creates a fresh detached
    one at the current repo HEAD. Later sessions reuse the existing worktree
    and its fixed target_branch (a new target is ignored).

    Returns ``{session_id, worktree, base_sha, target_branch, lock_ttl_seconds}``.
    """
    db = db or get_db()
    if not feature or not feature.strip():
        raise GitAgentError("Feature is required.")
    if not target_branch or not re.fullmatch(r"[A-Za-z0-9._/-]+", target_branch):
        raise GitAgentError(f"Invalid target branch: {target_branch!r}")
    if lock_ttl_seconds is None or lock_ttl_seconds < 1:
        raise GitAgentError("lock_ttl_seconds must be >= 1.")

    db.conn.execute("BEGIN IMMEDIATE")
    try:
        existing = db.fetchone(
            "SELECT * FROM session WHERE state = 'open' ORDER BY created_at "
            "DESC LIMIT 1"
        )

        repo = gitwrap.repo_root()
        if existing is not None:
            # Reuse the shared worktree and its target branch.
            wt = Path(existing["worktree"])
            if not wt.exists():
                raise GitAgentError(
                    f"Shared worktree missing: {wt}. Abort open sessions first."
                )
            sid = _sid()
            now = _now()
            db.execute(
                """INSERT INTO session
                   (id, feature, target_branch, base_sha, worktree, state,
                    created_at, lock_ttl_seconds)
                   VALUES (?, ?, ?, ?, ?, 'open', ?, ?)""",
                (
                    sid,
                    feature.strip(),
                    existing["target_branch"],
                    existing["base_sha"],
                    str(wt),
                    now,
                    lock_ttl_seconds,
                ),
            )
            db.commit()
            return {
                "session_id": sid,
                "worktree": str(wt),
                "base_sha": existing["base_sha"],
                "target_branch": existing["target_branch"],
                "lock_ttl_seconds": lock_ttl_seconds,
            }

        base_sha = gitwrap.current_sha(cwd=repo)
        wt = repo / ".gitagent" / "worktree"
        if wt.exists():
            shutil.rmtree(wt, ignore_errors=True)
        gitwrap.worktree_prune(cwd=repo)
        gitwrap.worktree_add_detached(wt, base_sha, cwd=repo)

        sid = _sid()
        now = _now()
        db.execute(
            """INSERT INTO session
               (id, feature, target_branch, base_sha, worktree, state,
                created_at, lock_ttl_seconds)
               VALUES (?, ?, ?, ?, ?, 'open', ?, ?)""",
            (
                sid,
                feature.strip(),
                target_branch,
                base_sha,
                str(wt),
                now,
                lock_ttl_seconds,
            ),
        )
        db.commit()
        return {
            "session_id": sid,
            "worktree": str(wt),
            "base_sha": base_sha,
            "target_branch": target_branch,
            "lock_ttl_seconds": lock_ttl_seconds,
        }
    except Exception:
        db.conn.rollback()
        raise


def abort_session(session_id: str, *, db: Database | None = None) -> None:
    """Mark a session aborted.

    The shared worktree is only removed when this is the LAST open session.
    Other open sessions keep the worktree alive.
    """
    db = db or get_db()
    row = db.fetchone("SELECT * FROM session WHERE id = ?", (session_id,))
    if row is None:
        raise GitAgentError(f"No session with id '{session_id}'.")
    if row["state"] != "open":
        raise GitAgentError(
            f"Session '{session_id}' is already '{row['state']}'."
        )

    others = db.fetchall(
        "SELECT id FROM session WHERE state = 'open' AND id != ?",
        (session_id,),
    )

    repo = gitwrap.repo_root()
    wt = Path(row["worktree"])
    if not others:
        # Last open session: tear down the shared worktree.
        gitwrap.worktree_remove(wt, force=True, cwd=repo)
        gitwrap.worktree_prune(cwd=repo)

    now = _now()
    db.execute(
        "UPDATE session SET state = 'aborted', ended_at = ? WHERE id = ?",
        (now, session_id),
    )
    db.commit()