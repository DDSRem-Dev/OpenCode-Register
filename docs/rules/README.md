# Development Rules Hub

This directory is the engineering source of truth for OpenCode Register.
`docs/architecture.md` defines product behavior and system goals; this rules
library defines how contributors implement, validate, review, and release it.

## Document Index

| No. | Document | Scope |
| --- | --- | --- |
| 01 | `01-project-overview.md` | Goals, boundaries, phases, and shared terms |
| 02 | `02-tech-stack.md` | Toolchain, dependency ownership, and version policy |
| 03 | `03-commands.md` | Authoritative development and verification commands |
| 04 | `04-design-patterns.md` | Allowed patterns and their trigger conditions |
| 05 | `05-architecture.md` | Module ownership, dependency flow, and process boundaries |
| 06 | `06-code-styles.md` | Python, Rust, and TypeScript coding standards |
| 07 | `07-naming-conventions.md` | File, symbol, suffix, and domain naming rules |
| 08 | `08-comment-styles.md` | Docstrings, comments, and technical documentation |
| 09 | `09-external-interfaces.md` | REST, WebSocket, Tauri IPC, and provider contracts |
| 10 | `10-data-and-persistence.md` | SQLite, encryption, config files, migration, import/export |
| 11 | `11-quality-and-security.md` | Tests, quality gates, complexity, security, human boundaries |
| 12 | `12-collaboration-and-release.md` | Git, PR, versioning, build, and release workflow |

## Reading Paths

### Python Backend Work

Read 01, 02, 03, 05, 06, 07, 08, and 11. Also read 09 for API work and 10
for storage, encryption, or configuration work.

### Rust And Tauri Work

Read 01, 02, 03, 05, 06, 07, 09, and 11.

### React And TypeScript Work

Read 01, 02, 03, 05, 06, 07, 09, and 11.

### Architecture And Review Work

Read 01, 04, 05, 09, 10, and 11.

### Git And Release Work

Read 03, 11, and 12.

## Authority Order

1. Explicit instructions from the user for the current task.
2. The nearest scoped `AGENTS.md`.
3. The engineering rules in this directory.
4. Product and system requirements in `docs/architecture.md`.
5. Current behavior proven by code, tests, and configuration.

If items 3, 4, and 5 conflict, stop work in the conflicting scope and request
direction. Do not preserve both behaviors through a compatibility shim or an
ambiguous implementation.

Last updated: 2026-07-25
