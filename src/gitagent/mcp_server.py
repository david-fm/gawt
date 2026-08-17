"""MCP server entrypoint for gitagent.

Exposes all gitagent operations as MCP tools via stdio transport.
"""
from __future__ import annotations

from mcp.server import MCPServer

from . import agents, edits, inbox, intents, session
from .db import get_db
from .errors import GitAgentError

server = MCPServer("gitagent", version="0.5.1")


def _err(e: Exception) -> dict[str, str]:
    """Return machine-readable MCP tool error payload."""
    message = str(e)
    code = message.split(":", 1)[0] if ":" in message else type(e).__name__
    return {"error": code, "message": message}


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@server.tool()
def start_session(feature: str, target_branch: str = "main") -> dict:
    """Start a new session with a single global worktree."""
    try:
        r = session.start_session(feature, target_branch)
        return r
    except GitAgentError as e:
        return _err(e)


@server.tool()
def finalize_session(message: str, sign: bool = False) -> dict:
    """Commit worktree state onto the target branch."""
    try:
        sha = session.finalize_session(message, sign=sign)
        return {"final_sha": sha}
    except GitAgentError as e:
        return _err(e)


@server.tool()
def abort_session() -> dict:
    """Remove worktree and mark session aborted."""
    try:
        session.abort_session()
        return {"ok": True}
    except GitAgentError as e:
        return _err(e)


@server.tool()
def get_session() -> dict | None:
    """Return the current open session or None."""
    try:
        r = session.get_session()
        return r
    except GitAgentError as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Agent lifecycle
# ---------------------------------------------------------------------------

@server.tool()
def register_agent(role: str = "") -> dict:
    """Register a new agent. Returns {agent_id}."""
    try:
        r = agents.register_agent(role)
        return r
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
def list_agents() -> list[dict]:
    """List all agents in the current session."""
    try:
        r = agents.list_agents()
        return r
    except GitAgentError as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Semantic intent
# ---------------------------------------------------------------------------

@server.tool()
def start_intent(agent_id: str, intent: str) -> dict:
    """Record the start of a new intent."""
    try:
        r = intents.start_intent(agent_id, intent)
        return r
    except GitAgentError as e:
        return _err(e)


@server.tool()
def repurpose(agent_id: str, intent: str) -> dict:
    """Record an intent shift."""
    try:
        r = intents.repurpose(agent_id, intent)
        return r
    except GitAgentError as e:
        return _err(e)


@server.tool()
def get_current_intent(agent_id: str) -> dict | None:
    """Return the active intent for an agent."""
    try:
        r = intents.get_current_intent(agent_id)
        return r
    except GitAgentError as e:
        return _err(e)


# ---------------------------------------------------------------------------
# File editing
# ---------------------------------------------------------------------------

@server.tool()
def edit_file(
    agent_id: str,
    file: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    expected_sha256: str | None = None,
) -> dict:
    """Exact-match string replacement with atomic write.

    Args:
        file: Relative to worktree root (same as repo root, e.g. "src/auth.py").
    """
    try:
        r = edits.edit(
            agent_id, file, old_string, new_string,
            replace_all=replace_all,
            expected_sha256=expected_sha256,
        )
        return r
    except GitAgentError as e:
        return _err(e)


@server.tool()
def write_file(agent_id: str, file: str, content: str, expected_sha256: str | None = None) -> dict:
    """Create or overwrite a file with atomic write.

    Args:
        file: Relative to worktree root (same as repo root, e.g. "src/limiter.py").
    """
    try:
        r = edits.write(agent_id, file, content, expected_sha256=expected_sha256)
        return r
    except GitAgentError as e:
        return _err(e)


@server.tool()
def read_file(agent_id: str, file: str) -> dict:
    """Read a file. Returns content + sha256.

    Args:
        file: Relative to worktree root (same as repo root, e.g. "src/auth.py").
    """
    try:
        r = edits.read(agent_id, file)
        return r
    except GitAgentError as e:
        return _err(e)


@server.tool()
def delete_file(agent_id: str, file: str, expected_sha256: str | None = None) -> dict:
    """Remove a file.

    Args:
        file: Relative to worktree root (same as repo root, e.g. "src/old.py").
    """
    try:
        r = edits.delete_file(agent_id, file, expected_sha256=expected_sha256)
        return r
    except GitAgentError as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Inbox + observability
# ---------------------------------------------------------------------------

@server.tool()
def check_inbox(agent_id: str) -> list[dict]:
    """Return unread inbox items for the agent, mark them read."""
    try:
        r = inbox.check_inbox(agent_id)
        return r
    except GitAgentError as e:
        return _err(e)


@server.tool()
def send_message(from_agent_id: str, to_agent_id: str, message: str) -> dict:
    """Send a message between agents."""
    try:
        r = inbox.send_message(from_agent_id, to_agent_id, message)
        return r
    except GitAgentError as e:
        return _err(e)


@server.tool()
def list_edits(
    agent_id: str | None = None,
    file: str | None = None,
    since_ts: str | None = None,
) -> list[dict]:
    """Debug: list edits with optional filters."""
    try:
        r = edits.list_edits(agent_id=agent_id, file=file, since_ts=since_ts)
        return r
    except GitAgentError as e:
        return _err(e)


@server.tool()
def list_intents(agent_id: str | None = None) -> list[dict]:
    """Debug: list intents."""
    try:
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
