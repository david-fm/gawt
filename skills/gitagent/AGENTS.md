# gitagent — Agent Playbook (Option B: accept = mark, integrate = apply)

A practical guide for AI agents (supervisors and subagents) using the `gitagent` CLI to coordinate multi-agent coding work.

Read `SKILL.md` first for the command reference and critical rules. This document is the **playbook** — patterns, recipes, and gotchas.

---

## 1. The three agent roles

`gitagent` naturally models three roles. A single agent can play more than one, but the responsibilities are distinct.

### 1.1 Supervisor (superagent) — the orchestrator

Owns the session, the integration branch, and the final commit.

**Responsibilities:**
- `init` + `start` the session
- `spawn` subagents (decide the split of work)
- Run `proposals` / `status` / `show` / `diff` to review
- Make decisions: `accept` / `reject` / `revise` (these are *decisions*, not applications)
- Run `integrate` to apply all accepted proposals (or rely on `finalize` to do it)
- Run `finalize` when done — this is the one and only commit

**Mindset:** the integration branch is yours. Subagents never see it; they only see their own worktree and your `feedback` strings.

### 1.2 Coder (subagent) — the implementer

Works inside a single isolated worktree. Cannot see other agents' work.

**Responsibilities:**
- Receive a task and a worktree path (from the supervisor)
- Implement the change in the worktree
- `git add -A` is **not** required (the supervisor's `propose` does it)
- Run `gitagent propose --agent <id> --title "..."` when done
- If sent back with `revise`, iterate and `propose` again (a new `pid` is generated each time)
- Never run `gitagent init` / `start` / `finalize` / `accept` / `integrate` — those are supervisor-only

**Mindset:** your worktree is disposable. The supervisor will `kill` it or let it be torn down at `finalize`. Don't put anything important there that isn't also captured in a proposal.

### 1.3 Reviewer (subagent) — the critic

Optional role. Examines proposals and recommends accept/reject/revise to the supervisor.

**Responsibilities:**
- Read proposals via `gitagent show <pid>` (colored) or `gitagent diff <pid>` (raw, LLM-friendly)
- Output a recommendation; the **supervisor** makes the final call via `accept`/`reject`/`revise`
- Never runs `accept`/`reject`/`revise` itself unless explicitly delegated

---

## 2. The canonical happy-path loop (Option B)

```
SUPERVISOR                          SUBAGENT
─────────                           ────────
gitagent init
gitagent start --feature "x"
gitagent spawn --id a_backend  ───▶ "implement X in your worktree"
                                    ... implement in worktree ...
                                    gitagent propose --agent a_backend \
                                       --title "..." --confidence 0.8
gitagent proposals
gitagent show <pid>
gitagent accept <pid>              (or revise/reject)   ← decision only
                                    ... if revise: iterate, re-propose ...
gitagent spawn --id a_tests    ───▶ "write tests for X"
                                    ... write tests ...
                                    gitagent propose --agent a_tests ...
gitagent accept <pid>              ← decision only

# OPTIONAL: apply now to see conflicts early
gitagent integrate                 ← applies all accepted in creation order

gitagent finalize -m "feat: X"     ← applies again if needed, then 1 commit
# 1 commit, .gitagent reset, no push
```

`finalize` calls `integrate` internally, so `accept` + `finalize` is the absolute minimum. Run `integrate` standalone when you want to surface conflicts *before* the final commit.

---

## 3. The proposal state machine

Each proposal moves through these states:

```
                    propose
                       │
                       ▼
                   ┌────────┐  accept   ┌──────────┐  integrate  ┌────────────┐
                   │pending │ ────────▶ │ accepted │ ──────────▶ │ integrated │
                   └────────┘           └──────────┘             └────────────┘
                       │                    │   ▲                     │
                       │ reject             │   │ retry (fix worktree │
                       ▼                    │   │  + re-accept)       │
                   ┌────────┐               │   └─────────────────────┘
                   │rejected│               │
                   └────────┘               │ integrate (3-way merge)
                                           ▼   fails
                                     ┌──────────┐
                                     │ conflict │ ─── revise ──▶ ┌────────┐
                                     └──────────┘                 │ revise │
                                                                 └────────┘
```

- **`accept` from `pending` or `conflict`** → marks `accepted` (no apply).
- **`integrate`** applies all `accepted` in **proposal creation order** (`created_at`). Success → `integrated`. Failure → `conflict`; processing continues with the next proposal.
- **`revise`** sends back to the agent. The agent re-proposes with a new `pid`; the old one stays as `revise` (audit trail).
- **`reject`** from any non-`integrated` state → `rejected`.
- An already-`integrated` proposal is immutable: `reject`/`revise`/`accept` error.

---

## 4. Driving the CLI from an LLM (machine-readable output)

For any command an LLM will consume, use `--json`. The JSON is **raw, uncolored, unwrapped** — safe to parse with `json.loads`.

```bash
# list proposals as JSON
proposals=$(gitagent proposals --json)

# parse with python
echo "$proposals" | python -c '
import sys, json
for p in json.load(sys.stdin):
    print(p["manifest"]["id"], p["review"]["state"], p["manifest"]["title"])
'

# get a raw diff to feed another LLM
gitagent diff p_7f3a | llm "review this patch for security issues"

# check status
gitagent status --json | python -c '
import sys, json
s = json.load(sys.stdin)
print("session:", s["session"]["id"], "state:", s["session"]["state"])
print("proposals:", len(s["proposals"]), "integrated:", s["integration"]["integrated_count"])
'

# apply and see the per-proposal result
gitagent integrate --json | python -c '
import sys, json
r = json.load(sys.stdin)
print("applied:", r["applied"])
print("conflicted:", r["conflicted"])
print("skipped:", r["skipped"])
'
```

**Anti-patterns:**
- Don't parse the colored table output of `gitagent status` (no `--json`) — always use `--json` for LLM consumption.
- Don't pipe `gitagent show <pid>` (it uses Rich) — use `gitagent diff <pid>` for raw output.

---

## 5. Conflict resolution recipes

Conflicts are normal in multi-agent work. The session is designed to survive them. With Option B, conflicts surface at **`integrate`** time (not at `accept`).

### Recipe 1: Detect at integrate, then revise

```bash
gitagent accept p_a   # marks accepted (no apply)
gitagent accept p_b   # marks accepted (no apply)
gitagent integrate
# → applies p_a (ok)
# → tries p_b → CONFLICT, marks p_b, session stays alive
#   summary: applied=[p_a], conflicted=[p_b], skipped=[]

# send p_b back for the agent to fix
gitagent revise p_b --feedback "your function collides with p_a's; rename it"
# the agent iterates and proposes again (new pid)
gitagent accept <new-pid>
gitagent integrate      # or just gitagent finalize
```

### Recipe 2: Manual fix in the integration worktree

```bash
gitagent integrate      # p_b conflicts
# integration worktree at .gitagent/integration/worktree has conflict markers,
# and the last commit on the integration branch is from p_a only
cd .gitagent/integration/worktree
# ... edit files to resolve conflict markers ...
git add -A && git commit -m "manual resolution: p_a + p_b"
cd ../../..
# now p_b is still marked "conflict" in review.json
# tell gitagent to retry: re-accept (resets state to 'accepted') and re-integrate
gitagent accept p_b
gitagent integrate      # p_b's patch now applies cleanly → state=integrated
```

### Recipe 3: Plan the creation order

Because integration order = proposal creation order, the supervisor should **spawn and let agents propose in the dependency order**:

```bash
# 1. agent_a proposes the schema/type first (it'll integrate first)
gitagent spawn --id a_schema
# ... agent_a proposes ...

# 2. agent_b proposes the consumer code (integrates after a_schema)
gitagent spawn --id a_consumer
# ... agent_b proposes ...
```

The supervisor can accept them in any order; integration honors creation order. This is the single most important lever to avoid spurious conflicts.

---

## 6. Working as a subagent in a worktree

You are spawned. You got a worktree path. Now what?

```bash
# 1. cd into your worktree
cd /path/to/repo/.gitagent/agents/<your-id>/worktree

# 2. explore the codebase, make your changes
#    ... edit files ...

# 3. check what you've changed
git status
git diff HEAD   # this is exactly what `gitagent propose` will capture

# 4. submit your proposal (run from the repo root, not the worktree)
cd /path/to/repo
gitagent propose --agent <your-id> --title "Short summary" \
                 --summary "Longer description" --confidence 0.85
```

**Important:** the worktree is a real `git worktree`. You can `git commit` inside it for your own scratch organization — but those commits are local to your worktree and **will not** end up in the final history. The supervisor only sees your `propose`'d patch (which is `git diff` of your worktree vs the base you were spawned on).

If you `git commit` inside the worktree and then `propose`, the committed changes still appear in the diff (they're in the tree, just not in HEAD). That's fine. If you want a clean patch, leave things uncommitted — `propose` will diff against your base SHA regardless of staging.

---

## 7. Concurrent agents — what you can and can't do

`gitagent` uses file locks (`fcntl`) to make write operations safe. But it does NOT coordinate the *content* of agents' work — only the metadata writes.

**Safe to do concurrently:**
- Multiple agents editing in their own worktrees (they're isolated)
- Multiple agents `propose`ing at the same time (the lock serializes the writes)

**Not safe — the supervisor must serialize:**
- `accept` / `reject` / `revise` calls (they mutate review state)
- `integrate` and `finalize` (must run alone)

**If two `accept`s race:** the lock makes the writes safe, but the order is whichever lock-holder runs first. Since `accept` only marks state (doesn't apply), racing accepts are harmless — both just set state=accepted.

**If you race `integrate`:** the lock serializes it. The second caller will see the first's effects (some proposals now `integrated`) and skip them. Safe but pointless to race.

---

## 8. Idempotency and re-runs

| Command | Idempotent? | Notes |
|---|---|---|
| `init` | No — errors on re-run | |
| `start` | No — errors if session active | Use `abort` first |
| `spawn` | No — errors on duplicate id | |
| `propose` | No — generates a new pid each time | Don't re-propose the same work |
| `accept` | Yes (from `accepted` not-yet-applied) | From `pending`/`conflict` it resets to `accepted` |
| `reject` | Yes | Re-rejecting is harmless |
| `revise` | Yes (from `revise`) | Updates feedback |
| `integrate` | Yes | Skips already-`integrated` proposals |
| `finalize` | No — but `finalize` after abort errors cleanly | |

---

## 9. Common mistakes to avoid

1. **Pushing after `finalize` and expecting it to "just work"** — `gitagent` doesn't push. Run `git push` yourself.
2. **Trying to `finalize` with zero accepted proposals** — errors with "No integrated proposals". Accept at least one first.
3. **Modifying `.gitagent/integration/worktree` directly without committing** — your changes will be lost on the next `integrate` (which does `git add -A` + commit, so this is usually fine, but don't leave uncommitted work expecting it to persist between integrate runs).
4. **Running `gitagent` from inside an agent's worktree** — it must run from the repo root (or any subdir; it finds the repo via `git rev-parse --show-toplevel`). Running from a worktree is *technically* fine (it'll find the main repo) but confusing.
5. **Expecting `abort` to roll back the integration commit** — `abort` only cleans `.gitagent/`. If you `merge --squash`'d already (via `finalize`), that's a real commit on your branch; `git reset` it manually.
6. **Spawning 10 agents and proposing in random order** — integration follows creation order, so the last agent to `propose` will integrate on top. Make the "base" agents propose first.
7. **Re-proposing after `revise` and trying to "edit" the old `pid`** — `propose` always creates a new `pid`. The old one stays as `revise` (audit trail). Don't try to mutate an existing proposal; submit a new one.
8. **Treating `accept` as "apply"** — it isn't. Always follow accepts with `integrate` (or `finalize`, which integrates for you) to actually apply.
9. **Forgetting that `finalize` runs `integrate` first** — if some proposals would conflict, `finalize` will mark them `conflict` and *still* produce a commit with whatever did integrate. To avoid surprise conflicts, run `integrate` standalone and inspect before finalizing.

---

## 10. Quick copy-paste recipes

### Recipe: full supervisor loop in one script

```bash
#!/bin/bash
set -euo pipefail
REPO="${1:?repo path}"
FEATURE="${2:?feature name}"
MSG="${3:?commit message}"
cd "$REPO"

gitagent init
gitagent start --feature "$FEATURE"

# spawn agents (your orchestration decides the work split)
gitagent spawn --id a_backend --role "implement core"
gitagent spawn --id a_tests   --role "write tests"

# ... agents do their work and propose ...
# (in practice, you poll or wait for proposals)

# accept everything pending
for pid in $(gitagent proposals --json | python -c '
import sys, json
for p in json.load(sys.stdin):
    if p["review"]["state"] == "pending":
        print(p["manifest"]["id"])
'); do
  gitagent accept "$pid"
done

# OPTIONAL: pre-flight integrate to surface conflicts before committing
summary=$(gitagent integrate --json)
conflicts=$(echo "$summary" | python -c 'import sys,json;print(",".join(json.load(sys.stdin)["conflicted"]))')
if [ -n "$conflicts" ]; then
  echo "Conflicts: $conflicts" >&2
  echo "Resolve (revise, or fix integration worktree + re-accept + re-integrate), then re-run." >&2
  exit 1
fi

gitagent finalize --message "$MSG"
```

### Recipe: subagent proposal

```bash
#!/bin/bash
# run from the repo root
AGENT="${1:?agent id}"
TITLE="${2:?title}"
SUMMARY="${3:-}"
CONFIDENCE="${4:-0.8}"

cd "$(git rev-parse --show-toplevel)"
gitagent propose --agent "$AGENT" --title "$TITLE" \
                 --summary "$SUMMARY" --confidence "$CONFIDENCE"
```

### Recipe: supervisor polling loop

```bash
# wait for N proposals to appear, then exit
N="${1:?expected number of proposals}"
while true; do
  count=$(gitagent proposals --json | python -c 'import sys,json;print(len(json.load(sys.stdin)))')
  if [ "$count" -ge "$N" ]; then break; fi
  sleep 2
done
```

---

## 11. Programmatic / scripted use

If you're writing a wrapper around `gitagent` (e.g., a Claude/GPT orchestration tool):

- **Always shell out** — `gitagent` is a CLI, not a library. (The Python modules *are* importable from `gitagent.*` if you have the package on `PYTHONPATH`, but the public contract is the CLI.)
- **Parse `--json`, not human output.** Stable contract: `dict` with documented keys (`session`, `agents`, `proposals`, `integration`, `manifest`, `review`).
- **Exit codes:** `0` = success; `1` = any `GitAgentError` (uninitialized, no session, conflict, etc.). Errors print to stderr.
- **Idempotent commands:** `integrate`, `proposals`, `status`, `log` are safe to re-run.

### Programmatic example (Python, Option B)

```python
import json, subprocess

def ga(*args):
    r = subprocess.run(["gitagent", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gitagent {' '.join(args)}: {r.stderr}")
    return r.stdout

# drive it
ga("init")
ga("start", "--feature", "rate-limiter")
ga("spawn", "--id", "a_backend")
# ... (subagent works) ...
ga("propose", "--agent", "a_backend", "--title", "limiter", "--confidence", "0.9")

proposals = json.loads(ga("proposals", "--json"))
for p in proposals:
    if p["review"]["state"] == "pending":
        # show diff to your LLM-of-choice, get accept/reject decision
        diff = ga("diff", p["manifest"]["id"])
        decision = ask_llm(diff)  # "accept" / "reject" / "revise"
        if decision == "accept":
            ga("accept", p["manifest"]["id"])
        elif decision == "reject":
            ga("reject", p["manifest"]["id"], "--reason", "...")
        else:
            ga("revise", p["manifest"]["id"], "--feedback", "...")

# apply everything that was accepted
summary = json.loads(ga("integrate", "--json"))
if summary["conflicted"]:
    for pid in summary["conflicted"]:
        print(f"conflict: {pid} → revise or fix manually")
else:
    ga("finalize", "--message", "feat: rate limiter")
```

---

## 12. Where things live on disk (for debugging)

```
<repo>/.gitagent/
├── session.json              # active session id, feature, base_sha, state
├── agents/<id>/meta.json     # role, worktree, branch, base_sha
├── agents/<id>/worktree/     # the agent's isolated git worktree
├── proposals/<pid>/
│   ├── manifest.json         # id, agent_id, base_sha, title, files, summary, confidence
│   ├── change.patch          # raw git diff of the proposal
│   └── review.json           # state, feedback, integrated, integration_sha
├── integration/worktree/     # the integration branch's worktree
├── locks/                    # fcntl lockfiles (one per resource)
└── log.jsonl                 # append-only audit trail
```

If something looks wrong, read `session.json`, the relevant `meta.json`, the `review.json`, and the tail of `log.jsonl` — 90% of bugs are visible there.

---

## 13. Limitations & roadmap (v0.1)

This is the MVP. Known limitations, in priority order:

- **No `--json` on every command** (only `status`, `log`, `list-agents`, `proposals`, `integrate`). Patches always go through `diff` (raw) for now.
- **No `config.toml` policies** — no way to enforce "always run ruff before propose" or "limit N agents per session" yet. Roadmap v0.3.
- **No hooks** — can't auto-trigger anything on `propose`/`accept`. Roadmap v0.3.
- **No GitHub PR export** — you finalize to a local commit, then `git push` and open a PR yourself. Roadmap v0.4.
- **No Neo4j "virtual commits" sync** — the audit log is on disk but not graph-linked. Roadmap v0.4.
- **No reordering of integration** — integration follows proposal creation order. There's no `gitagent reorder` command. Workaround: plan propose order, or fix conflicts as they come.
