"""File editing: edit, write, read, delete_file with atomic writes and locks.

The pheromone model: every write leaves a traceable mark in ``edits``
(``agent_id, intent_id, op, file``). Each write acquires the per-file lock
first and ALWAYS releases it in a ``finally``. If another active agent holds
a fresh lock, the write is **rejected** (never applied) and the response
carries the full ``read`` payload so the agent can re-plan informed.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from . import gitwrap, locks, session
from .agents import validate_agent
from .db import Database, get_db
from .errors import GitAgentError
from .intents import get_current_intent

WARNING = (
    "La intención debe estar actualizada antes de escribir nada "
    "(start_intent/repurpose)."
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _agent_session(agent: dict, db: Database) -> dict:
    s = session.get_session(agent["session_id"], db=db)
    if s is None:
        raise GitAgentError(f"Session '{agent['session_id']}' not found.")
    return s


def _resolve(file: str, srow: dict) -> Path:
    """Resolve a relative file path inside the worktree. Rejects escapes."""
    wt = Path(srow["worktree"]).resolve()
    p = (wt / file).resolve()
    if not p.is_relative_to(wt):
        raise GitAgentError(f"Path escapes worktree: {file}")
    return p


def _atomic_write(target: Path, content: bytes) -> None:
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


def _record_edit(
    agent_id: str,
    srow: dict,
    file: str,
    op: str,
    db: Database,
    *,
    old_string: str | None = None,
    new_string: str | None = None,
    full_content: str | None = None,
    intent_id: int | None = None,
    replace_all: bool = False,
) -> None:
    db.execute(
        """INSERT INTO edits
           (agent_id, session_id, file, op, old_string, new_string,
            full_content, intent_id, replace_all, ts)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (agent_id, srow["id"], file, op, old_string, new_string,
         full_content, intent_id, int(replace_all), _now()),
    )
    db.commit()


def _edit_rows(file: str, db: Database) -> list[dict]:
    rows = db.fetchall(
        """SELECT e.*, a.role AS role
           FROM edits e LEFT JOIN agents a ON a.id = e.agent_id
           WHERE e.file = ? ORDER BY e.id""",
        (file,),
    )
    return [dict(r) for r in rows]


def _read_payload(
    agent_id: str, file: str, db: Database, agent: dict, srow: dict
) -> dict:
    """Build the informed read payload (also used for rejection responses)."""
    target = _resolve(file, srow)
    if not target.exists():
        raise GitAgentError(f"File not found: {file}")

    content = target.read_text(encoding="utf-8")
    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    cwd = Path(srow["worktree"])
    base_sha = gitwrap.current_sha(cwd=gitwrap.main_repo_root(cwd))
    _, diff = gitwrap.diff_vs_ref(srow["target_branch"], file, cwd=cwd)

    return {
        "content": content,
        "sha256": sha,
        "path": file,
        "base_sha": base_sha,
        "diff": diff,
        "edits": _edit_rows(file, db),
    }


def _reject(
    agent: dict,
    file: str,
    db: Database,
    srow: dict,
    *,
    reason: str,
    blocked_by: dict | None = None,
) -> dict:
    """Build a structured 'rejected' response carrying the informed read."""
    try:
        read_payload = _read_payload(agent["id"], file, db, agent, srow)
    except GitAgentError:
        read_payload = None
    resp: dict = {"status": "rejected", "reason": reason}
    if blocked_by is not None:
        resp["blocked_by"] = {
            "agent_id": blocked_by.get("holder_agent"),
            "session_id": blocked_by.get("session_id"),
        }
        holder = db.fetchone(
            "SELECT role FROM agents WHERE id = ?",
            (blocked_by.get("holder_agent"),),
        )
        if holder is not None:
            resp["blocked_by"]["role"] = holder["role"]
    resp["read"] = read_payload
    return resp


def _lock_error_reason(locked_by: dict | None) -> str:
    if locked_by is None:
        return "file locked by another agent"
    return f"file locked by agent {locked_by.get('holder_agent')}"


def _reject_for_lock(
    acquired: dict, agent: dict, file: str, db: Database, srow: dict
) -> dict:
    return _reject(
        agent,
        file,
        db,
        srow,
        reason=_lock_error_reason(acquired.get("blocked_by")),
        blocked_by=acquired.get("blocked_by"),
    )


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
    """Exact-match string replacement with atomic write + lock.

    On success returns ``{ok: True, path, matches}``. A fresh foreign lock
    (or STALE_WRITE) returns ``{status: 'rejected', read: {...}}`` with no
    write applied. Raises ``old_string_not_found`` / ``ambiguous_match`` on
    stale reads.
    """
    db = db or get_db()
    agent = validate_agent(agent_id, db)
    srow = _agent_session(agent, db)
    target = _resolve(file, srow)

    acquired = locks.acquire(
        file, agent_id, srow["id"],
        ttl_seconds=int(srow["lock_ttl_seconds"]), db=db,
    )
    token = None
    try:
        if acquired["status"] == "held_elsewhere":
            return _reject_for_lock(acquired, agent, file, db, srow)
        token = acquired["token"]

        if not target.exists():
            raise GitAgentError(f"File not found: {file}")

        content = target.read_text(encoding="utf-8")
        if expected_sha256 is not None:
            actual_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if actual_sha256 != expected_sha256:
                return _reject(
                    agent, file, db, srow, reason="STALE_WRITE"
                )

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

        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        _atomic_write(target, new_content.encode("utf-8"))

        intent = get_current_intent(agent_id, db)
        intent_id = intent["intent_id"] if intent else None
        _record_edit(
            agent_id, srow, file, "edit", db,
            old_string=old_string, new_string=new_string,
            intent_id=intent_id, replace_all=replace_all,
        )
        return {"ok": True, "path": file, "matches": count}
    finally:
        if token is not None:
            locks.release(file, token, db=db)


def write(
    agent_id: str,
    file: str,
    content: str,
    *,
    expected_sha256: str | None = None,
    db: Database | None = None,
) -> dict:
    """Create or overwrite a file with atomic write + lock."""
    db = db or get_db()
    agent = validate_agent(agent_id, db)
    srow = _agent_session(agent, db)
    target = _resolve(file, srow)

    acquired = locks.acquire(
        file, agent_id, srow["id"],
        ttl_seconds=int(srow["lock_ttl_seconds"]), db=db,
    )
    token = None
    try:
        if acquired["status"] == "held_elsewhere":
            return _reject_for_lock(acquired, agent, file, db, srow)
        token = acquired["token"]

        if expected_sha256 is not None:
            if not target.exists():
                raise GitAgentError(f"STALE_WRITE: expected existing file '{file}'.")
            actual_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual_sha256 != expected_sha256:
                return _reject(agent, file, db, srow, reason="STALE_WRITE")

        _atomic_write(target, content.encode("utf-8"))

        intent = get_current_intent(agent_id, db)
        intent_id = intent["intent_id"] if intent else None
        _record_edit(
            agent_id, srow, file, "write", db,
            full_content=content, intent_id=intent_id,
        )
        return {"ok": True, "path": file}
    finally:
        if token is not None:
            locks.release(file, token, db=db)


def read(agent_id: str, file: str, *, db: Database | None = None) -> dict:
    """Informed read. Returns content + sha + base_sha + diff + edits + warning."""
    db = db or get_db()
    agent = validate_agent(agent_id, db)
    srow = _agent_session(agent, db)
    payload = _read_payload(agent["id"], file, db, agent, srow)
    payload["warning"] = WARNING
    return payload


def delete_file(
    agent_id: str, file: str, *, expected_sha256: str | None = None,
    db: Database | None = None,
) -> dict:
    """Remove a file with lock. Returns ``{ok: True, path}``."""
    db = db or get_db()
    agent = validate_agent(agent_id, db)
    srow = _agent_session(agent, db)
    target = _resolve(file, srow)

    acquired = locks.acquire(
        file, agent_id, srow["id"],
        ttl_seconds=int(srow["lock_ttl_seconds"]), db=db,
    )
    token = None
    try:
        if acquired["status"] == "held_elsewhere":
            return _reject_for_lock(acquired, agent, file, db, srow)
        token = acquired["token"]

        if target.exists():
            if expected_sha256 is not None:
                actual_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
                if actual_sha256 != expected_sha256:
                    return _reject(agent, file, db, srow, reason="STALE_WRITE")
            target.unlink()

        intent = get_current_intent(agent_id, db)
        intent_id = intent["intent_id"] if intent else None
        _record_edit(agent_id, srow, file, "delete", db, intent_id=intent_id)
        return {"ok": True, "path": file}
    finally:
        if token is not None:
            locks.release(file, token, db=db)


def list_edits(
    *,
    agent_id: str | None = None,
    file: str | None = None,
    since_ts: str | None = None,
    session_id: str | None = None,
    limit: int | None = None,
    db: Database | None = None,
) -> list[dict]:
    """Debug/observability view: list edits with optional filters.

    *limit* caps the number of rows returned (orchestrators use it to pick
    a snapshot boundary).
    """
    db = db or get_db()
    clauses: list[str] = []
    params: list = []
    if agent_id:
        clauses.append("agent_id = ?")
        params.append(agent_id)
    if file:
        clauses.append("file = ?")
        params.append(file)
    if since_ts:
        clauses.append("ts >= ?")
        params.append(since_ts)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    snippet = " LIMIT ?" if limit is not None else ""
    if limit is not None:
        params.append(limit)
    rows = db.fetchall(
        f"SELECT * FROM edits{where} ORDER BY id{snippet}", tuple(params)
    )
    return [dict(r) for r in rows]