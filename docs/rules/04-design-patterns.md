# 04 · Design Patterns

Patterns solve demonstrated structural problems. They are not decoration and
may be introduced only when the trigger conditions below are met.

## 4.1 Provider Strategy

**Use when:** multiple temporary-email or replaceable external services share a
stable contract.

- Put the abstraction in `providers/base.py`.
- Put implementations in `providers/integrations/`.
- Put selection in the factory; workflows depend only on the abstraction.
- A single implementation may retain this interface because the architecture
  explicitly requires providers, but do not add extra abstraction layers.

## 4.2 Factory

**Use when:** configuration, priority, or runtime availability selects an
implementation.

- A factory selects and constructs; it does not own provider behavior.
- Unknown types produce an explicit configuration error.
- Never dynamically import an unapproved module from an arbitrary string.

## 4.3 Workflow State Machine

**Use when:** a long-running workflow can pause, resume, cancel, or fail.

- Model states and transitions explicitly.
- Validate source and destination states for every transition.
- Record intent before a side effect and record the result after it completes.
- Resume operations are idempotent and cannot duplicate payment, creation, or
  config writes.
- Manual intervention is a first-class state, not an exception fallback.

## 4.4 Observer And Event Stream

**Use when:** workflow state, logs, or manual requests are pushed to consumers.

- Events are validated typed models.
- Event names express completed facts or explicit requests, such as
  `step_completed` and `manual_intervention_required`.
- A WebSocket disconnect never changes authoritative workflow state.
- Events never include passwords, API keys, verification codes, or unmasked
  third-party payloads.

## 4.5 Repository

**Use when:** persistence queries are reused across services or SQLite details
must be isolated.

- Return domain/storage models, not cursors, rows, or raw SQL results.
- Keep transaction boundaries as small as the business invariant allows.
- Do not build a generic repository framework around one simple query.

## 4.6 Manager And Singleton

**Use when:** a browser process, sidecar, scheduler, or similar resource needs
one lifecycle owner.

- Use singleton ownership only for a truly unique resource.
- Define create, status, close, repeated-call, and failure-recovery semantics.
- Tests must be able to replace or isolate the owner.

## 4.7 Adapter

**Use when:** third-party payloads, Tauri IPC, or browser APIs differ from
internal models.

- Parse and convert immediately at the boundary.
- Raw third-party objects never enter engine, UI, or storage layers.
- Do not silently ignore a contract-breaking payload; fail with sanitized
  context.

## 4.8 Forbidden Patterns

- Generic helpers, services, or interfaces with one caller and no framework
  requirement.
- Prebuilt abstraction layers for speculative future work.
- Global service locators or cross-layer import shortcuts.
- Exceptions used for expected workflow states.
- Raw dictionaries, JSON strings, or `Any` used as lasting module contracts.

Last updated: 2026-07-25
