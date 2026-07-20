# gitagent

> Agent workspace manager over Git: isolate, review and consolidate multi-agent changes into a **single commit per feature** — directly on `main`.

`gitagent` is a lightweight CLI that wraps `git worktree` so a **superagent** can spawn isolated **subagents** per feature, collect their work as **proposals** (patch + manifest), accept/reject/revise them, and finally produce **one clean commit on `main`** — then clean everything up. It never pushes.

**Decoupled from your git branches**: gitagent never creates or switches branches in your repository. All work happens in **detached worktrees** derived from `main`. Every command requires `--feature <name>` (a logical key, not a git branch). Your local repo stays on `main` throughout — agents, integration and proposals are fully isolated coordination state, never refs in your repo.

The headline feature: **multiple features in parallel, with multiple agents per feature**. Run `gitagent start --feature <name>` for each, spawn agents per feature, and switch between them. Each feature keeps its own session, agents, proposals, and an integration worktree. `finalize` writes exactly one commit on `main` per feature.

Two planes are kept strictly separate:

- **`.git`** — the source of truth. Untouched until `finalize`, which writes exactly one commit on `main` (or a configured target). gitagent never adds a `ga/`, `agent/`, or `gitagent/integration/` branch to your repo.
- **`.gitagent/`** — an ephemeral coordination layer, one subdirectory per feature: detached worktrees, patches, manifests, decisions, audit log. Discardable and reset after `finalize`/`abort`.

`gitagent` does **not** reimplement Git. Proposals are stored as `git diff` patches plus JSON metadata, never as a second history.

## Decision / application split (Option B)

`gitagent` cleanly separates **deciding** from **applying**:

| Command | Role |
|---|---|
| `accept <pid>` | Record the decision "approved". Does **not** apply. |
| `reject <pid>` | Record "rejected". |
| `revise <pid>` | Send back to the agent (new `pid` on re-propose). |
| `integrate` | **Apply** all accepted proposals onto the integration worktree. Detects conflicts. |
| `finalize` | Calls `integrate` (if needed) + creates **one** commit on the target branch (`main`). |

So a minimal flow is: `accept` each → `finalize`. Run `integrate` standalone when you want to see conflicts *before* the final commit.

## Install

The PyPI distribution is **`gawt`** (the name `gitagent` on PyPI is a different,
unrelated project). The command it installs is still called `gitagent`.

```bash
# end user (isolated environment)
pipx install gawt
# or
uv tool install gawt

# from GitHub
pipx install "git+https://github.com/david-fm/gawt"

# local development
git clone https://github.com/david-fm/gawt && cd gawt
uv tool install . -e   # or: pipx install -e .
```

Requires Python 3.11+ and a working `git` on `PATH`.

## Quick start (single feature)

```bash
gitagent init

# Start a feature from main (no checkout needed, no branch created)
gitagent start --feature auth-rate-limiting

# each subagent gets its own detached worktree (no branch added to your repo)
gitagent spawn --feature auth-rate-limiting --id a_backend --role "implement limiter"
gitagent spawn --feature auth-rate-limiting --id a_tests --role "write tests"

# subagents work in their own folders, then propose (no commit to .git)
gitagent propose --feature auth-rate-limiting --agent a_backend \
                 --title "Token bucket limiter" --confidence 0.85
gitagent propose --feature auth-rate-limiting --agent a_tests \
                 --title "Limiter tests" --confidence 0.9

# superagent reviews and marks decisions
gitagent proposals --feature auth-rate-limiting
gitagent show --feature auth-rate-limiting p_7f3a
gitagent accept --feature auth-rate-limiting p_7f3a
gitagent revise --feature auth-rate-limiting p_9b2c --feedback "add edge cases"
# ... a_tests re-proposes (a new pid is generated) ...
gitagent accept --feature auth-rate-limiting p_9b2c

# OPTIONAL: pre-flight apply to surface conflicts before committing
gitagent integrate --feature auth-rate-limiting

# consolidate into ONE commit on main, then reset
gitagent finalize --feature auth-rate-limiting \
                  --message "feat(auth): add rate limiting with tests"
# → 1 commit on main, .gitagent reset, no push
```

## Multi-feature in parallel

Features are identified by name, not by branch. `finalize` lands each feature
directly on `main` — no manual merges needed.

```bash
gitagent init

# === Feature A: auth rate limiting (no checkout needed) ===
gitagent start --feature auth-rate-limiting
gitagent spawn --feature auth-rate-limiting --id a_backend
# ... a_backend works, proposes, you accept, integrate, finalize ...
gitagent finalize --feature auth-rate-limiting --message "feat(auth): rate limiting"
# → 1 commit on main, .gitagent reset

# === Feature B: user profile (parallel) ===
gitagent start --feature user-profile
gitagent spawn --feature user-profile --id a_frontend
# ... a_frontend works, proposes, you accept, integrate, finalize ...
gitagent finalize --feature user-profile --message "feat(user): profile page"
# → 1 commit on main, .gitagent reset

# Both commits are on main. No git merge needed.
```

`gitagent status` (without `--feature`) shows every feature and its session state.
Use `--feature <name>` for the detail view of a single feature.

`integrate` resets the integration worktree to the **live** target branch before
applying proposals, so cross-feature conflicts surface immediately.

## Commands

| Command | Description |
| --- | --- |
| `init` | Create `.gitagent/` and add it to `.gitignore`. |
| `start --feature <name>` | Open a session for a feature. No branch is created; a detached integration worktree is prepared. |
| `status [--feature <name>] [--json]` | Show all features, or detail for one feature. |
| `list-features [--json]` | List every feature with session/proposal/agent counts. |
| `log [--json]` | Append-only audit trail (global across features, `log.jsonl`). |
| `abort --feature <name>` | Discard a feature's session: remove worktrees, reset `.gitagent`. |
| `spawn --feature <name> --id <id> [--role ...]` | Create an isolated **detached** worktree for a subagent (no branch in your repo). |
| `list-agents --feature <name> [--json]` | List agents in a feature's session. |
| `kill <id> --feature <name>` | Remove an agent's worktree. |
| `propose --feature <name> --agent <id> --title ...` | Capture worktree changes as a patch proposal. |
| `proposals --feature <name> [--json]` | List proposals + review state. |
| `show <id> --feature <name>` | Show a proposal's diff (color). |
| `diff <id> --feature <name>` | Print a proposal's raw diff (pipe-friendly, for LLMs). |
| `accept <id> --feature <name>` | Mark a proposal as accepted (decision only; does **not** apply). |
| `reject <id> --feature <name> [--reason ...]` | Reject a proposal (patch not applied). |
| `revise <id> --feature <name> --feedback ...` | Send a proposal back for another iteration. |
| `integrate --feature <name> [--json]` | Apply all accepted proposals; detect cross-feature conflicts via 3-way merge. |
| `finalize --feature <name> --message ... [--target main]` | Call `integrate` + produce one commit on the target branch + reset `.gitagent`. **Never pushes.** |
| `install-skill` | Install the bundled `gitagent` agent skill into `~/.agents/skills/gitagent`. |

## How it works

- **Branchless & decoupled supervisor**: all commands require `--feature <name>` (a logical key, not a git branch). gitagent never creates or switches branches in your repository — your local checkout is never disturbed and stays on `main`.
- **One feature = one session**: identified by name, stored in `.gitagent/features/<key>/`. No `ga/<name>` branch is ever created.
- **Isolation**: `git worktree add --detach` gives every agent its own `HEAD`, index and working tree (all detached, sharing the object store) — ideal for parallelism and impossible to pollute your refs.
- **Per-feature storage**: each feature gets its own directory under `.gitagent/features/<key>/`, holding its session, agents, proposals, integration worktree, and locks. The audit log (`log.jsonl`) is global and spans all features.
- **Proposals as patches**: `git -C <worktree> diff --cached <base_sha> --binary` is stored as `change.patch` and re-applied with `git apply --3way` on the integration worktree. Agent "commits" never pollute the final history.
- **Live cross-feature conflict detection**: `integrate` resets the integration worktree to the current state of the target branch (default `main`) before applying proposals. If another feature was already finalized on `main`, its changes are visible and 3-way merge conflicts surface immediately.
- **Single commit on `main`**: `finalize` uses a detached temp worktree on the target branch, squash-merges the integration worktree state, creates one commit, and updates the target ref via `update-ref` (under a per-feature lock so concurrent finalizes can't clobber each other). The user's checkout is untouched.
- **Conflicts**: a conflicting patch is marked `conflict` at `integrate` time — the session stays alive so the superagent can `revise`, fix the integration worktree manually (then re-`accept` + re-`integrate`), or `abort`.
- **Concurrency**: file locks (`fcntl`) guard review-state writes; `accept` is safe to race (it only marks).
- **Audit**: every event is appended to `.gitagent/log.jsonl` (parseable by another agent/LLM). Spans all features.
- **Dual output**: humans get Rich tables; `--json` and `diff` give raw, pipe-friendly output for orchestration.

## Layout

```
.gitagent/
├── features/
│   ├── <feature-key>/
│   │   ├── session.json          # active session: id, feature, base_sha, state
│   │   ├── agents/<agent-id>/
│   │   │   ├── meta.json         # role, worktree path, base_sha, state
│   │   │   └── worktree/         # git worktree for this agent
│   │   ├── proposals/<proposal-id>/
│   │   │   ├── manifest.json     # agent, base_sha, files, summary, confidence
│   │   │   ├── change.patch      # git diff of the proposal
│   │   │   └── review.json       # pending|accepted|integrated|rejected|revise|conflict + feedback
│   │   ├── integration/worktree/ # detached integration worktree under construction
│   │   └── locks/                # fcntl lockfiles for concurrent writes
│   └── ...
└── log.jsonl                     # append-only audit trail (global, all features)
```

Proposal states: `pending → accepted → integrated | conflict | revise | rejected`.
Session states: `open → integrating → finalized | aborted`.

## Installing the agent skill

`gitagent` ships with a **skill** so any AI agent can use it freely — it teaches another agent the full CLI surface, the decision/application split, the branchless multi-feature workflow, conflict resolution recipes, and the supervisor/coder/reviewer patterns.

The skill lives in the repo at `skills/gitagent/SKILL.md` (single file — opencode's skill system only reads `SKILL.md`) and gets installed into the local opencode skills directory so future agent sessions can load it.

### From a gitagent install (pipx / pip / uv tool)

```bash
gitagent install-skill
# → installs bundled skill to ~/.agents/skills/gitagent
# → re-run to refresh after upgrading gitagent
```

### From a git clone (developers / source installs)

```bash
git clone https://github.com/david-fm/gawt
cd gawt
make install-skill
# (equivalent to: bash scripts/install-skill.sh)
```

### Manual

```bash
cp -R skills/gitagent ~/.agents/skills/
```

The skill will be available to agents in **future** sessions. Re-run any of the above commands to refresh the installed copy.

## Design principle

> `finalize` **never** runs `git push`. It produces exactly one local commit on the target branch (`main`) and stops. gitagent never creates branches in your repository, so with multiple agents or multiple features there is no branch-switching, no `ga/` pollution, and no risk of an out-of-band commit landing on the wrong ref — everything converges on `main` through `finalize`.

## License

MIT © David Florez Mazuera
