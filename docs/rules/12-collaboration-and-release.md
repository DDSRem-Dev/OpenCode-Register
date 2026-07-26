# 12 · Collaboration, Versioning, Build, And Release

## 12.1 Branch Strategy

The repository uses a trunk plus short-lived branches model.

| Branch | Purpose | Lifetime |
| --- | --- | --- |
| `main` | Single production source; every commit is releasable | Permanent |
| `feature/<topic>` | New behavior | Delete after merge |
| `refactor/<topic>` | Behavior-preserving restructuring | Delete after merge |
| `fix/<topic>` | Bug fix against `main` | Delete after merge |
| `chore/<topic>` | Tooling, dependencies, or infrastructure | Delete after merge |
| `release/<x.y>` | Optional release stabilization | Delete after tag |

Branch topics use snake_case after the slash, for example
`feature/manual_intervention`.

The repository currently starts on `master`; before shared GitHub development,
rename the production branch to `main` so repository state matches this policy.

Operational rules:

- Direct large changes to `main` are prohibited; integrate through pull
  requests.
- Rebase on current `main` before merge.
- Keep history linear.
- Delete short-lived branches after merge.
- Do not mix unrelated refactors or formatting with a feature/fix.

## 12.2 Synchronization And History

- Use `git pull -p` to synchronize and prune stale remote-tracking branches.
- Use `git pull --rebase -p` on a feature branch to retain linear history.
- Push the current branch with `git push origin HEAD`.
- After an intentional rebase of a published branch, use
  `git push --force-with-lease origin HEAD`.
- Plain `--force`, destructive reset of shared work, and rewriting another
  contributor's commits are forbidden.
- Before rebasing or committing in a dirty worktree, preserve unrelated user
  changes and understand overlap.

## 12.3 Commit Messages

Use English Conventional Commits:

```text
<type>(<scope>): <imperative subject>
```

Allowed types:

- `feat`
- `fix`
- `refactor`
- `chore`
- `docs`
- `perf`
- `test`
- `build`
- `ci`
- `style`

The scope names the actual module or directory, such as `backend`, `engine`,
`storage`, `tauri`, `frontend`, `api`, or `docs`. The subject is imperative,
under 15 words, and has no trailing period.

Examples:

```text
feat(engine): add resumable flow transitions
fix(tauri): reap crashed backend process
docs(rules): define provider contract requirements
```

Each commit is coherent and passes relevant gates. Remove fixup noise before
merge.

## 12.4 Pull Request Requirements

A pull request is mergeable only when:

1. CI and all applicable local quality gates are green.
2. The branch is rebased on current `main`.
3. At least one maintainer approves it.
4. Tests cover changed behavior and regressions.
5. Documentation is updated for architecture, naming, persistence, interfaces,
   commands, or error taxonomy changes.
6. New dependencies are justified.
7. Security and misuse risk are assessed for automation behavior.
8. Commit history is clean and linear.

The PR body includes:

- summary and motivation;
- behavioral and architectural impact;
- verification commands and results;
- security/privacy/manual-boundary impact;
- migration and rollback notes when relevant;
- screenshots only for UI changes and only with sanitized data.

## 12.5 Reviewer Focus

Reviewers reject:

- scope creep and speculative abstractions;
- upward dependencies or business logic in UI/Rust/API adapters;
- missing Chinese Python docstrings or Pydantic descriptions;
- unsafe error handling or untyped boundaries;
- plaintext secrets, unsafe config writes, or sensitive logs;
- automated verification/payment bypass;
- narrow tests presented as proof of an untested end-to-end contract;
- noisy history and unrelated formatting churn.

## 12.6 Semantic Versioning

Use `MAJOR.MINOR.PATCH`:

- **MAJOR:** breaking API, IPC, config, database/export, CLI, or workflow
  contract change.
- **MINOR:** backwards-compatible feature.
- **PATCH:** bug fix, dependency update, or documentation-only release.

At release time synchronize all shipping version sources:

- root `package.json`;
- `backend/pyproject.toml`;
- `src-tauri/Cargo.toml`;
- `src-tauri/tauri.conf.json`;
- backend health/service version source.

Version drift is a release blocker. A future central version module may become
authoritative only through an explicit architecture change.

## 12.7 CI Pipeline

The intended CI order is:

1. Python format, lint, and Mypy.
2. Python tests.
3. Frontend tests and production build.
4. Rust format, check, Clippy, and tests.
5. Local cross-process smoke test.
6. Dependency/security checks.
7. Platform build matrix for packaged releases.

CI must use lockfiles and clean environments. It must never access real user
configuration, credentials, live registration/payment flows, or destructive
account endpoints.

## 12.8 Release Checklist

- [ ] Select the correct SemVer increment.
- [ ] Synchronize every version source.
- [ ] Run the full gate in `03-commands.md`.
- [ ] Audit runtime dependencies and document accepted risks.
- [ ] Verify schema/config/export compatibility and migrations.
- [ ] Review user-facing and architecture documentation for stale paths.
- [ ] Build and smoke-test each supported target platform.
- [ ] Verify Python sidecar/browser packaging.
- [ ] Verify macOS signing/notarization and Windows signing/Defender behavior.
- [ ] Generate a changelog from Conventional Commit subjects.
- [ ] Tag the exact reviewed commit and attach checksummed artifacts.

## 12.9 Changelog And Rollout

- Generate changelog sections by commit type.
- Publish only artifacts produced from the tagged commit.
- `.github/workflows/release.yml` creates the GitHub Release only after every
  target build succeeds and uploads target-qualified installers and checksum
  files so assets from different runners cannot collide.
- Include known limitations and migration instructions.
- Roll back by withdrawing affected artifacts and issuing a fixed version; do
  not silently replace an artifact under an existing tag.
- Any future gradual rollout mechanism requires an architecture and privacy
  review before implementation.

Last updated: 2026-07-25
