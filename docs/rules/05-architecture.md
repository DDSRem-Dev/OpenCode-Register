# 05 · Architecture And Module Boundaries

## 5.1 Dependency Flow

```text
React pages/components
        |
        v
frontend services ----> Tauri commands ----> desktop lifecycle
        |
        v
FastAPI API/WebSocket
        |
        v
workflow engine
   |       |       |
   v       v       v
browser  providers  scheduler
   \       |       /
           v
        storage/config
```

Higher layers may depend on public contracts from lower layers. Lower layers
must not import higher layers. Cross-process calls use documented interfaces.

## 5.2 React Boundary

React owns pages, components, state presentation, accessible interaction, and
calls through `services/`. It does not own workflow decisions, CloakBrowser
selectors, provider selection, databases, encryption, config writes, or
long-lived secrets.

## 5.3 Tauri And Rust Boundary

Rust owns Python sidecar lifecycle, desktop permissions, windows, system paths,
packaging resources, and a small typed IPC surface. It does not own account
workflow, providers, browser automation, or account data.

Production packaging embeds the sidecar. Development-path launching must not be
presented as completed production packaging.

## 5.4 Python API Boundary

`api/` owns protocol parsing, validation, authentication when required, error
mapping, and delegation. Route handlers stay thin and never directly operate
SQLite, CloakBrowser, or provider implementations.

## 5.5 Engine Boundary

`engine/` is authoritative for workflow state, transitions, pause, resume,
cancel, and idempotency. It calls public browser/provider/storage contracts and
emits typed events. It does not know FastAPI requests, WebSocket connections,
React state, or SQL rows.

## 5.6 Browser Boundary

`browser/` encapsulates CloakBrowser lifecycle, page operations, and selectors.
Selectors exist only in this layer. Verification, unknown pages, and payment
return explicit manual-intervention results; they are never bypassed.

## 5.7 Provider Boundary

Providers create mailboxes, wait for codes, and release resources. They convert
third-party responses to internal models and never change workflow state or
write the database.

## 5.8 Storage And Config Boundary

`storage/` exclusively owns SQLite, migrations, transactions, field encryption,
and repositories. `config/` owns typed application settings. OpenCode/OMO config
writes use a dedicated adapter with backup, validation, atomic replacement, and
permission rules.

## 5.9 Scheduler Boundary

The scheduler decides when to trigger work and delegates actual quota or state
operations to services. Jobs are repeatable and cannot race with active work on
the same account.

## 5.10 Rejected Boundary Violations

- Backend code importing frontend or Tauri code.
- Rust parsing account business payloads or accessing SQLite.
- UI directly reading or writing user config files.
- Route handlers implementing workflow orchestration.
- Engine methods returning `httpx.Response`, CloakBrowser Page, SQL row, or
  FastAPI types.
- Broad `__init__` re-exports that hide a real cross-package dependency.

Last updated: 2026-07-25
