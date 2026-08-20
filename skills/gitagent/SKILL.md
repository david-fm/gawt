---
name: gitagent
description: 'Use this skill when you (or another agent you supervise) need to coordinate multi-agent coding work over Git via MCP — shared worktree, pheromone edit tracking with semantic intents, per-file write locks with informed rejection, partial snapshots per orchestrator. Triggers on "gitagent", "multi-agent git workflow", "coordinate agents", "shared worktree". Do NOT use for: general git questions, single-developer workflows, or non-agent coordination tasks.'
---

# gitagent (v0.6.0)

MCP-first agent workspace manager. **One shared worktree**, live edit tracking in SQLite (the **pheromone**), semantic intents, per-file write locks with informed rejection, and **partial snapshots** per orchestrator. No inbox — coordination emerges from the edit log. No CLI — all operations via MCP tools over stdio.

---

## Architecture

```
Host (Claude Code / opencode)
  → spawns: gitagent mcp (stdio)
    → .gitagent/state.db (sqlite)
    → .gitagent/worktree/  ← ONE shared detached worktree for ALL sessions
```

- **Multiple sessions open at once**, each with its own agents.
- **One worktree per repo**, reused across sessions. Snapshots never delete it.
- **Target branch is fixed per worktree.** The first session that creates the worktree picks it; later sessions share it (a new `target_branch` is ignored).

---

## File paths — IMPORTANT

All file paths (`file` in `edit_file`, `write_file`, `read_file`, `delete_file`) are **relative to the worktree root** (`.gitagent/worktree/`). Since the worktree is a detached copy of the main branch, these paths are effectively the same as relative to the main repo root.

```
edit_file(agent_id="a_1", file="src/auth.py", ...)
read_file(agent_id="a_1", file="tests/test_auth.py", ...)

#   .gitagent/worktree/src/auth.py        ← file="src/auth.py"
#   .gitagent/worktree/tests/test_auth.py ← file="tests/test_auth.py"
```

- Use `src/auth.py`, not `.gitagent/worktree/src/auth.py`.
- No `..` escapes allowed (path must stay inside the worktree).

---

## Hard rule

**Use `gitagent_mcp__edit_file` and `gitagent_mcp__write_file` for ALL file changes.**
Do NOT use the host's Edit/Write tools — they bypass attribution, the pheromone, and the lock protocol.

---

## Workflow

### 0. Orchestrator: start a session

```
start_session(feature="rate limiter", target_branch="main")
```

The first session on the repo creates `.gitagent/worktree/` on the current HEAD. Subsequent `start_session` calls reuse it — pass a distinct `feature` so sessions are independent.

### 1. Register and route agents

```
register_agent(role="implement limiter", session_id="s_...")
→ store {agent_id, session_id}
```

With **two or more open sessions**, `session_id` is **required** (hard error otherwise). Pass `agent_id` on every subsequent call.

### 2. Orchestrator: pick a per-session frontier

Inspect the shared worktree state to decide which files are yours:

```
snapshot_status(session_id="s_...")
list_edits(limit=50)
read_file(agent_id, file)
```

`snapshot_status` returns **all changed files in the worktree vs the target**, with per-file diff, edit history (pheromone), and snapshot progress.

### 3. Agents set intent before editing

```
start_intent(agent_id="a_3f2c", intent="implement rate limiter in auth middleware")
```

Update intent when focus shifts:

```
repurpose(agent_id="a_3f2c", intent="write tests for rate limiter")
```

### 4. Agents edit files (lock + informed rejection)

```
edit_file(agent_id="a_3f2c", file="src/auth.py", old_string="...", new_string="...")
write_file(agent_id="a_3f2c", file="src/limiter.py", content="...")
read_file(agent_id="a_3f2c", file="src/auth.py")
delete_file(agent_id="a_3f2c", file="src/old.py")
```

Every write acquires the **per-file lock** at the start and releases it in a `finally` (never leaks). If another agent holds a fresh lock, the write is **rejected — never applied** and the response includes the full `read` payload so you can re-plan informed.

### 5. Orchestrator: partial snapshot

Publish the part of the worktree that is yours:

```
snapshot_session(session_id="s_...", message="auth middleware done", files=["src/auth.py"])
```

- Commit lands on the worktree's **target branch** (via a temp worktree).
- The **live worktree is NOT deleted** — other sessions keep working.
- Files **not in `files`** stay pending for your next snapshot (a per-file `snapshot_progress` frontier tracks this per session).
- `boundary_edit_id=N` publishes the state reconstructed up to edit N via replay.

### 6. Orchestrator ends the session

When all its agents finish, the orchestrator aborts its session:

```
abort_session(session_id="s_...")
```

The worktree is only removed when the **last** open session aborts.

---

## Conflict protocol

Two agents competing for the same file: one wins the lock, the other gets a `{status: "rejected"}` response whose `read` shows the current content. The loser re-plans and retries. There is no queuing and no waiting inside the server — conflict resolution lives in the agents.

If you see a rejected write:

1. Read the `read` payload (or call `read_file`) to see the current state.
2. `repurpose` your intent if it changed.
3. Retry `edit_file` / `write_file` against the new content.

---

## Recoveries

- **Crash mid-write**: disk is the source of truth. `snapshot_status` detects disk changes with no matching pheromone row and inserts a synthetic `{op: 'adjusted'}` attribution so replay never fails.
- **STALE_WRITE**: if a write's `expected_sha256` no longer matches after acquiring the lock, the write is rejected with `reason: "STALE_WRITE"` — re-read and retry.
- **Orphaned lock**: a lock older than `lock_ttl_seconds` (default 15s) is reclaimed as crash residue.

---

## Tool reference

### Session lifecycle (orchestrator only)

| Tool | Effect |
|---|---|
| `start_session(feature, target_branch="main", lock_ttl_seconds=15)` | Creates/reuses the shared worktree + session. |
| `abort_session(session_id)` | Marks aborted; removes worktree only if the last open session. |
| `get_session(session_id=None)` | Returns a session by id (or the single open one when None). |
| `list_sessions()` | Lists all sessions. |

### Snapshots

| Tool | Effect |
|---|---|
| `snapshot_status(session_id)` | Whole-worktree diff vs target + edit log + progress. |
| `snapshot_session(session_id, message, files=None, boundary_edit_id=None, sign=False)` | Partial commit onto the target branch. Live worktree stays. |
| `list_snapshots(session_id=None)` | Lists recorded snapshots. |

### Agent lifecycle

| Tool | Effect |
|---|---|
| `register_agent(role="", session_id=None)` | Returns `{agent_id, session_id}`. session_id required with 2+ open. |
| `unregister_agent(agent_id)` | Marks agent ended. |
| `list_agents(session_id=None)` | Lists agents (optionally per session). |

### Semantic intent

| Tool | Effect |
|---|---|
| `start_intent(agent_id, intent)` | Records intent start. Returns `{intent_id}`. |
| `repurpose(agent_id, intent)` | Records intent shift. |
| `get_current_intent(agent_id)` | Returns active intent. |

### File editing

| Tool | Effect |
|---|---|
| `edit_file(agent_id, file, old_string, new_string, replace_all=False, expected_sha256=None)` | Exact match + atomic write. |
| `write_file(agent_id, file, content, expected_sha256=None)` | Create / overwrite. Atomic write. |
| `read_file(agent_id, file)` | Informed read: `{content, sha256, path, base_sha, diff, edits[], warning}`. |
| `delete_file(agent_id, file, expected_sha256=None)` | Removes file. |

### Observability

| Tool | Effect |
|---|---|
| `list_edits(agent_id=None, file=None, since_ts=None, session_id=None, limit=None)` | Edit log with filters and a row cap for boundary picking. |
| `list_intents(agent_id=None)` | Intent log. |

---

## Validation rules

Every tool call validates:
- `agent_id` is registered and currently active (`ended_at IS NULL`).
- The session (by `session_id` or single open) is `open`.
- File paths are inside the worktree (no `..` escape).

Failures return a clear error string the agent can act on — or a structured `{status: "rejected"}` for write conflicts.

---

## On-disk layout

```
<repo>/.gitagent/
├── state.db              # sqlite: sessions, agents, intents, edits, snapshot_progress, locks, snapshots
├── worktree/             # ONE shared detached worktree (all open sessions)
├── _snapshot_temp/       # temp worktree during a snapshot commit (auto-cleaned)
└── log.jsonl             # append-only audit trail
```