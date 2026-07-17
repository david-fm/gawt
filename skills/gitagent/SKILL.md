---
name: gitagent
description: Use this skill when you (or another agent you supervise) need to coordinate multi-agent coding work over Git — spawning isolated subagent worktrees per feature, collecting proposals as patches, accepting/rejecting/revising them, and producing one clean commit on main. Triggers on "gitagent", "agent worktree", "multi-agent git workflow", "isolate subagent work", "consolidate agent changes into one commit", "spawn an agent for X", "review proposal", "agent proposal", "multi-feature", "parallel features". Do NOT use for: general git questions (use regular git), single-developer workflows, or non-agent coordination tasks.
---

# gitagent

A CLI for coordinating **multiple AI subagents** working in the same Git repository, on **multiple features in parallel**. Each feature gets its own session, subagent worktrees, proposals, and integration branch. You (the superagent) collect subagent work as **proposals** (patches + manifests), review them, and finalize each feature as **one commit on `main`**. `gitagent` never pushes.

**Branchless workflow**: all commands accept `--feature <name>`. Your local checkout is never disturbed.

> Two planes: `.git` (real history, untouched until `finalize`) and `.gitagent/` (ephemeral coordination — one subdirectory per feature, plus a global audit log).

This document is the **complete skill** — frontmatter loads it, and it contains the full playbook. There is no companion file; everything you need is here.

---

## 1. The three agent roles

`gitagent` naturally models three roles. A single agent can play more than one, but the responsibilities are distinct.

### 1.1 Supervisor (superagent) — the orchestrator

Owns **all** active sessions — one per feature — and the final commit per feature. Lands features on `main` directly.

**Responsibilities:**
- `init` once per repo
- For each feature: `start --feature <name>`, `spawn` subagents, review, decide, `finalize`
- Run `list-features` to see all in-flight features
- `status --feature <name>` to see detail for one feature
- `finalize --feature <name> -m "..."` lands on main directly (no manual merge needed)

**Mindset:** the integration branch is yours per feature. Subagents never see it; they only see their own worktree and your `feedback` strings. The `main` branch is **never** touched by `gitagent`.

### 1.2 Coder (subagent) — the implementer

Works inside a single isolated worktree. Cannot see other agents' work (even less other features).

**Responsibilities:**
- Receive a task and a worktree path (from the supervisor)
- Implement the change in the worktree
- `git add -A` is **not** required (the supervisor's `propose` does it)
- Run `gitagent propose --agent <id> --title "..."` when done
- If sent back with `revise`, iterate and `propose` again (a new `pid` is generated each time)
- Never run `gitagent init` / `start` / `finalize` / `accept` / `integrate` — those are supervisor-only
- The proposal lands in **the current feature's session**, not the main repo. Subagents don't care which branch the supervisor is on — they just `propose` and the supervisor's checkout determines where the session lives.

**Mindset:** your worktree is disposable. The supervisor will `kill` it or let it be torn down at `finalize`. Don't put anything important there that isn't also captured in a proposal.

### 1.3 Reviewer (subagent) — the critic

Optional role. Examines proposals and recommends accept/reject/revise to the supervisor.

**Responsibilities:**
- Read proposals via `gitagent show <pid>` (colored) or `gitagent diff <pid>` (raw, LLM-friendly)
- Output a recommendation; the **supervisor** makes the final call via `accept`/`reject`/`revise`
- Never runs `accept`/`reject`/`revise` itself unless explicitly delegated

---

## 2. The multi-feature model

**The single rule:** a feature is identified by `--feature <name>`. The `ga/<name>` branch is internal — created at `start`, deleted at `finalize`. Your local checkout is never disturbed.

```bash
gitagent init

# Feature A: auth rate limiting (no checkout needed)
gitagent start --feature auth-rate-limiting
gitagent spawn --feature auth-rate-limiting --id a_backend
# ... work, propose, accept, finalize ...
gitagent finalize --feature auth-rate-limiting -m "feat(auth): rate limiting"
# → 1 commit on main

# Feature B: user profile (parallel)
gitagent start --feature user-profile
gitagent spawn --feature user-profile --id a_frontend
# ... work, propose, accept, finalize ...
gitagent finalize --feature user-profile -m "feat(user): profile page"
# → 1 commit on main

# Both commits are on main. No git merge needed.
```

Without `--feature`, the current branch is used as default (with a deprecation warning). Pass `--feature` explicitly to avoid the checkout dance.

**What's isolated per feature:**
- session.json (different `s_<hex>` ids)
- agents and their worktrees (different folders under `.gitagent/features/<key>/agents/`)
- proposals and their patches
- integration branch and worktree
- locks (each feature has its own)

**What's shared:**
- The git object store (of course — same repo).
- The audit log (`.gitagent/log.jsonl`) — events for *all* features are interleaved. Use the `feature_key` field to filter.

**What the supervisor does NOT do via gitagent:**
- `git checkout` to switch features. All operations target features by name.

---

## 3. Decision / application split (Option B)

The CLI cleanly separates **deciding** from **applying**:

| Command | Role |
|---|---|
| `accept <pid>` | Record the decision "this proposal is approved". Does **not** apply. |
| `reject <pid>` | Record "this proposal is rejected". |
| `revise <pid>` | Send back to the agent for another iteration (new `pid` on re-propose). |
| `integrate` | **Apply** all accepted proposals onto the integration branch (in creation order). Detects conflicts. |
| `finalize` | Calls `integrate` (if needed) + creates **one** commit on the current feature branch. |

`finalize` calls `integrate` internally, so a minimal flow is: `accept` each → `finalize`. Run `integrate` explicitly when you want to see conflicts before committing.

---

## 4. When to use / when NOT to use

**Use gitagent when:**
- A superagent is orchestrating one or more coding subagents in the same repo, possibly on **multiple features in parallel**.
- Subagents must work **in parallel without trampling each other** (even across features).
- The result per feature must be **one clean commit** (not N agent commits polluting history).
- You need a **review loop** (accept / reject / revise) before consolidation.
- You want **machine-readable output** (`--json`, raw `diff`) to drive orchestration.

**Do NOT use gitagent when:**
- Single-developer / single-agent workflows → just use `git worktree` directly.
- You want a real PR-based workflow with humans reviewing on GitHub → use GitHub PRs (you can still land feature branches via PRs *after* `finalize`).
- You want to push to origin → `gitagent` never pushes; that's your job.

---

## 5. Quick reference (commands)

```bash
gitagent init                                # one-time per repo

gitagent start --feature <name>              # open a session for a feature

gitagent spawn --feature <name> --id <agent-id> [--role "..."] [--base <ref>]
gitagent list-agents --feature <name>        # --json available
gitagent kill <agent-id> --feature <name>

# inside an agent's worktree, the subagent does its work, then:
gitagent propose --feature <name> --agent <id> --title "..." [--summary "..."] [--confidence 0.8]

gitagent proposals --feature <name>          # --json available
gitagent show <pid> --feature <name>         # colored diff (human)
gitagent diff <pid> --feature <name>         # raw diff (pipe to LLM / apply)
gitagent status                              # --json available (all features)
gitagent status --feature <name>             # detail view for one feature
gitagent list-features                       # --json available (all features in the repo)
gitagent log                                 # --json available (audit trail; global, all features)

# superagent decisions (mark only — no apply):
gitagent accept <pid> --feature <name>
gitagent reject <pid> --feature <name> [--reason "..."]
gitagent revise <pid> --feature <name> --feedback "..."

# application step (apply all accepted; resets to live target, surfaces cross-feature conflicts):
gitagent integrate --feature <name>          # --json available

# single commit on the target branch (default: main), then reset .gitagent:
gitagent finalize --feature <name> --message "<msg>" [--target main] [--keep-feature-branch] [--sign]
```

---

## 6. The canonical happy-path loop (single feature)

```
SUPERVISOR                          SUBAGENT
─────────                           ────────
gitagent init
git checkout -b ga/auth-rl main
gitagent start
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
# 1 commit on ga/auth-rl, .gitagent reset, no push
```

`finalize` calls `integrate` internally, so `accept` + `finalize` is the absolute minimum. Run `integrate` standalone when you want to surface conflicts *before* the final commit.

---

## 7. The multi-feature loop (parallel features)

The same flow, repeated per feature branch. Switch branches to switch sessions.

```
SUPERVISOR (single agent, many features in flight)
──────────────────────────────────────────────────

# == Feature A: auth rate limiting ==
git checkout -b ga/auth-rl main
gitagent start
gitagent spawn --id a_backend
# ... a_backend proposes ...
gitagent accept p_x
gitagent finalize -m "feat(auth): rate limiting"
# → 1 commit on ga/auth-rl

# == Feature B: user profile (started in parallel, finished in parallel) ==
git checkout -b ga/user-profile main
gitagent start
gitagent spawn --id a_frontend
# ... a_frontend proposes ...
gitagent accept p_y
gitagent finalize -m "feat(user): profile page"
# → 1 commit on ga/user-profile

# == Land on main (plain git) ==
git checkout main
git merge --squash ga/auth-rl      && git commit -m "feat(auth): rate limiting"
git merge --squash ga/user-profile && git commit -m "feat(user): profile page"
# OR: open two PRs and let humans review.
```

**Critical:** `gitagent start` **refuses** to run on `main` or `master`. You must
be on a `ga/<name>` branch. If you see `error: Current branch 'main' is not a
feature branch`, run `git checkout -b ga/<name> main` first.

---

## 8. Critical rules

1. **Never push.** `finalize` produces a local commit on the current feature branch and stops. You push and merge if you want to.
2. **Never commit inside an agent worktree directly.** Agents work uncommitted; `propose` snapshots the diff. (You *can* commit locally in a worktree for the agent's own scratch use — just `git add -A` again before `propose` will include those changes too.)
3. **Always pass `--json` to commands an LLM will consume.** Human output is for humans.
4. **`diff <pid>` is pipe-friendly** — it's a raw `git diff` patch. Use it to feed another LLM, to `git apply` manually, or to inspect.
5. **Conflicts don't kill the session.** A `conflict` proposal means "this patch didn't apply cleanly to current integration". Use `revise` to send it back, or fix the integration worktree manually and re-`accept` then re-`integrate`.
6. **Integration order = proposal creation order.** The first agent to `propose` integrates first. Plan who proposes first if their change is the "base" others depend on.
7. **`abort` is destructive** — it removes all agent worktrees, branches, and the feature's `.gitagent/` state (keeping the audit log). Use it when you want to start over on a feature.
8. **`--feature` is the session selector.** All commands that operate on a feature accept `--feature <name>`. Without it, the current branch is used as default (with a deprecation warning). Pass `--feature` explicitly to avoid the checkout dance.
9. **`finalize` lands on `main` directly** (configurable via `--target`). Uses a detached temp worktree. The user's checkout is never disturbed. The `ga/<feature>` branch is deleted (use `--keep-feature-branch` to preserve it).

---

## 9. The proposal state machine

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

## 10. Driving the CLI from an LLM (machine-readable output)

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

## 11. Conflict resolution recipes

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

## 12. Working as a subagent in a worktree

You are spawned. You got a worktree path. Now what?

```bash
# 1. cd into your worktree
#    (path is typically .gitagent/features/<feature-key>/agents/<your-id>/worktree)
cd /path/to/repo/.gitagent/features/<feature-key>/agents/<your-id>/worktree

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

## 13. Concurrent agents — what you can and can't do

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

## 14. Idempotency and re-runs

| Command | Idempotent? | Notes |
|---|---|---|
| `init` | No — errors on re-run | |
| `start` | No — errors if a session is active for the current feature branch | `git checkout` another `ga/...` branch to switch features; `abort` to clear the current one |
| `spawn` | No — errors on duplicate id (per feature) | |
| `propose` | No — generates a new pid each time | Don't re-propose the same work |
| `accept` | Yes (from `accepted` not-yet-applied) | From `pending`/`conflict` it resets to `accepted` |
| `reject` | Yes | Re-rejecting is harmless |
| `revise` | Yes (from `revise`) | Updates feedback |
| `integrate` | Yes | Skips already-`integrated` proposals |
| `finalize` | No — but `finalize` after abort errors cleanly | Per feature. Different features are independent. |
| `list-features` | Yes | Read-only. |
| `status` | Yes | Read-only; works on any branch (returns `session: null` on `main`). |

---

## 15. Common mistakes to avoid

1. **Pushing after `finalize` and expecting it to "just work"** — `gitagent` doesn't push. Run `git push` yourself.
2. **Trying to `finalize` with zero accepted proposals** — errors with "No integrated proposals". Accept at least one first.
3. **Modifying `.gitagent/features/<key>/integration/worktree` directly without committing** — your changes will be lost on the next `integrate` (which does `git add -A` + commit, so this is usually fine, but don't leave uncommitted work expecting it to persist between integrate runs).
4. **Running `gitagent` from inside an agent's worktree** — it must run from the repo root (or any subdir; it finds the repo via `git rev-parse --show-toplevel`). Running from a worktree is *technically* fine (it'll find the main repo) but confusing.
5. **Expecting `abort` to delete the feature branch** — `abort` cleans the feature's `.gitagent/` state and removes agent worktrees, but **the feature branch is preserved**. That's your commit; you merge it to `main`.
6. **Spawning 10 agents and proposing in random order** — integration follows creation order, so the last agent to `propose` will integrate on top. Make the "base" agents propose first.
7. **Re-proposing after `revise` and trying to "edit" the old `pid`** — `propose` always creates a new `pid`. The old one stays as `revise` (audit trail). Don't try to mutate an existing proposal; submit a new one.
8. **Treating `accept` as "apply"** — it isn't. Always follow accepts with `integrate` (or `finalize`, which integrates for you) to actually apply.
9. **Forgetting that `finalize` runs `integrate` first** — if some proposals would conflict, `finalize` will mark them `conflict` and *still* produce a commit with whatever did integrate. To avoid surprise conflicts, run `integrate` standalone and inspect before finalizing.
10. **Trying `gitagent start` on `main`** — errors with "Current branch 'main' is not a feature branch". Create the feature branch with `git checkout -b ga/<name> main` first.
11. **Passing `--feature` to `start`** — there's no such flag anymore. The feature name is derived from the branch. If you want a different feature name, use a different branch name.
12. **Forgetting which branch you're on** — `start`, `spawn`, `propose`, `accept`, `integrate`, `finalize` all operate on the session attached to the *current* branch. Always run `git branch --show-current` first if you're unsure. Use `gitagent list-features` for a global view.

---

## 16. Quick copy-paste recipes

### Recipe: full supervisor loop in one script (single feature)

```bash
#!/bin/bash
set -euo pipefail
REPO="${1:?repo path}"
FEATURE_BRANCH="${2:?feature branch, e.g. ga/auth-rl}"
MSG="${3:?commit message}"
cd "$REPO"

gitagent init
# The user (or orchestration) has already created the feature branch.
# If not:
#   git checkout -b "$FEATURE_BRANCH" main
git checkout "$FEATURE_BRANCH"
gitagent start

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
# Then the supervisor (or user) merges the feature branch into main with git.
```

### Recipe: drive two features in parallel, then merge to main

```bash
#!/bin/bash
set -euo pipefail
REPO="${1:?repo path}"
cd "$REPO"

gitagent init

# Feature A
git checkout -b ga/auth-rl main
gitagent start
gitagent spawn --id a_backend
# ... wait for a_backend to propose ...
gitagent accept "$(gitagent proposals --json | python -c 'import sys,json;print(json.load(sys.stdin)[0]["manifest"]["id"])')"
gitagent integrate
gitagent finalize -m "feat(auth): rate limiting"

# Feature B (parallel)
git checkout main
git checkout -b ga/user-profile main
gitagent start
gitagent spawn --id a_frontend
# ... wait for a_frontend to propose ...
gitagent accept "$(gitagent proposals --json | python -c 'import sys,json;print(json.load(sys.stdin)[0]["manifest"]["id"])')"
gitagent integrate
gitagent finalize -m "feat(user): profile page"

# Land both on main
git checkout main
git merge --squash ga/auth-rl      && git commit -m "feat(auth): rate limiting"
git merge --squash ga/user-profile && git commit -m "feat(user): profile page"
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

## 17. Programmatic / scripted use

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

def git(*args, cwd=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr}")
    return r.stdout.strip()

# Set up a feature (the orchestration creates the branch)
git("checkout", "-b", "ga/rate-limiter", "main")
ga("init")
ga("start")  # feature name = "rate-limiter" (derived from the branch)
ga("spawn", "--id", "a_backend")
# ... (subagent works) ...
ga("propose", "--agent", "a_backend", "--title", "limiter", "--confidence", "0.9")

proposals = json.loads(ga("proposals", "--json"))
for p in proposals:
    if p["review"]["state"] == "pending":
        diff = ga("diff", p["manifest"]["id"])
        decision = ask_llm(diff)  # "accept" / "reject" / "revise"
        if decision == "accept":
            ga("accept", p["manifest"]["id"])
        elif decision == "reject":
            ga("reject", p["manifest"]["id"], "--reason", "...")
        else:
            ga("revise", p["manifest"]["id"], "--feedback", "...")

summary = json.loads(ga("integrate", "--json"))
if summary["conflicted"]:
    for pid in summary["conflicted"]:
        print(f"conflict: {pid} → revise or fix manually")
else:
    ga("finalize", "--message", "feat: rate limiter")

# Now start another feature in parallel
git("checkout", "main")
git("checkout", "-b", "ga/other-feature", "main")
ga("start")
# ... etc ...

# When all features are done, the supervisor lands on main
git("checkout", "main")
git("merge", "--squash", "ga/rate-limiter")
git("commit", "-m", "feat: rate limiter")
```

---

## 18. Where things live on disk (for debugging)

```
<repo>/.gitagent/
├── features/
│   ├── <feature-key>/         # one per feature branch (e.g. 'auth-rl' for ga/auth-rl)
│   │   ├── session.json       # active session: id, feature, base_sha, state, branch
│   │   ├── agents/<id>/
│   │   │   ├── meta.json      # role, worktree, branch, base_sha
│   │   │   └── worktree/      # the agent's isolated git worktree
│   │   ├── proposals/<pid>/
│   │   │   ├── manifest.json  # id, agent_id, base_sha, title, files, summary, confidence
│   │   │   ├── change.patch   # raw git diff of the proposal
│   │   │   └── review.json    # state, feedback, integrated, integration_sha
│   │   ├── integration/worktree/  # the integration branch's worktree
│   │   └── locks/             # fcntl lockfiles (one per resource)
│   └── ...
└── log.jsonl                  # append-only audit trail (global; spans all features)
```

If something looks wrong, read `session.json`, the relevant `meta.json`, the `review.json`, and the tail of `log.jsonl` — 90% of bugs are visible there. The `feature_key` field on every log event lets you filter to a single feature.

---

## 19. Limitations & roadmap (v0.2)

This is the multi-feature release. Known limitations, in priority order:

- **No `--json` on every command** (only `status`, `log`, `list-agents`, `list-features`, `proposals`, `integrate`). Patches always go through `diff` (raw) for now.
- **No `config.toml` policies** — no way to enforce "always run ruff before propose" or "limit N agents per feature" yet. Roadmap v0.3.
- **No hooks** — can't auto-trigger anything on `propose`/`accept`. Roadmap v0.3.
- **No GitHub PR export** — you finalize to a local commit on the feature branch, then `git push` and open a PR yourself. The supervisor merges to `main` (squash, PR, etc.). Roadmap v0.4.
- **No Neo4j "virtual commits" sync** — the audit log is on disk but not graph-linked. Roadmap v0.4.
- **No reordering of integration** — integration follows proposal creation order within a feature. There's no `gitagent reorder` command. Workaround: plan propose order, or fix conflicts as they come.
- **No automatic cross-feature ordering** — if feature A and feature B touch the same file, the supervisor's merge of A into `main` may conflict with B. Detect by inspecting `gitagent list-features` and planning merge order. Workaround: merge features to `main` in the dependency order; or rebase `ga/<later>` on `main` after `main` moves.
