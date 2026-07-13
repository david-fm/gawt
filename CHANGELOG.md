# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Consolidated the bundled skill into a single `SKILL.md`**. The opencode skill system only reads `SKILL.md` (frontmatter + body); the previous companion `AGENTS.md` was never loaded. Removed `AGENTS.md` and merged its full content (roles, multi-feature loop, state machine, LLM JSON recipes, conflict recipes, subagent worktree how-to, concurrency, idempotency, mistakes, copy-paste recipes, programmatic use, on-disk layout, roadmap) into `SKILL.md` so the skill is self-contained.

## [0.2.0] - 2026-07-13

### Changed (BREAKING)
- **Multi-feature model**: a feature is now a git branch whose name starts with `ga/`. The current branch determines the active session. Two features in two branches run in parallel without colliding.
- **`start` no longer takes `--feature`**. The feature name is derived from the current branch (`ga/auth-rl` → `auth-rl`). `start` refuses to run on `main` / `master` / detached HEAD.
- **`finalize` lands the commit on the current feature branch**, not on `main`. The superagent merges feature branches into `main` with normal git (PR, `git merge --squash`, etc.). `gitagent` never touches `main`.
- **Storage layout**: per-feature state moved to `.gitagent/features/<key>/` (one subdirectory per feature). The audit log remains global at `.gitagent/log.jsonl`.

### Added
- New command `gitagent list-features [--json]` to inspect every feature branch and its session state.
- `Session` model gains `branch` and `feature_key` fields.
- New module `gitagent.feature` for branch-slug derivation (`ga/<name>` → safe directory key).
- 7 new tests in `tests/test_multi_feature.py` covering parallel features, isolation, branch-preservation, and the `start`-on-main guard.
- README and `SKILL.md` updated with the multi-feature workflow and revised "where things live" layout.

### Migration from v0.1.0
- `gitagent start --feature "x"` → `git checkout -b ga/x && gitagent start`.
- After `finalize`, manually merge the feature branch to `main`:
  `git checkout main && git merge --squash ga/x && git commit -m "..."`.
- Per-feature worktrees now live under `.gitagent/features/<key>/agents/<id>/worktree` (instead of `.gitagent/agents/<id>/worktree`).

## [0.1.0] - 2026-07-07

### Added
- Initial public release.
- `git worktree`-based agent isolation under `.gitagent/`.
- Session lifecycle: `init`, `start`, `status`, `log`, `abort`.
- Agent management: `spawn`, `list-agents`, `kill`.
- Proposals as patch + manifest: `propose`, `proposals`, `show`, `diff`.
- Superagent decisions: `accept`, `reject`, `revise`, `integrate` (with `git apply --3way` conflict detection).
- `finalize` producing a single squashed commit on the current branch and resetting `.gitagent` (never pushes).
- `--json` output on `status`, `log`, `list-agents`, `proposals`, `integrate` for LLM/orchestrator consumption.
- Append-only audit trail at `.gitagent/log.jsonl`.
- CI (ruff + pytest on Python 3.11–3.13) and trusted-publishing release workflow.
- Bundled `gitagent` agent skill (installable via `gitagent install-skill` or `make install-skill`).

### Notes
- The PyPI distribution is published as **`gawt`** (not `gitagent`) because the
  name `gitagent` on PyPI is already taken by an unrelated project (a Tornado
  HTTP webhook server, last released 2016). The installed command is still
  `gitagent`. Install with `pipx install gawt` / `uv tool install gawt`.
