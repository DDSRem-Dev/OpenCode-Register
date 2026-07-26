# React And TypeScript Agent Rules

This file applies to `src/` and supplements the repository root `AGENTS.md`.

## Required Reading

Read `docs/rules/03-commands.md`, `05-architecture.md`,
`06-code-styles.md`, `07-naming-conventions.md`,
`09-external-interfaces.md`, and `11-quality-and-security.md` before editing.

## Ownership

- `pages/` composes route-level user workflows.
- `components/` contains reusable, focused UI units.
- `services/` owns HTTP, WebSocket, and Tauri IPC adapters.
- UI code renders state and sends user intent; it does not implement backend
  workflow, storage, encryption, provider, or browser automation logic.

## Mandatory Frontend Rules

- TypeScript strict mode stays enabled. Do not use `any`, `@ts-ignore`, or
  unchecked double casts.
- Components and files use the naming rules in `07-naming-conventions.md`.
- Keep effects narrow and clean them up. Network requests require cancellation
  or stale-result protection where a component can unmount or retry.
- API payloads are represented by explicit types at the service boundary.
- Do not call `fetch` or Tauri `invoke` directly from pages or components;
  route calls through `services/`.
- Do not store secrets in browser storage, query strings, logs, or UI state
  longer than required for the current interaction.
- Every asynchronous screen provides loading, success, empty, and error states
  appropriate to the workflow.
- Use semantic HTML, accessible names, keyboard operation, and visible focus.
- Preserve the existing visual language; avoid unrelated redesigns.

## Required Verification

Run the frontend tests and production build in `docs/rules/03-commands.md`.
Verify changed layouts at desktop width and 390px width with no overlap or
horizontal overflow.
