---
name: gitagent
description: 'Use this skill when you (or another agent you supervise) need to coordinate multi-agent coding work over Git via MCP — single shared worktree, live edit tracking with semantic intents, inbox coordination between agents. Triggers on "gitagent", "multi-agent git workflow", "coordinate agents", "shared worktree". Do NOT use for: general git questions, single-developer workflows, or non-agent coordination tasks.'
---

# gitagent (v0.5.0)

MCP-first agent workspace manager. Single global worktree, live edit tracking in SQLite, semantic intents, inbox for inter-agent coordination. No CLI — all operations via MCP tools.

---

## Architecture

```
Host (Claude Code / opencode)
  → spawns: gitagent mcp (stdio)
    → .gitagent/state.db (sqlite)
    → .gitagent/worktree/ (single detached worktree)
```

One worktree active at a time. Features are serialized. Parallelism lives in multiple agents sharing the same worktree.

---

## File paths — IMPORTANT

All file paths (`file` parameter in `edit_file`, `write_file`, `read_file`, `delete_file`) are **relative to the worktree root** (`.gitagent/worktree/`). Since the worktree is a detached copy of the main branch, these paths are **effectively the same as relative to the main repo root**.

```
# These two are equivalent:
edit_file(agent_id="a_1", file="src/auth.py", ...)
read_file(agent_id="a_1", file="tests/test_auth.py", ...)

# The worktree at .gitagent/worktree/ mirrors the repo structure:
#   .gitagent/worktree/src/auth.py        ← file="src/auth.py"
#   .gitagent/worktree/tests/test_auth.py ← file="tests/test_auth.py"
```

- Use `src/auth.py`, not `.gitagent/worktree/src/auth.py`.
- No `..` escapes allowed (path must stay inside the worktree).
- Think of paths as if you were at the repo root — because the worktree has the same structure.

---

## Hard rule

**Use `gitagent_mcp__edit_file` and `gitagent_mcp__write_file` for ALL file changes.**
Do NOT use the host's Edit/Write tools. Host tools bypass attribution and conflict tracking. If you use them, your changes are invisible to your peers and to the orchestrator.

---

## Workflow

### 1. Register

```
register_agent(role="implement limiter")
→ store the returned agent_id (e.g. "a_3f2c")
```

You must pass `agent_id` on every subsequent call.

### 2. Set intent before editing

```
start_intent(agent_id="a_3f2c", intent="implement rate limiter in auth middleware")
```

Update intent when focus shifts:

```
repurpose(agent_id="a_3f2c", intent="write tests for rate limiter")
```

### 3. Edit files

```
edit_file(agent_id="a_3f2c", file="src/auth.py", old_string="...", new_string="...")
write_file(agent_id="a_3f2c", file="src/limiter.py", content="...")
read_file(agent_id="a_3f2c", file="src/auth.py")
delete_file(agent_id="a_3f2c", file="src/old.py")
```

All writes are atomic (temp + rename). All edits are tracked with attribution.

### 4. Coordinate with peers

After significant edits, check for conflicts:

```
check_inbox(agent_id="a_3f2c")
```

Send messages to peers:

```
send_message(from_agent_id="a_3f2c", to_agent_id="a_7b1e", message="I changed auth.py, you may need to rebase")
```

### 5. Finish

Tell the orchestrator you're done. Do NOT call `finalize_session` yourself — only the orchestrator does that.

---

## Conflict protocol

If `check_inbox` returns a `conflict` message:

1. `read_file` the conflicting file to see current state.
2. Re-plan your edit with the new content.
3. Retry via `edit_file` / `write_file`.

Conflicts are advisory, never blocking. Two agents can edit the same file — the inbox notifies both.

---

## Tool reference

### Lifecycle (orchestrator only)

| Tool | Effect |
|---|---|
| `start_session(feature, target_branch="main")` | Creates worktree + session. Fails if one is already open. |
| `finalize_session(message, sign=False)` | Commits worktree onto target branch. Returns `{final_sha}`. |
| `abort_session()` | Removes worktree, marks aborted. |
| `get_session()` | Returns current open session or null. |

### Agent lifecycle

| Tool | Effect |
|---|---|
| `register_agent(role)` | Returns `{agent_id}`. |
| `unregister_agent(agent_id)` | Marks agent ended. |
| `list_agents()` | Lists agents in current session. |

### Semantic intent

| Tool | Effect |
|---|---|
| `start_intent(agent_id, intent)` | Records intent start. Returns `{intent_id}`. |
| `repurpose(agent_id, intent)` | Records intent shift. Returns `{intent_id}`. |
| `get_current_intent(agent_id)` | Returns active intent. |

### File editing

| Tool | Effect |
|---|---|
| `edit_file(agent_id, file, old_string, new_string, replace_all=False)` | Exact match + atomic write. |
| `write_file(agent_id, file, content)` | Create / overwrite. Atomic write. |
| `read_file(agent_id, file)` | Returns `{content, sha256}`. No tracking. |
| `delete_file(agent_id, file)` | Removes file. |

### Inbox + observability

| Tool | Effect |
|---|---|
| `check_inbox(agent_id)` | Returns unread items, marks read. |
| `send_message(from_agent_id, to_agent_id, message)` | Sends manual message. |
| `list_edits(agent_id, file, since_ts)` | Debug: list edits. |
| `list_intents(agent_id)` | Debug: list intents. |

---

## Validation rules

Every tool call validates:
- `agent_id` is registered in the current open session.
- `agent_id.ended_at IS NULL` (agent is active).
- `session.state == 'open'`.
- File paths are inside the worktree (no `..` escape).

Failures return a clear error string the agent can act on.

---

## On-disk layout

```
<repo>/.gitagent/
├── state.db              # sqlite: sessions, agents, intents, edits, inbox
├── worktree/             # single detached worktree (active session)
├── _finalize_temp/       # temp worktree during finalize (auto-cleaned)
└── log.jsonl             # append-only audit trail
```
