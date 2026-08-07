# Plan: gitagent v0.5.0 — MCP-first, single global worktree

> **Status:** draft for review.
> **Branch (when work starts):** `feat/mcp-sqlite-core` from `main`.
> **Release:** merge to `main` + tag `v0.5.0` after user validation.

---

## 1. Goals

- Replace the CLI/proposal/patch pipeline with an **MCP server** as the only public surface.
- Track **every edit live** in SQLite with full attribution `(agent_id, file, intent, ts)`.
- Provide **semantic intent tools** (`start_intent`, `repurpose`) so the edit log carries meaning.
- Provide an **inbox + conflict notification system** so multiple agents in the same worktree can coordinate.
- Use a **single global worktree** at a time. Parallelism lives in the agents, not in worktrees.
- Drop `propose`, `spawn` (as worktree creator), and `integrate`. `finalize_session` commits the worktree state directly.

## 2. Non-goals (v0.5.0)

- Multi-worktree / multi-feature parallel sessions (features are serialized).
- File locks (best-effort + inbox notifications only).
- Detection of writes made through host-side `Edit`/`Write` tools (prompt enforcement only).
- HTTP / SSE MCP transport (stdio only for v0.5.0).
- Backward compatibility with v0.4.2.

---

## 3. Architecture

```
┌─ Host (Claude Code / opencode / ...) ─┐
│  spawns: gitagent mcp (stdio)         │
└──────────────────┬────────────────────┘
                   │ MCP stdio
┌──────────────────▼────────────────────┐
│ gitagent mcp server (FastMCP)         │
└──────────────────┬────────────────────┘
                   │
        ┌──────────┴──────────┐
        ▼                     ▼
  .gitagent/state.db    .gitagent/worktree/   ← single, global
  (sqlite)              (detached worktree)
```

- One detached worktree under `.gitagent/worktree/` is active at any moment.
- Multiple agents share that worktree and coordinate through the SQLite inbox.
- The user's checkout on `main` is never touched. `finalize_session` produces a single commit on the target branch via a detached temp worktree + `git update-ref`, exactly as v0.4.2 did.

---

## 4. Lifecycle (global state machine)

```
state: no_worktree
  ↓ start_session(feature="x", target_branch="main")
state: open (one worktree active)
  ├─ register_agent(role)               # called multiple times
  ├─ agents use edit / write / read / check_inbox / start_intent / repurpose
  ├─ start_session(feature="y") → error if previous session is not finalized/aborted
  ↓ finalize_session(message)
state: finalized (commit on target_branch, worktree removed)
  ↓ start_session(feature="z")
state: open
  ...
```

Only one session is `open` at a time. Features are serialized. Parallelism is achieved by spawning many agents in the same session.

---

## 5. SQLite schema

```sql
PRAGMA user_version = 1;

CREATE TABLE session (
  id TEXT PRIMARY KEY,           -- 's_<hex>' (effectively a singleton)
  feature TEXT NOT NULL,         -- logical label, e.g. 'auth-rl'
  target_branch TEXT NOT NULL,   -- default 'main'
  base_sha TEXT NOT NULL,
  worktree TEXT NOT NULL,
  state TEXT,                    -- 'open' | 'finalized' | 'aborted'
  created_at TEXT,
  ended_at TEXT,
  final_sha TEXT                 -- commit SHA on target_branch post-finalize
);

CREATE TABLE agents (
  id TEXT PRIMARY KEY,           -- 'a_<hex>'
  session_id TEXT NOT NULL,
  role TEXT,
  started_at TEXT,
  ended_at TEXT
);

CREATE TABLE intents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT NOT NULL,
  kind TEXT,                     -- 'start' | 'repurpose'
  intent TEXT,
  ts TEXT
);

CREATE TABLE edits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  file TEXT NOT NULL,            -- relative to worktree root
  op TEXT,                       -- 'edit' | 'write' | 'delete'
  old_string TEXT,               -- op = 'edit'
  new_string TEXT,               -- op = 'edit'
  full_content TEXT,             -- op = 'write'
  intent_id INTEGER,
  ts TEXT
);

CREATE TABLE inbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  to_agent TEXT NOT NULL,
  from_agent TEXT,
  kind TEXT,                     -- 'conflict' | 'manual' | 'system'
  payload TEXT,                  -- JSON
  ts TEXT,
  read INTEGER DEFAULT 0
);

CREATE INDEX idx_edits_file ON edits(session_id, file, ts);
CREATE INDEX idx_inbox_to   ON inbox(to_agent, read, ts);
```

The singleton invariant is enforced in code: queries against `session WHERE state = 'open'` are expected to return 0 or 1 row. `start_session` fails loudly if a row already exists in state `open`.

---

## 6. MCP tools catalog

All tools except lifecycle require `agent_id` explicitly per call.

### Lifecycle (orchestrator)

| Tool | Effect |
|---|---|
| `start_session(feature, target_branch="main")` | Fails if a session is already `open`. Creates a detached worktree from `target_branch` HEAD. Inserts a `session` row. Returns `{session_id, worktree, base_sha}`. |
| `finalize_session(message, sign=False)` | Requires an `open` session. `git add -A` + commit on the worktree. Creates a detached temp worktree on `target_branch`, squashes the worktree commit, creates the final commit, `git update-ref` to advance the target. Removes the worktree, prunes. Marks session `finalized`. Returns `{final_sha}`. |
| `abort_session()` | Removes the worktree, marks session `aborted`. |
| `get_session()` | Returns the current `open` session or `null`. |

### Agent lifecycle

| Tool | Effect |
|---|---|
| `register_agent(role)` | Auto-assigns `a_<hex>`. Inserts `agents` row. Returns `{agent_id}`. |
| `unregister_agent(agent_id)` | Sets `ended_at`. |
| `list_agents()` | Lists agents of the current open session. |

### Semantic intent

| Tool | Effect |
|---|---|
| `start_intent(agent_id, intent)` | Inserts `intents` row `kind=start`. Returns `{intent_id}`. |
| `repurpose(agent_id, intent)` | Inserts `intents` row `kind=repurpose`. Updates the current-intent pointer for that agent. |
| `get_current_intent(agent_id)` | Returns the active `{intent_id, intent}`. |

### File editing (replicates Claude Code Edit + Write)

| Tool | Effect |
|---|---|
| `edit(agent_id, file, old_string, new_string, replace_all=False)` | Exact match + replace. Atomic write via temp + rename. Records an `edits` row. Runs conflict detection. |
| `write(agent_id, file, content)` | Create / overwrite. Atomic write via temp + rename. Records an `edits` row. Runs conflict detection. |
| `read(agent_id, file)` | Read file (no tracking). Returns content + sha256. |
| `delete_file(agent_id, file)` | Removes file. Records `edits` row with `op='delete'`. |

### Inbox + observability

| Tool | Effect |
|---|---|
| `check_inbox(agent_id)` | Returns unread rows for the agent, marks them read. |
| `send_message(from_agent_id, to_agent_id, message)` | Inserts an `inbox` row `kind='manual'`. |
| `list_edits(agent_id=None, file=None, since_ts=None)` | Debug view. |
| `list_intents(agent_id=None)` | Debug view. |

### Validation rules applied on every tool call

- `agent_id` is registered in the current open session.
- `agent_id.ended_at IS NULL`.
- `session.state == 'open'`.
- File paths are inside the worktree (no `..` escape).
- Failures return a clear error string the agent can act on.

---

## 7. Agent id flow

The identity problem from MCP is solved by requiring the agent to pass its own id:

```
register_agent(role="implement limiter")
  → {"agent_id": "a_3f2c"}

# Agent stores a_3f2c and passes it to every subsequent call
start_intent(agent_id="a_3f2c", intent="...")
edit(agent_id="a_3f2c", file="src/auth.py", old_string="...", new_string="...")
check_inbox(agent_id="a_3f2c")
```

No cwd inference, no environment variable. Harness-agnostic by construction.

---

## 8. Concurrency model (best-effort + inbox)

No file locks. On `edit` / `write`:

1. Look up the `edits` table: did another agent edit this `file` within the last `N` seconds (default 30)?
2. If yes, insert `inbox` rows:
   - `to = other_agent`, `kind = 'conflict'`, payload = `{file, your_edit_ts, conflicting_agent}`.
   - `to = self`, `kind = 'conflict'`, payload = `{file, their_edit_ts, conflicting_agent}`.
3. Atomic write (temp + rename).
4. Insert `edits` row.

Agents poll `check_inbox` after significant edits and decide how to react: re-read and retry, abort their intent, or push through. Conflicts are advisory, never blocking.

---

## 9. Atomicity of writes

For every edit / write:

```
1. Compute final content (in memory).
2. Write to <file>.tmp.<pid> in the same directory (same filesystem).
3. fsync (optional, configurable).
4. os.replace(tmp, file)   # POSIX-atomic on the same filesystem.
5. Record the edits row.
```

- Crash between 1–3: no effect on the real file.
- Crash between 4–5: effect on the file but the row is missing. Recoverable at `finalize_session` via `git status` against the worktree.
- The file is never observed in a half-written state.

---

## 10. Stale read handling

`edit(agent_id, file, old_string, new_string, replace_all=False)`:

- Read the current file.
- If `old_string` not found → error `old_string_not_found`. Suggest `read(file)` + retry.
- If `replace_all=False` and `old_string` matches more than once → error `ambiguous_match`. Suggest `replace_all=True` or more context.
- If `replace_all=True` → replace all occurrences.

No mtime-based proactive rejection. Agents are responsible for re-reading after a conflict notification.

---

## 11. Finalize flow

```
finalize_session(message="feat: rate limit")
  1. require session.state == 'open'
  2. warn (do not block) if any agents have ended_at IS NULL
  3. git -C <worktree> add -A
  4. git -C <worktree> commit -m <message> [--author ...]
  5. detached temp worktree on <target_branch>
  6. squash / cherry-pick the worktree commit
  7. commit on temp worktree with the final message
  8. git update-ref <target_branch> <new_sha>
  9. git worktree remove <worktree>; prune
 10. session.state = 'finalized'; final_sha = new_sha
 11. append to .gitagent/log.jsonl (legacy audit log)
```

Reuses the git plumbing patterns from v0.4.2 (`gitwrap.py`).

---

## 12. Skill (`SKILL.md`) content

The bundled skill is rewritten to teach the new workflow:

```markdown
# gitagent (v0.5.0)

You are a subagent inside a gitagent session.

## Hard rule
**Use `gitagent_mcp__edit` and `gitagent_mcp__write` for ALL file changes.**
Do NOT use the host's Edit/Write tools. Host tools bypass attribution and
conflict tracking. If you use them, your changes are invisible to your peers
and to the orchestrator.

## Workflow
1. `register_agent(role="...")` → store the returned `agent_id`.
2. `start_intent(agent_id, intent="...")` before your first edit.
3. Use `edit` / `write` / `read` / `delete_file` for changes.
4. `repurpose(agent_id, intent)` whenever your focus shifts.
5. `check_inbox(agent_id)` after each significant change.
6. `send_message` to coordinate with peers explicitly.
7. When done, tell the orchestrator. Do NOT call `finalize_session` yourself.

## Conflict protocol
If `check_inbox` returns a `conflict` message:
- `read` the conflicting file.
- Re-plan your edit with the new content.
- Retry via `edit` / `write`.
```

---

## 13. File structure

```
src/gitagent/
  mcp_server.py      # FastMCP entrypoint + tool registration
  db.py              # sqlite wrapper, migrations (PRAGMA user_version)
  session.py         # start_session, finalize_session, abort_session, get_session
  agents.py          # register_agent, unregister_agent, list_agents
  intents.py         # start_intent, repurpose, get_current_intent
  edits.py           # edit, write, read, delete_file + atomic write + conflict detection
  inbox.py           # check_inbox, send_message
  gitwrap.py         # unchanged (reused)
  (delete) cli.py, store.py, proposals.py, review.py, finalize.py
```

New dependency: `mcp` (or `fastmcp`).

---

## 14. Backward compatibility / migration

- v0.5.0 is a total break. No compat with v0.4.2.
- `feat/mcp-sqlite-core` stays isolated until the user validates end-to-end.
- The merge to `main` deletes the deprecated files (`cli.py`, `store.py`, `proposals.py`, `review.py`, `finalize.py`) in a single clean commit.
- `CHANGELOG.md` gains an `Unreleased` v0.5.0 section explaining the break.
- `README.md` is rewritten for the MCP-only workflow.
- The legacy `.gitagent/log.jsonl` audit log is preserved as a finalization artifact (append-only summary of sessions and conflicts).

---

## 15. Open issues (resolvable during implementation)

1. **`finalize_session` with live agents**: warn vs. block. Default: warn, do not block.
2. **`start_session` while one is `open`**: clear error explaining `finalize_session` / `abort_session` is required first.
3. **Agent from a previous session acting in a new one**: error (`agent not in current session`); the agent must `register_agent` again.
4. **MCP server starts before `.gitagent/` exists**: lazy init on the first tool call that touches the DB.
5. **`start_intent` required before `edit`**: enforcement vs. warn. Default: warn (stderr + inbox entry), do not block.
6. **Filesystem watcher for out-of-MCP writes**: skip in v0.5.0. Consider for v0.6.0 once prompt-only enforcement is validated.
7. **Conflict window default (N seconds)**: default 30. Make it configurable per session (`start_session(..., conflict_window_seconds=30)`).

---

## 16. Implementation steps (when leaving plan mode)

1. `git checkout -b feat/mcp-sqlite-core`
2. Create `db.py` with schema + migrations.
3. Create `session.py` (`start_session`, `finalize_session`, `abort_session`, `get_session`).
4. Create `agents.py` (`register_agent`, `unregister_agent`, `list_agents`).
5. Create `intents.py` (`start_intent`, `repurpose`, `get_current_intent`).
6. Create `edits.py` (atomic write, conflict detection, `edit`/`write`/`read`/`delete_file`).
7. Create `inbox.py` (`check_inbox`, `send_message`).
8. Create `mcp_server.py` (FastMCP entrypoint; registers all tools).
9. Rewrite `SKILL.md` with the v0.5.0 workflow.
10. Tests:
    - `tests/test_db.py` — schema, migrations, indexes.
    - `tests/test_session.py` — single-worktree invariant, finalize commit on target.
    - `tests/test_agents.py` — auto-id, ended_at, validation.
    - `tests/test_intents.py` — start/repurpose ordering, current pointer.
    - `tests/test_edits.py` — atomic write, old_string match, conflict detection.
    - `tests/test_inbox.py` — unread/read transitions, conflict payload.
    - `tests/test_mcp_server.py` — in-process MCP tool calls end-to-end.
11. Manual E2E: spawn 3 agents, edit overlapping files, verify inbox notifications, `finalize_session`, verify single commit on `main`.
12. Update `README.md` (MCP-only workflow) and `CHANGELOG.md` (Unreleased v0.5.0 section).