# 01 · Project Overview

## 1.1 Purpose

OpenCode Register is a local desktop application for a personal learning
environment. It manages multiple user-authorized OpenCode accounts, reduces
repetitive setup work, and maintains local account-pool configuration.

The core workflow is:

```text
temporary email -> GitHub registration -> OpenCode login -> payment page ->
manual payment -> API key retrieval -> local account pool
```

`docs/architecture.md` is authoritative for complete product behavior.

## 1.2 Non-Negotiable Product Boundaries

- Payment is always completed by the user. Automation may navigate and wait.
- CAPTCHA, risk checks, phone verification, and unknown verification states
  must pause for the user.
- The application must not forge identities, bypass payment, or bypass a
  third-party security control.
- Passwords, API keys, OAuth data, and encryption material remain local and
  are handled only through approved security boundaries.
- Long-running automation must be pausable, cancellable, observable, and
  represented by explicit state.
- Third-party account deletion is destructive and requires verified target
  identity and explicit user intent.

Any implementation that weakens these boundaries must be rejected.

## 1.3 Product Delivery

The product capabilities described in `docs/architecture.md` are implemented.
When extending the product:

1. Derive explicit deliverables and evidence requirements.
2. Verify that prerequisite capabilities actually work.
3. Keep proposed behavior clearly unimplemented or disabled until delivered.
4. Do not use mock values in production UI to imply completion.

## 1.4 System Components

- **React UI:** renders state, captures user intent, and hosts manual work.
- **Tauri Rust host:** owns desktop lifecycle, permissions, paths, and the
  Python child process.
- **Python local service:** owns workflow, browser, providers, storage, and
  scheduling.
- **SQLite and config files:** hold local state, encrypted credentials, and
  OpenCode/OMO configuration.
- **External services:** GitHub, OpenCode, and temporary-email providers.

## 1.5 Definition Of Done

A task is complete only when:

- behavior satisfies the requirement and product boundaries;
- code resides in the correct ownership module;
- failure, cancellation, retry, and manual-pause paths are handled;
- tests match the change's risk and blast radius;
- language-specific format, lint, type, test, and build gates pass;
- changed contracts, commands, paths, versions, or architecture are documented;
- the worktree contains no credentials, generated noise, or unrelated edits.

Last updated: 2026-07-25
