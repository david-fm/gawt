"""File editing: edit, write, read, delete_file with atomic writes and conflict detection.

All writes go through atomic_write (temp + os.replace). Conflict detection
is best-effort: advisory inbox entries, never blocking.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .agents import validate_agent
from .db import Database, get_db
from .errors import GitAgentError
from .intents import get_current_intent
from .session import get_session


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _worktree(db: Database | None = None) -> Path:
    session = get_session(db)
    if session is None:
        raise GitAgentError("No open session.")
    return Path(session["worktree"])


def _resolve(file: str, db: Database | None = None) -> Path:
    """Resolve a relative file path inside the worktree. Rejects escapes."""
    wt = _worktree(db)
    p = (wt / file).resolve()
    if not p.is_relative_to(wt.resolve()):
        raise GitAgentError(f"Path escapes worktree: {file}")
    return p


def _atomic_write(target: Path, content: bytes) -> None:
    """Write *content* to *target* atomically via temp + rename."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        os.write(fd, content)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, target)


def _detect_conflicts(
    agent_id: str, file: str, db: Database, window_seconds: int = 30
) -> list[dict]:
    """Check if another agent edited this file recently."""
    from .inbox import _send_inbox  # avoid circular

    session = get_session(db)
    rows = db.fetchall(
        """SELECT agent_id, ts FROM edits
           WHERE session_id = ? AND file = ?
           ORDER BY ts DESC
           LIMIT 10""",
        (session["id"], file),
    )

    notifications = []
    for row in rows:
        if row["agent_id"] == agent_id:
            continue
        # Check if within the conflict window
        try:
            edit_ts = datetime.fromisoformat(row["ts"])
            now = datetime.now(UTC)
            diff = (now - edit_ts).total_seconds()
            if diff <= window_seconds:
                other = row["agent_id"]
                # Notify the other agent
                _send_inbox(
                    other, agent_id, "conflict",
                    {"file": file, "conflicting_agent": agent_id},
                    db=db,
                )
                # Notify self
                _send_inbox(
                    agent_id, other, "conflict",
                    {"file": file, "other_edit_ts": row["ts"]},
                    db=db,
                )
                notifications.append({"agent": other, "ts": row["ts"]})
        except (ValueError, TypeError):
            continue

    return notifications


def _record_edit(
    agent_id: str, file: str, op: str, db: Database,
    *, old_string: str | None = None, new_string: str | None = None,
    full_content: str | None = None, intent_id: int | None = None,
) -> None:
    session = get_session(db)
    now = _now()
    db.execute(
        """INSERT INTO edits
           (agent_id, session_id, file, op, old_string, new_string,
            full_content, intent_id, ts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (agent_id, session["id"], file, op, old_string, new_string,
         full_content, intent_id, now),
    )
    db.commit()


def edit(
    agent_id: str,
    file: str,
    old_string: str,
    new_string: str,
    *,
    replace_all: bool = False,
    expected_sha256: str | None = None,
    db: Database | None = None,
) -> dict:
    """Exact-match string replacement with atomic write.

    Returns ``{ok: True, path, matches}`` on success.
    Raises ``old_string_not_found`` or ``ambiguous_match`` on stale reads.
    """
    db = db or get_db()
    validate_agent(agent_id, db)
    target = _resolve(file, db)

    if not target.exists():
        raise GitAgentError(f"File not found: {file}")

    content = target.read_text(encoding="utf-8")
    if expected_sha256 is not None:
        actual_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if actual_sha256 != expected_sha256:
            raise GitAgentError(
                f"STALE_WRITE: '{file}' changed after it was read. "
                "Read the file again before editing."
            )

    # Count occurrences
    count = content.count(old_string)

    if count == 0:
        raise GitAgentError(
            f"old_string_not_found in '{file}'. "
            "Read the file first and retry with current content."
        )

    if not replace_all and count > 1:
        raise GitAgentError(
            f"ambiguous_match: old_string appears {count} times in '{file}'. "
            "Use replace_all=True or provide more context."
        )

    # Compute new content
    if replace_all:
        new_content = content.replace(old_string, new_string)
    else:
        new_content = content.replace(old_string, new_string, 1)

    # Atomic write
    _atomic_write(target, new_content.encode("utf-8"))

    # Conflict detection
    _detect_conflicts(agent_id, file, db)

    # Record edit
    intent = get_current_intent(agent_id, db)
    intent_id = intent["intent_id"] if intent else None
    _record_edit(
        agent_id, file, "edit", db,
        old_string=old_string, new_string=new_string,
        intent_id=intent_id,
    )

    return {"ok": True, "path": file, "matches": count}


def write(
    agent_id: str,
    file: str,
    content: str,
    *,
    expected_sha256: str | None = None,
    db: Database | None = None,
) -> dict:
    """Create or overwrite a file with atomic write.

    Returns ``{ok: True, path}``.
    """
    db = db or get_db()
    validate_agent(agent_id, db)
    target = _resolve(file, db)

    if expected_sha256 is not None:
        if not target.exists():
            raise GitAgentError(f"STALE_WRITE: expected existing file '{file}'.")
        actual_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual_sha256 != expected_sha256:
            raise GitAgentError(
                f"STALE_WRITE: '{file}' changed after it was read. "
                "Read the file again before writing."
            )

    _atomic_write(target, content.encode("utf-8"))

    _detect_conflicts(agent_id, file, db)

    intent = get_current_intent(agent_id, db)
    intent_id = intent["intent_id"] if intent else None
    _record_edit(
        agent_id, file, "write", db,
        full_content=content, intent_id=intent_id,
    )

    return {"ok": True, "path": file}


def read(agent_id: str, file: str, *, db: Database | None = None) -> dict:
    """Read a file. Returns ``{content, sha256, path}``.

    No tracking — reads are not recorded.
    """
    db = db or get_db()
    validate_agent(agent_id, db)
    target = _resolve(file, db)

    if not target.exists():
        raise GitAgentError(f"File not found: {file}")

    content = target.read_text(encoding="utf-8")
    sha = hashlib.sha256(content.encode()).hexdigest()

    return {"content": content, "sha256": sha, "path": file}


def delete_file(
    agent_id: str, file: str, *, expected_sha256: str | None = None,
    db: Database | None = None
) -> dict:
    """Remove a file. Returns ``{ok: True, path}``."""
    db = db or get_db()
    validate_agent(agent_id, db)
    target = _resolve(file, db)

    if target.exists():
        if expected_sha256 is not None:
            actual_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual_sha256 != expected_sha256:
                raise GitAgentError(
                    f"STALE_WRITE: '{file}' changed after it was read."
                )
        target.unlink()

    _detect_conflicts(agent_id, file, db)

    intent = get_current_intent(agent_id, db)
    intent_id = intent["intent_id"] if intent else None
    _record_edit(agent_id, file, "delete", db, intent_id=intent_id)

    return {"ok": True, "path": file}


def list_edits(
    *,
    agent_id: str | None = None,
    file: str | None = None,
    since_ts: str | None = None,
    db: Database | None = None,
) -> list[dict]:
    """Debug view: list edits with optional filters."""
    db = db or get_db()
    session = get_session(db)
    if session is None:
        return []

    clauses = ["session_id = ?"]
    params: list = [session["id"]]

    if agent_id:
        clauses.append("agent_id = ?")
        params.append(agent_id)
    if file:
        clauses.append("file = ?")
        params.append(file)
    if since_ts:
        clauses.append("ts >= ?")
        params.append(since_ts)

    where = " AND ".join(clauses)
    rows = db.fetchall(
        f"SELECT * FROM edits WHERE {where} ORDER BY ts",
        tuple(params),
    )
    return [dict(r) for r in rows]
