"""MCP server entrypoint for gitagent v0.6.0.

Exposes all gitagent operations as MCP tools via stdio transport. The inbox
is gone; coordination emerges from the pheromone (edits log) + informed
writes (lock + rejection) + partial snapshots.
"""
from __future__ import annotations

from mcp.server import MCPServer

from . import agents, edits, intents, session, snapshot
from .errors import GitAgentError

server = MCPServer("gitagent", version="0.6.1")


def _err(e: Exception) -> dict[str, str]:
    """Return machine-readable MCP tool error payload."""
    message = str(e)
    code = message.split(":", 1)[0] if ":" in message else type(e).__name__
    return {"error": code, "message": message}


# ---------------------------------------------------------------------------
# Session lifecycle (multi-session, shared worktree)
# ---------------------------------------------------------------------------

@server.tool()
def start_session(
    feature: str,
    target_branch: str = "main",
    lock_ttl_seconds: int = 15,
) -> dict:
    """Start a session on the shared worktree. Target is fixed per worktree."""
    try:
        return session.start_session(
            feature, target_branch, lock_ttl_seconds=lock_ttl_seconds
        )
    except GitAgentError as e:
        return _err(e)


@server.tool()
def abort_session(session_id: str) -> dict:
    """Mark a session aborted. Removes the worktree only if it's the last open."""
    try:
        session.abort_session(session_id)
        return {"ok": True}
    except GitAgentError as e:
        return _err(e)


@server.tool()
def get_session(session_id: str | None = None) -> dict | None:
    """Return a session by id, or the single open session when None."""
    try:
        return session.get_session(session_id)
    except GitAgentError as e:
        return _err(e)


@server.tool()
def list_sessions() -> list[dict]:
    """List all sessions, newest first."""
    try:
        return session.list_sessions()
    except GitAgentError as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Snapshots (partial commits + status)
# ---------------------------------------------------------------------------

@server.tool()
def snapshot_session(
    session_id: str,
    message: str,
    files: list[str] | None = None,
    boundary_edit_id: int | None = None,
    sign: bool = False,
) -> dict:
    """Partial commit: publish part of the shared worktree to the target branch."""
    try:
        return snapshot.snapshot_session(
            session_id, message, files=files,
            boundary_edit_id=boundary_edit_id, sign=sign,
        )
    except GitAgentError as e:
        return _err(e)


@server.tool()
def snapshot_status(session_id: str) -> dict:
    """Whole-worktree git status/diff vs target, with per-file pheromone."""
    try:
        return snapshot.snapshot_status(session_id)
    except GitAgentError as e:
        return _err(e)


@server.tool()
def list_snapshots(session_id: str | None = None) -> list[dict]:
    """List recorded snapshots, optionally filtered by session."""
    try:
        return snapshot.list_snapshots(session_id)
    except GitAgentError as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Agent lifecycle
# ---------------------------------------------------------------------------

@server.tool()
def register_agent(role: str = "", session_id: str | None = None) -> dict:
    """Register an agent. session_id required with multiple open sessions."""
    try:
        return agents.register_agent(role, session_id)
    except GitAgentError as e:
        return _err(e)


@server.tool()
def unregister_agent(agent_id: str) -> dict:
    """Mark an agent as ended."""
    try:
        agents.unregister_agent(agent_id)
        return {"ok": True}
    except GitAgentError as e:
        return _err(e)


@server.tool()
def list_agents(session_id: str | None = None) -> list[dict]:
    """List agents, optionally scoped to a session."""
    try:
        return agents.list_agents(session_id)
    except GitAgentError as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Semantic intent
# ---------------------------------------------------------------------------

@server.tool()
def start_intent(agent_id: str, intent: str) -> dict:
    """Record the start of a new intent."""
    try:
        return intents.start_intent(agent_id, intent)
    except GitAgentError as e:
        return _err(e)


@server.tool()
def repurpose(agent_id: str, intent: str) -> dict:
    """Record an intent shift."""
    try:
        return intents.repurpose(agent_id, intent)
    except GitAgentError as e:
        return _err(e)


@server.tool()
def get_current_intent(agent_id: str) -> dict | None:
    """Return the active intent for an agent."""
    try:
        return intents.get_current_intent(agent_id)
    except GitAgentError as e:
        return _err(e)


# ---------------------------------------------------------------------------
# File editing (pheromone: lock + informed rejection)
# ---------------------------------------------------------------------------

@server.tool()
def edit_file(
    agent_id: str,
    file: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> dict:
    """Exact-match string replacement. Validates against the agent's last read."""
    try:
        return edits.edit(
            agent_id, file, old_string, new_string,
            replace_all=replace_all,
        )
    except GitAgentError as e:
        return _err(e)


@server.tool()
def write_file(agent_id: str, file: str, content: str) -> dict:
    """Create or overwrite a file atomically under a per-file lock."""
    try:
        return edits.write(agent_id, file, content)
    except GitAgentError as e:
        return _err(e)


@server.tool()
def read_file(agent_id: str, file: str) -> dict:
    """Informed read: content + sha256 + base_sha + edits (with intent) + note/warning."""
    try:
        return edits.read(agent_id, file)
    except GitAgentError as e:
        return _err(e)


@server.tool()
def delete_file(agent_id: str, file: str) -> dict:
    """Remove a file under a per-file lock."""
    try:
        return edits.delete_file(agent_id, file)
    except GitAgentError as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------

@server.tool()
def list_edits(
    agent_id: str | None = None,
    file: str | None = None,
    since_ts: str | None = None,
    session_id: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """List edits with optional filters. limit caps rows (boundary picking)."""
    try:
        return edits.list_edits(
            agent_id=agent_id, file=file, since_ts=since_ts,
            session_id=session_id, limit=limit,
        )
    except GitAgentError as e:
        return _err(e)


@server.tool()
def list_intents(agent_id: str | None = None) -> list[dict]:
    """List intents."""
    try:
        from .db import get_db

        db = get_db()
        if agent_id:
            rows = db.fetchall(
                "SELECT * FROM intents WHERE agent_id = ? ORDER BY ts",
                (agent_id,),
            )
        else:
            rows = db.fetchall("SELECT * FROM intents ORDER BY ts")
        return [dict(r) for r in rows]
    except GitAgentError as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the MCP server over stdio."""
    import asyncio

    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()