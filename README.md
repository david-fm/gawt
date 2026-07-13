# gitagent

> Agent workspace manager over Git: isolate, review and consolidate multi-agent changes into a **single commit per feature**.

`gitagent` is a lightweight CLI that wraps `git worktree` so a **superagent** can spawn isolated **subagents** per feature branch, collect their work as **proposals** (patch + manifest), accept/reject/revise them, and finally produce **one clean commit** on each feature branch — then clean everything up. It never pushes.

The headline feature: **multiple features in parallel**. Create one git branch per feature (`ga/<name>`), run `gitagent start` on each, and switch between them. Each feature keeps its own session, agents, proposals, and integration branch. The superagent merges each finished feature branch into `main` with normal git.

Two planes are kept strictly separate:

- **`.git`** — the source of truth. Untouched until `finalize`, which writes exactly one commit per feature branch.
- **`.gitagent/`** — an ephemeral coordination layer, one subdirectory per feature: worktrees, patches, manifests, decisions, audit log. Discardable and reset after `finalize`/`abort`.

`gitagent` does **not** reimplement Git. Proposals are stored as `git diff` patches plus JSON metadata, never as a second history.

## Decision / application split (Option B)

`gitagent` cleanly separates **deciding** from **applying**:

| Command | Role |
|---|---|
| `accept <pid>` | Record the decision "approved". Does **not** apply. |
| `reject <pid>` | Record "rejected". |
| `revise <pid>` | Send back to the agent (new `pid` on re-propose). |
| `integrate` | **Apply** all accepted proposals onto the integration branch. Detects conflicts. |
| `finalize` | Calls `integrate` (if needed) + creates **one** commit on the current feature branch. |

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

## Quick start (Option B, single feature)

```bash
gitagent init
git checkout -b ga/auth-rate-limiting
gitagent start

# each subagent gets an isolated worktree + ephemeral branch
gitagent spawn --id a_backend --role "implement limiter"
gitagent spawn --id a_tests   --role "write tests"

# subagents work in their own folders, then propose (no commit to .git)
gitagent propose --agent a_backend --title "Token bucket limiter" --confidence 0.85
gitagent propose --agent a_tests   --title "Limiter tests"        --confidence 0.9

# superagent reviews and marks decisions
gitagent proposals
gitagent show p_7f3a
gitagent accept p_7f3a
gitagent revise p_9b2c --feedback "add edge cases"
# ... a_tests re-proposes (a new pid is generated) ...
gitagent accept p_9b2c

# OPTIONAL: pre-flight apply to surface conflicts before committing
gitagent integrate

# consolidate into ONE commit on the feature branch, then reset
gitagent finalize --message "feat(auth): add rate limiting with tests"
# → 1 local commit on ga/auth-rate-limiting, .gitagent reset, no push
```

## Multi-feature in parallel

The rule: **one git branch per feature**, prefixed `ga/`. Switch branches to switch
sessions. The superagent runs each feature to completion, then merges to `main`
with normal git.

```bash
gitagent init

# === Feature A: auth rate limiting ===
git checkout -b ga/auth-rate-limiting main
gitagent start
gitagent spawn --id a_backend
# ... a_backend works, proposes, you accept, integrate, finalize ...
gitagent finalize --message "feat(auth): rate limiting"
# → 1 commit on ga/auth-rate-limiting

# === Feature B: user profile (parallel) ===
git checkout main
git checkout -b ga/user-profile main
gitagent start
gitagent spawn --id a_frontend
# ... a_frontend works, proposes, you accept, integrate, finalize ...
gitagent finalize --message "feat(user): profile page"
# → 1 commit on ga/user-profile

# === Superagent merges features to main (plain git) ===
git checkout main
git merge --squash ga/auth-rate-limiting && git commit -m "feat(auth): rate limiting"
git merge --squash ga/user-profile      && git commit -m "feat(user): profile page"
# OR open two PRs and merge through your normal review flow.
```

`gitagent list-features` shows every feature branch and its session state
(agents, proposals, integration branch). Two features in two branches never
collide: each one writes to its own `.gitagent/features/<key>/` directory.

`gitagent start` **refuses to run on `main`** — it requires a `ga/<name>`
branch. The feature name shown in `status` is derived from the branch.

## Commands

| Command | Description |
| --- | --- |
| `init` | Create `.gitagent/` and add it to `.gitignore`. |
| `start` | Open a session on the current feature branch (`ga/...`). |
| `status [--json]` | Show session, agents, proposals, integration state, known features. |
| `list-features [--json]` | List every feature branch with session/proposal/agent counts. |
| `log [--json]` | Append-only audit trail (global across features, `log.jsonl`). |
| `abort` | Discard the current feature's session: remove worktrees, reset `.gitagent`. The feature branch is preserved. |
| `spawn --id <id> [--base <ref>] [--role ...]` | Create an isolated worktree for a subagent. |
| `list-agents [--json]` | List agents in the current feature. |
| `kill <id>` | Remove an agent's worktree and ephemeral branch. |
| `propose --agent <id> --title ... [--summary ...] [--confidence 0..1]` | Capture worktree changes as a patch proposal. |
| `proposals [--json]` | List proposals + review state. |
| `show <id>` | Show a proposal's diff (color). |
| `diff <id>` | Print a proposal's raw diff (pipe-friendly, for LLMs). |
| `accept <id>` | Mark a proposal as accepted (decision only; does **not** apply). |
| `reject <id> [--reason ...]` | Reject a proposal (patch not applied). |
| `revise <id> --feedback ...` | Send a proposal back for another iteration. |
| `integrate [--json]` | Apply all accepted proposals onto the integration branch; detect conflicts. |
| `finalize --message ... [--sign] [--no-reset]` | Call `integrate` + produce one commit on the current feature branch + reset `.gitagent`. **Never pushes.** |
| `install-skill` | Install the bundled `gitagent` agent skill into `~/.agents/skills/gitagent`. |

## How it works

- **One feature branch = one session.** A feature branch is any branch whose name starts with `ga/` (e.g. `ga/auth-rate-limiting`). The current branch determines the active session. To work on multiple features, create one branch per feature and `git checkout` between them.
- **Isolation**: `git worktree add` gives every agent its own `HEAD`, index and working tree while sharing the object store — ideal for parallelism.
- **Per-feature storage**: each feature gets its own directory under `.gitagent/features/<key>/`, holding its session, agents, proposals, integration worktree, and locks. The audit log (`log.jsonl`) is global and spans all features.
- **Proposals as patches**: `git -C <worktree> diff --cached <base_sha> --binary` is stored as `change.patch` and re-applied with `git apply --3way` on the integration worktree. Agent "commits" never pollute the final history.
- **Integration order = proposal creation order**: the first agent to `propose` integrates first. Plan who proposes first if their change is the "base" others depend on.
- **Single commit per feature**: `finalize` does `git merge --squash <integration-branch>` on the current feature branch and creates exactly one commit, then removes all worktrees/branches and resets `.gitagent/features/<key>/` (the audit log is kept). The feature branch itself is preserved — that's the commit you merge to `main`.
- **Landing on `main`**: `gitagent` never touches `main`. Merge feature branches with `git merge --squash`, PRs, or whatever your team uses.
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
│   │   │   ├── meta.json         # role, worktree path, ephemeral branch, state
│   │   │   └── worktree/         # git worktree for this agent
│   │   ├── proposals/<proposal-id>/
│   │   │   ├── manifest.json     # agent, base_sha, files, summary, confidence
│   │   │   ├── change.patch      # git diff of the proposal
│   │   │   └── review.json       # pending|accepted|integrated|rejected|revise|conflict + feedback
│   │   ├── integration/worktree/ # integration branch under construction
│   │   └── locks/                # fcntl lockfiles for concurrent writes
│   └── ...
└── log.jsonl                     # append-only audit trail (global, all features)
```

Proposal states: `pending → accepted → integrated | conflict | revise | rejected`.
Session states: `open → integrating → finalized | aborted`.

## Installing the agent skill

`gitagent` ships with a **skill** so any AI agent can use it freely — it teaches another agent the full CLI surface, the Option B decision/application split, the multi-feature branch model, conflict resolution recipes, and the supervisor/coder/reviewer patterns.

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

> `finalize` **never** runs `git push`. It produces a local commit on the **feature branch** and stops. The superagent merges feature branches into `main` with normal git (squash, PR, whatever the team uses).

## License

MIT © David Florez Mazuera
