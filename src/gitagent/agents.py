"""Agent lifecycle: register, unregister, list, validate.

Agents are auto-assigned ``a_<hex>`` ids at registration. The agent passes
its id explicitly on every subsequent tool call — no cwd/env inference.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from .db import Database, get_db
from .errors import GitAgentError
from .session import get_session


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _aid() -> str:
    return f"a_{secrets.token_hex(4)}"


def register_agent(role: str = "", db: Database | None = None) -> dict:
    """Register a new agent in the current open session.

    Returns ``{agent_id}``.
    """
    db = db or get_db()
    session = get_session(db)
    if session is None:
        raise GitAgentError("No open session. Start one first.")

    aid = _aid()
    now = _now()

    db.execute(
        """INSERT INTO agents (id, session_id, role, started_at)
           VALUES (?, ?, ?, ?)""",
        (aid, session["id"], role, now),
    )
    db.commit()

    return {"agent_id": aid}


def unregister_agent(agent_id: str, db: Database | None = None) -> None:
    """Mark an agent as ended."""
    db = db or get_db()
    now = _now()
    db.execute(
        "UPDATE agents SET ended_at = ? WHERE id = ?",
        (now, agent_id),
    )
    db.commit()


def list_agents(db: Database | None = None) -> list[dict]:
    """List all agents in the current open session."""
    db = db or get_db()
    session = get_session(db)
    if session is None:
        return []

    rows = db.fetchall(
        "SELECT * FROM agents WHERE session_id = ? ORDER BY started_at",
        (session["id"],),
    )
    return [dict(r) for r in rows]


def validate_agent(agent_id: str, db: Database | None = None) -> dict:
    """Validate that agent_id is active in the current open session.

    Returns the agent row as a dict. Raises on any failure.
    """
    db = db or get_db()
    session = get_session(db)
    if session is None:
        raise GitAgentError("No open session.")

    row = db.fetchone(
        "SELECT * FROM agents WHERE id = ? AND session_id = ?",
        (agent_id, session["id"]),
    )
    if row is None:
        raise GitAgentError(
            f"Agent '{agent_id}' not found in session '{session['id']}'. "
            "Register first with register_agent."
        )
    if row["ended_at"] is not None:
        raise GitAgentError(
            f"Agent '{agent_id}' is ended (ended_at={row['ended_at']})."
        )
    return dict(row)
