# 02 · Tech Stack

## 2.1 Current Toolchain

| Layer | Technology | Authoritative configuration |
| --- | --- | --- |
| Frontend | React, TypeScript, Vite, Vitest | `package.json`, `tsconfig.json`, `vite.config.ts` |
| Desktop | Tauri 2, Rust 2021 | `src-tauri/Cargo.toml`, `tauri.conf.json` |
| Backend | Python 3.11+, FastAPI, Uvicorn | `backend/pyproject.toml` |
| Python quality | Pytest, Ruff, Mypy | `backend/pyproject.toml` |
| Rust quality | rustfmt, Clippy, Cargo test | Rust toolchain, `Cargo.toml` |
| Frontend quality | TypeScript strict, Vitest, Testing Library | `tsconfig.json`, `package.json` |
| Package management | npm, uv, Cargo | Corresponding lockfiles |

PyInstaller is an active build-time dependency in the backend `dev` group since
Phase 8; it freezes the backend into the Tauri sidecar binary and never enters
the runtime dependency set.

CloakBrowser, SQLite, cryptography, APScheduler, and other planned dependencies
are added only when their architecture phase begins and a real caller exists. Do
not preinstall dependencies for speculative future work.

## 2.2 Lockfile Policy

- npm alone generates `package-lock.json`.
- uv alone generates `backend/uv.lock`.
- Cargo alone generates `src-tauri/Cargo.lock`.
- Never edit a lockfile manually.
- Dependency changes include both the manifest and lockfile.
- Pull requests explain purpose, alternatives, supply-chain risk, and package
  size impact for each new top-level dependency.
- Unbounded versions and unreviewed Git or local-path dependencies are banned.

## 2.3 Dependency Ownership

- Browser automation, provider SDKs, scheduling, and data dependencies belong
  to Python.
- Desktop permissions, windows, paths, and sidecar capabilities belong to Rust.
- Rendering, interaction, and frontend adapter dependencies belong to React.
- Do not implement the same capability in two runtimes.

## 2.4 New Dependency Gate

Before adding a top-level dependency, prove that:

1. The standard library and current dependencies cannot reasonably solve it.
2. It belongs to the selected runtime and ownership module.
3. Its license is compatible with distribution.
4. Maintenance activity, vulnerabilities, and transitive dependencies were
   reviewed.
5. The pull request documents its purpose and alternatives.

Last updated: 2026-07-25
