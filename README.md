# gitagent

> MCP-first agent workspace manager: shared worktree, pheromone edit tracking, semantic intents, per-file locks, partial snapshots.

`gitagent` v0.6.2 is a total rewrite. The CLI is gone — all operations are exposed as **MCP tools** over stdio. Multiple sessions share ONE global worktree. Every edit is tracked in SQLite with full attribution (the **pheromone**). Coordination — no inbox — emerges from the edit log. Writes acquire per-file locks and **reject informed** (auto-STALE via last-read tracking) on conflict; orchestrators publish their part via **partial snapshots**.

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
# Orchestrator: start a session (creates/reuses the shared worktree)
sess_a = start_session(feature="auth-rate-limiting")
sess_b = start_session(feature="tests-for-limiter")   # same worktree, 2nd session

# Register + route agents (session_id required with 2+ open sessions)
register_agent(role="implement limiter", session_id=sess_a["session_id"])
register_agent(role="write tests", session_id=sess_b["session_id"])

# Agents set intent and edit files (pheromone + lock)
start_intent(agent_id="a_3f2c", intent="implement rate limiter")
edit_file(agent_id="a_3f2c", file="src/auth.py", old_string="...", new_string="...")

# Orchestrator: inspect shared state, pick a frontier
snapshot_status(session_id=sess_a["session_id"])      # whole worktree vs target
list_edits(limit=50)

# Orchestrator: publish only its part (live worktree stays)
snapshot_session(session_id=sess_a["session_id"], message="feat(auth): rate limiter",
                 files=["src/auth.py"])
# → 1 commit on target branch; other sessions keep the worktree

# Orchestrator ends the session
abort_session(session_id=sess_a["session_id"])        # removes worktree only if last open
```

## Architecture

```
Host (Claude Code / opencode)
  → spawns: gitagent mcp (stdio)
    → .gitagent/state.db (sqlite)
    → .gitagent/worktree/ (ONE shared detached worktree)
```

- Multiple sessions open at once, each with its own agents.
- One worktree per repo, reused across sessions. Snapshots never delete it.
- Target branch fixed per worktree (the first session picks it).
- User's checkout on `main` is never touched.

## MCP Tools

| Category | Tools |
|---|---|
| Sessions | `start_session`, `abort_session`, `get_session`, `list_sessions` |
| Snapshots | `snapshot_session`, `snapshot_status`, `list_snapshots` |
| Agents | `register_agent`, `unregister_agent`, `list_agents` |
| Intent | `start_intent`, `repurpose`, `get_current_intent` |
| Editing | `edit_file`, `write_file`, `read_file`, `delete_file` |
| Observability | `list_edits`, `list_intents` |

All editing/intent tools require `agent_id` per call.

## How it works

- **Shared worktree**: one detached worktree at `.gitagent/worktree/`, shared by all sessions and their agents.
- **Pheromone**: every `edit_file` / `write_file` / `delete_file` records `(agent_id, session_id, file, intent_id, op, ts)` in SQLite — a traceable "I edited this, with this intent".
- **Per-file locks**: writes acquire a lock first and always release it in `finally` (TTL default 15s reclaims orphans). A fresh foreign lock → informed `rejected` response with the current `read`.
- **Informed reads**: `read_file` returns content + sha256 + `base_sha` + `edits[]` (each with `op`, `role`, `intent`, `ts`) + intent `warning` — no fat diff payload. The git diff lives in `snapshot_status`.
- **Auto STALE_WRITE**: gawt remembers each agent's last read per file (`last_reads`) and rejects a write with `STALE_WRITE` if the disk changed since, or if you never read the file (you must read before touching a file someone else owns). Agents never pass or manage a SHA.
- **Atomic writes**: all writes go through temp + `os.replace` (POSIX-atomic); disk is the source of truth.
- **Partial snapshots**: `snapshot_session` commits *part* of the worktree onto the target branch via a detached temp worktree, without touching the live worktree. Per-file `snapshot_progress` tracks each session's frontier.
- **Crash reconciliation**: `snapshot_status` inserts synthetic `adjusted` rows for disk changes with no pheromone entry, so `replay` never fails on crash residue.

## Layout

```
<repo>/.gitagent/
├── state.db              # sqlite: sessions, agents, intents, edits, snapshot_progress, locks, snapshots
├── worktree/             # ONE shared detached worktree (all open sessions)
├── _snapshot_temp/       # temp worktree during a snapshot commit (auto-cleaned)
└── log.jsonl             # append-only audit trail
```

## Development

```bash
pip install -e ".[dev]"
PYTHONPATH=src pytest tests/ -v
```

## License

MIT © David Florez Mazuera