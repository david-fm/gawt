---
name: gitagent
description: Use this skill when you (or another agent you supervise) need to coordinate multi-agent coding work over Git — spawning isolated subagent worktrees, collecting proposals as patches, accepting/rejecting/revising them, and producing a single clean commit. Triggers on "gitagent", "agent worktree", "multi-agent git workflow", "isolate subagent work", "consolidate agent changes into one commit", "spawn an agent for X", "review proposal", "agent proposal". Do NOT use for: general git questions (use regular git), single-developer workflows, or non-agent coordination tasks.
---

# gitagent

A CLI for coordinating **multiple AI subagents** working in the same Git repository. Each subagent gets an isolated `git worktree`; you (the superagent) collect their work as **proposals** (patches + manifests), review them, and finalize everything as **one commit** on the real branch. `gitagent` never pushes.

> Two planes: `.git` (real history, untouched until `finalize`) and `.gitagent/` (ephemeral coordination — worktrees, patches, decisions, audit log — reset after `finalize`/`abort`).

## Decision / application split (Option B)

The CLI cleanly separates **deciding** from **applying**:

| Command | Role |
|---|---|
| `accept <pid>` | Record the decision "this proposal is approved". Does **not** apply. |
| `reject <pid>` | Record "this proposal is rejected". |
| `revise <pid>` | Send back to the agent for another iteration (new `pid` on re-propose). |
| `integrate` | **Apply** all accepted proposals onto the integration branch (in creation order). Detects conflicts. |

`finalize` calls `integrate` internally, so a minimal flow is: `accept` each → `finalize`. Run `integrate` explicitly when you want to see conflicts before committing.

## When to use

- A superagent is orchestrating one or more coding subagents in the same repo.
- Subagents must work **in parallel without trampling each other**.
- The final result must be **one clean commit** (not N agent commits polluting history).
- You need a **review loop** (accept / reject / revise) before consolidation.
- You want **machine-readable output** (`--json`, raw `diff`) to drive orchestration.

## When NOT to use

- Single-developer / single-agent workflows → just use `git worktree` directly.
- You want a real PR-based workflow with humans reviewing on GitHub → use GitHub PRs.
- You want to push to origin → `gitagent` never pushes; that's your job.

## Quick reference

```bash
gitagent init                                # one-time per repo
gitagent start --feature "<name>"            # open session at current HEAD

gitagent spawn --id <agent-id> [--role "..."] [--base <ref>]
gitagent list-agents                         # --json available
gitagent kill <agent-id>

# inside an agent's worktree, the subagent does its work, then:
gitagent propose --agent <id> --title "..." [--summary "..."] [--confidence 0.8]

gitagent proposals                           # --json available
gitagent show <pid>                          # colored diff (human)
gitagent diff <pid>                          # raw diff (pipe to LLM / apply)
gitagent status                              # --json available
gitagent log                                 # --json available (audit trail)

# superagent decisions (mark only — no apply):
gitagent accept <pid>
gitagent reject <pid> [--reason "..."]
gitagent revise <pid> --feedback "..."

# application step (apply all accepted; surfaces conflicts):
gitagent integrate                           # --json available

# single commit, then reset .gitagent:
gitagent finalize --message "<msg>" [--sign] [--no-reset]
```

## Critical rules

1. **Never push.** `finalize` produces a local commit and stops. You push if you want to.
2. **Never commit inside an agent worktree directly.** Agents work uncommitted; `propose` snapshots the diff. (You *can* commit locally in a worktree for the agent's own scratch use — just `git add -A` again before `propose` will include those changes too.)
3. **Always pass `--json` to commands an LLM will consume.** Human output is for humans.
4. **`diff <pid>` is pipe-friendly** — it's a raw `git diff` patch. Use it to feed another LLM, to `git apply` manually, or to inspect.
5. **Conflicts don't kill the session.** A `conflict` proposal means "this patch didn't apply cleanly to current integration". Use `revise` to send it back, or fix the integration worktree manually and re-`accept` then re-`integrate`.
6. **Integration order = proposal creation order.** The first agent to `propose` integrates first. Plan who proposes first if their change is the "base" others depend on.
7. **`abort` is destructive** — it removes all agent worktrees, branches, and `.gitagent/` state (keeping the audit log). Use it when you want to start over.

## For the full playbook

Read `AGENTS.md` in this skill directory. It covers:

- The supervisor / coder / reviewer agent patterns
- Conflict resolution recipes
- Concurrent agent safety
- Driving the CLI from another LLM (`--json` pipelines)
- Common mistakes to avoid
- Programmatic (Python) usage
