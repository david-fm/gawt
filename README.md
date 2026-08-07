# gitagent

> MCP-first agent workspace manager: single worktree, live edit tracking, semantic intents, inbox coordination.

`gitagent` v0.5.0 is a total rewrite. The CLI is gone — all operations are exposed as **MCP tools** over stdio. A single global worktree hosts multiple agents simultaneously. Every edit is tracked in SQLite with full attribution. Agents coordinate via an inbox with best-effort conflict notifications.

## Install

```bash
# from GitHub
pip install "git+https://github.com/david-fm/gawt"

# local development
git clone https://github.com/david-fm/gawt && cd gawt
pip install -e ".[dev]"
```

Requires Python 3.11+ and a working `git` on `PATH`.

## Quick start

```python
# Start a session
start_session(feature="auth-rate-limiting")

# Register agents
register_agent(role="implement limiter")   # → {"agent_id": "a_3f2c"}
register_agent(role="write tests")         # → {"agent_id": "a_7b1e"}

# Agents set intent and edit files
start_intent(agent_id="a_3f2c", intent="implement rate limiter")
edit_file(agent_id="a_3f2c", file="src/auth.py", old_string="...", new_string="...")

# Check for conflicts
check_inbox(agent_id="a_3f2c")

# Finalize (orchestrator only)
finalize_session(message="feat(auth): rate limiting")
# → 1 commit on main, worktree removed
```

## Architecture

```
Host (Claude Code / opencode)
  → spawns: gitagent mcp (stdio)
    → .gitagent/state.db (sqlite)
    → .gitagent/worktree/ (single detached worktree)
```

- One worktree active at a time. Features are serialized.
- Multiple agents share the worktree and coordinate via SQLite inbox.
- User's checkout on `main` is never touched.

## MCP Tools

| Category | Tools |
|---|---|
| Lifecycle | `start_session`, `finalize_session`, `abort_session`, `get_session` |
| Agents | `register_agent`, `unregister_agent`, `list_agents` |
| Intent | `start_intent`, `repurpose`, `get_current_intent` |
| Editing | `edit_file`, `write_file`, `read_file`, `delete_file` |
| Inbox | `check_inbox`, `send_message`, `list_edits`, `list_intents` |

All tools except lifecycle require `agent_id` explicitly per call.

## How it works

- **Single worktree**: detached worktree at `.gitagent/worktree/`. All agents edit the same files.
- **Live tracking**: every `edit_file`/`write_file` records `(agent_id, file, intent, ts)` in SQLite.
- **Atomic writes**: all writes go through temp + `os.replace` (POSIX-atomic). Files are never half-written.
- **Best-effort conflicts**: if two agents edit the same file within 30s, both get an inbox notification. No locks, no blocking.
- **Semantic intents**: `start_intent`/`repurpose` annotate the edit log with *why* changes are made.
- **Finalize**: `finalize_session` commits the worktree state onto the target branch via detached temp worktree + `git update-ref`. Single commit, no push.

## Layout

```
<repo>/.gitagent/
├── state.db              # sqlite: sessions, agents, intents, edits, inbox
├── worktree/             # single detached worktree (active session)
├── _finalize_temp/       # temp worktree during finalize (auto-cleaned)
└── log.jsonl             # append-only audit trail
```

## Development

```bash
pip install -e ".[dev]"
PYTHONPATH=src pytest tests/ -v
```

## License

MIT © David Florez Mazuera
