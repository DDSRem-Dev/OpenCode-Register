# Python Backend Agent Rules

This file applies to `backend/` and supplements the repository root
`AGENTS.md`.

## Required Reading

Before editing backend code, read:

- `docs/rules/05-architecture.md`
- `docs/rules/06-code-styles.md`
- `docs/rules/07-naming-conventions.md`
- `docs/rules/08-comment-styles.md`
- `docs/rules/09-external-interfaces.md`
- `docs/rules/11-quality-and-security.md`

Read `10-data-and-persistence.md` for storage, secrets, configuration, export,
or migration work.

## Mandatory Python Rules

- Ruff owns formatting and linting. Do not introduce Black, YAPF, autopep8,
  isort, or a second formatter.
- Use four spaces, double quotes, explicit imports, and the import grouping
  defined in `06-code-styles.md`.
- Use `typing` generics uniformly: `List`, `Dict`, `Tuple`, `Optional`,
  `Union`, and `Generator`. Native generic syntax and `T | None` are forbidden.
- Fully annotate every public function and method.
- Every public class, method, function, route handler, provider contract, and
  service entry point requires a Chinese docstring.
- Every Pydantic field requires `Field(..., description="<Chinese description>")`.
- Expected flow outcomes use typed status/result models. Exceptions are for
  broken invariants, resource failures, and unrecoverable errors.
- Never allow secrets or unvalidated provider payloads to escape a boundary.
- Async code is allowed where FastAPI, CloakBrowser, polling, or WebSocket
  contracts require it. Never hide blocking I/O inside an async function.

## Backend Boundaries

- `api/` translates HTTP/WebSocket contracts and delegates work.
- `engine/` owns workflow state and orchestration, not transport details.
- `browser/` owns CloakBrowser selectors and browser operations.
- `providers/` owns provider interfaces and integrations.
- `storage/` owns database, migrations, encryption, and repositories.
- `scheduler/` owns scheduled triggers and delegates business work.
- `config/` owns typed settings and defaults.

Routes must not access SQLite, CloakBrowser, or provider implementations
directly.

## Required Verification

Run the Python commands listed in `docs/rules/03-commands.md`. Tests must use
temporary storage and fake external boundaries; live account creation,
verification, payment, and destructive account operations are never automated
tests.
