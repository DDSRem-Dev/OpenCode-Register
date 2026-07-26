# Rust And Tauri Agent Rules

This file applies to `src-tauri/` and supplements the repository root
`AGENTS.md`.

## Required Reading

Read `docs/rules/03-commands.md`, `05-architecture.md`,
`06-code-styles.md`, `07-naming-conventions.md`,
`09-external-interfaces.md`, and `11-quality-and-security.md` before editing.

## Ownership

The Rust host owns desktop lifecycle concerns only:

- start, inspect, and stop the Python sidecar;
- expose narrow, typed Tauri commands;
- resolve application paths and desktop permissions;
- manage process cleanup and packaging resources.

Do not move account workflow, provider, browser automation, persistence, or
quota logic into Rust.

## Mandatory Rust Rules

- Stable Rust and edition settings in `Cargo.toml` are authoritative.
- `cargo fmt` owns formatting. Clippy warnings are review failures.
- Public commands and cross-module types require concise `///` contract docs
  when their behavior is not obvious from the signature.
- Use typed request/response structs with Serde. Do not pass arbitrary JSON
  strings through IPC.
- Avoid `unwrap`, `expect`, and `panic!` in recoverable runtime paths.
- Convert internal errors at the command boundary without exposing secrets,
  filesystem internals, or raw child-process output.
- Shared mutable state must have one clear owner and poison/error handling.
- Child processes must be reaped on normal exit, restart, startup failure, and
  application shutdown.
- Platform-specific code must be isolated and compile-gated.

## Required Verification

Run the Rust commands in `docs/rules/03-commands.md`, plus the relevant
end-to-end desktop smoke test when lifecycle or IPC behavior changes.
