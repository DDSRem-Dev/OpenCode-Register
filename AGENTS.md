# AGENTS.md

This file is the primary instruction entry point for AI agents and automated
contributors working in this repository. Repository-local instructions take
precedence over generic conventions.

## Instruction Scope

- This file applies to the entire repository.
- A nested `AGENTS.md` adds stricter rules for its directory subtree.
- Product requirements live in `docs/architecture.md`; engineering rules live
  in `docs/rules/`.
- When requirements and implementation disagree, do not silently choose one.
  Report the exact conflict and ask which source is current.

## Required Pre-Flight Check

Before changing files:

1. Classify the task by domain using the mapping below.
2. Read every mapped rule document completely.
3. Inspect the current implementation and tests in the affected scope.
4. Verify the proposal against architecture, naming, comments, security, and
   quality gates.
5. Preserve unrelated user changes in a dirty worktree.

Do not start implementation until the required rules have been read.

## Task-To-Documentation Mapping

| Task domain | Required sources |
| --- | --- |
| Any implementation | `docs/rules/01-project-overview.md`, `02-tech-stack.md`, `03-commands.md` |
| Architecture or module boundaries | `docs/rules/04-design-patterns.md`, `05-architecture.md` |
| Python backend | `backend/AGENTS.md`, `06-code-styles.md`, `07-naming-conventions.md`, `08-comment-styles.md` |
| Rust or Tauri | `src-tauri/AGENTS.md`, `05-architecture.md`, `06-code-styles.md`, `07-naming-conventions.md` |
| React or TypeScript | `src/AGENTS.md`, `05-architecture.md`, `06-code-styles.md`, `07-naming-conventions.md` |
| HTTP, WebSocket, IPC, providers | `docs/rules/09-external-interfaces.md` |
| SQLite, encryption, configuration | `docs/rules/10-data-and-persistence.md` |
| Tests, review, security | `docs/rules/11-quality-and-security.md` |
| Git, PR, version, release | `docs/rules/12-collaboration-and-release.md` |
| Documentation | `docs/AGENTS.md`, `docs/rules/README.md` |

## Non-Negotiable Gates

- Respect the dependency flow documented in `05-architecture.md`; circular or
  upward dependencies are rejected.
- Use an established pattern only when its trigger condition in
  `04-design-patterns.md` is met. Do not add speculative abstractions.
- Python public classes, methods, and functions require Chinese docstrings in
  the exact format defined by `08-comment-styles.md`.
- Python public boundaries must be fully typed and must not expose `Any`, raw
  dictionaries, or unvalidated third-party payloads.
- Rust must pass formatting, compilation, Clippy, and relevant tests.
- TypeScript must remain strict, avoid unsafe casts, and pass tests and the
  production build.
- Never log, commit, screenshot, or expose passwords, API keys, OAuth tokens,
  cookies, encryption keys, or unmasked personal data.
- Human verification and payment boundaries defined by the architecture must
  never be automated or bypassed.
- Only use repository commands documented in `03-commands.md`.

## Change Discipline

- Keep changes within the requested scope and existing ownership boundaries.
- Prefer explicit code over clever code. Extract an abstraction only after a
  real second use or when a framework contract requires one.
- Update tests with behavior changes and update documentation when contracts,
  paths, architecture, persistence, or commands change.
- Generated lockfiles may only be changed by their package manager.
- Do not edit user configuration under home-directory OpenCode paths during
  tests. Use temporary directories and fixtures.

## Conflict Resolution

If a rule, product requirement, test, and current implementation disagree:

1. Stop edits in the conflicting scope.
2. Cite the conflicting files and relevant lines.
3. Explain the behavioral difference, not just the textual difference.
4. Ask the user which behavior is authoritative.

## Documentation Hub

Read `docs/rules/README.md` for the complete rule index and review paths.

Last updated: 2026-07-25
