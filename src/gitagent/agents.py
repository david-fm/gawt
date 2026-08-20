"""Agent lifecycle: register, unregister, list, validate.

Agents are auto-assigned ``a_<hex>`` ids at registration. The agent passes
its id explicitly on every subsequent tool call — no cwd/env inference.
With multiple open sessions, ``session_id`` is REQUIRED at registration.
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime

from .db import Database, get_db
from .errors import GitAgentError


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _aid() -> str:
    return f"a_{secrets.token_hex(4)}"


def register_agent(
    role: str = "", session_id: str | None = None, db: Database | None = None
) -> dict:
    """Register a new agent.

    With a single open session, *session_id* is optional. With two or more
    open sessions, *session_id* is REQUIRED (hard error otherwise).
    """
    db = db or get_db()
    if session_id is not None:
        row = db.fetchone(
            "SELECT state FROM session WHERE id = ?", (session_id,)
        )
        if row is None:
            raise GitAgentError(f"No session with id '{session_id}'.")
        if row["state"] != "open":
            raise GitAgentError(
                f"Session '{session_id}' is not open (state={row['state']})."
            )
        open_sessions = [session_id]
    else:
        open_sessions = [
            r["id"]
            for r in db.fetchall("SELECT id FROM session WHERE state = 'open'")
        ]
        if not open_sessions:
            raise GitAgentError("No open session. Start one first.")
        if len(open_sessions) > 1:
            raise GitAgentError(
                "Multiple open sessions — session_id is required. "
                "Pass the session_id of the session this agent belongs to."
            )

    sid = open_sessions[0]
    aid = _aid()
    now = _now()

    db.execute(
        """INSERT INTO agents (id, session_id, role, started_at)
           VALUES (?, ?, ?, ?)""",
        (aid, sid, role, now),
    )
    db.commit()

    return {"agent_id": aid, "session_id": sid}


def unregister_agent(agent_id: str, db: Database | None = None) -> None:
    """Mark an agent as ended."""
    db = db or get_db()
    now = _now()
    db.execute(
        "UPDATE agents SET ended_at = ? WHERE id = ?", (now, agent_id)
    )
    db.commit()


def list_agents(
    session_id: str | None = None, db: Database | None = None
) -> list[dict]:
    """List agents, optionally scoped to a session. None = across all."""
    db = db or get_db()
    if session_id is not None:
        rows = db.fetchall(
            "SELECT * FROM agents WHERE session_id = ? ORDER BY started_at",
            (session_id,),
        )
    else:
        rows = db.fetchall("SELECT * FROM agents ORDER BY started_at")
    return [dict(r) for r in rows]


def validate_agent(agent_id: str, db: Database | None = None) -> dict:
    """Validate that agent_id is active. Returns the agent row as a dict."""
    db = db or get_db()
    row = db.fetchone("SELECT * FROM agents WHERE id = ?", (agent_id,))
    if row is None:
        raise GitAgentError(
            f"Agent '{agent_id}' not found. Register first with register_agent."
        )
    if row["ended_at"] is not None:
        raise GitAgentError(
            f"Agent '{agent_id}' is ended (ended_at={row['ended_at']})."
        )
    return dict(row)