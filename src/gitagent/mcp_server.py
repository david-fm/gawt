"""MCP server entrypoint for gitagent v0.5.0.

Exposes all gitagent operations as MCP tools via stdio transport.
"""
from __future__ import annotations

from mcp.server import MCPServer

from . import agents, edits, inbox, intents, session
from .db import get_db, reset_db
from .errors import GitAgentError

server = MCPServer("gitagent", version="0.5.0")


def _err(e: Exception) -> str:
    return f"error: {e}"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@server.tool()
def start_session(feature: str, target_branch: str = "main") -> str:
    """Start a new session with a single global worktree."""
    try:
        r = session.start_session(feature, target_branch)
        return str(r)
    except GitAgentError as e:
        return _err(e)


@server.tool()
def finalize_session(message: str, sign: bool = False) -> str:
    """Commit worktree state onto the target branch."""
    try:
        sha = session.finalize_session(message, sign=sign)
        return str({"final_sha": sha})
    except GitAgentError as e:
        return _err(e)


@server.tool()
def abort_session() -> str:
    """Remove worktree and mark session aborted."""
    try:
        session.abort_session()
        return str({"ok": True})
    except GitAgentError as e:
        return _err(e)


@server.tool()
def get_session() -> str:
    """Return the current open session or None."""
    try:
        r = session.get_session()
        return str(r)
    except GitAgentError as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Agent lifecycle
# ---------------------------------------------------------------------------

@server.tool()
def register_agent(role: str = "") -> str:
    """Register a new agent. Returns {agent_id}."""
    try:
        r = agents.register_agent(role)
        return str(r)
    except GitAgentError as e:
        return _err(e)


@server.tool()
def unregister_agent(agent_id: str) -> str:
    """Mark an agent as ended."""
    try:
        agents.unregister_agent(agent_id)
        return str({"ok": True})
    except GitAgentError as e:
        return _err(e)


@server.tool()
def list_agents() -> str:
    """List all agents in the current session."""
    try:
        r = agents.list_agents()
        return str(r)
    except GitAgentError as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Semantic intent
# ---------------------------------------------------------------------------

@server.tool()
def start_intent(agent_id: str, intent: str) -> str:
    """Record the start of a new intent."""
    try:
        r = intents.start_intent(agent_id, intent)
        return str(r)
    except GitAgentError as e:
        return _err(e)


@server.tool()
def repurpose(agent_id: str, intent: str) -> str:
    """Record an intent shift."""
    try:
        r = intents.repurpose(agent_id, intent)
        return str(r)
    except GitAgentError as e:
        return _err(e)


@server.tool()
def get_current_intent(agent_id: str) -> str:
    """Return the active intent for an agent."""
    try:
        r = intents.get_current_intent(agent_id)
        return str(r)
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
) -> str:
    """Exact-match string replacement with atomic write.

    Args:
        file: Relative to worktree root (same as repo root, e.g. "src/auth.py").
    """
    try:
        r = edits.edit(agent_id, file, old_string, new_string, replace_all=replace_all)
        return str(r)
    except GitAgentError as e:
        return _err(e)


@server.tool()
def write_file(agent_id: str, file: str, content: str) -> str:
    """Create or overwrite a file with atomic write.

    Args:
        file: Relative to worktree root (same as repo root, e.g. "src/limiter.py").
    """
    try:
        r = edits.write(agent_id, file, content)
        return str(r)
    except GitAgentError as e:
        return _err(e)


@server.tool()
def read_file(agent_id: str, file: str) -> str:
    """Read a file. Returns content + sha256.

    Args:
        file: Relative to worktree root (same as repo root, e.g. "src/auth.py").
    """
    try:
        r = edits.read(agent_id, file)
        return str(r)
    except GitAgentError as e:
        return _err(e)


@server.tool()
def delete_file(agent_id: str, file: str) -> str:
    """Remove a file.

    Args:
        file: Relative to worktree root (same as repo root, e.g. "src/old.py").
    """
    try:
        r = edits.delete_file(agent_id, file)
        return str(r)
    except GitAgentError as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Inbox + observability
# ---------------------------------------------------------------------------

@server.tool()
def check_inbox(agent_id: str) -> str:
    """Return unread inbox items for the agent, mark them read."""
    try:
        r = inbox.check_inbox(agent_id)
        return str(r)
    except GitAgentError as e:
        return _err(e)


@server.tool()
def send_message(from_agent_id: str, to_agent_id: str, message: str) -> str:
    """Send a message between agents."""
    try:
        r = inbox.send_message(from_agent_id, to_agent_id, message)
        return str(r)
    except GitAgentError as e:
        return _err(e)


@server.tool()
def list_edits(
    agent_id: str | None = None,
    file: str | None = None,
    since_ts: str | None = None,
) -> str:
    """Debug: list edits with optional filters."""
    try:
        r = edits.list_edits(agent_id=agent_id, file=file, since_ts=since_ts)
        return str(r)
    except GitAgentError as e:
        return _err(e)


@server.tool()
def list_intents(agent_id: str | None = None) -> str:
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
        return str([dict(r) for r in rows])
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
