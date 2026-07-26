# 03 · Commands

This file is the authoritative command inventory. Agents must not assume
unlisted scripts, global tools, or flags. Run commands from the repository root.

## 3.1 Initial Setup

```bash
npm install
uv sync --project backend --group dev
uv run --project backend python -m cloakbrowser install
uv run --project backend python scripts/build_backend.py --placeholder
```

The last command is required before any Cargo or `npm run tauri` command.
`tauri.conf.json` declares `externalBin`, and `tauri-build` verifies that the
sidecar path exists while running the build script, so a fresh clone fails
`cargo check` until the file is present. The placeholder is an empty file; the
Rust host rejects zero-length sidecars and falls back to the development
interpreter, so it never shadows a real backend.

The explicit CloakBrowser install pre-downloads its browser binary for Phase 3
GitHub registration. A first launch also downloads the binary automatically.
CI tests use the browser boundary and do not open live GitHub registration.

Cargo resolves Rust dependencies from `src-tauri/Cargo.lock` when a Cargo
command is first executed.

## 3.2 Local Development

Complete desktop application:

```bash
npm run tauri dev
```

Split frontend/backend debugging:

```bash
uv run --project backend python backend/main.py
npm run dev
```

Isolated manual testing that must not modify the user's real OpenCode files:

```bash
OPENCODE_REGISTER_SANDBOX_DIR=.opencode-register-sandbox uv run --project backend python backend/main.py
npm run dev
```

The same environment variable can be applied to `npm run tauri dev`. Sandbox
mode redirects the SQLite vault and all three OpenCode/OMO files beneath the
specified directory. It does not mock GitHub, DuckMail, OpenCode, or payment.

Health probe:

```bash
curl --fail --silent --show-error http://127.0.0.1:17891/api/health
```

## 3.3 Python Verification

Apply automatic fixes and formatting:

```bash
uv run --project backend ruff check backend --fix
uv run --project backend ruff format backend
```

Read-only pre-commit gates:

```bash
uv run --project backend ruff check backend
uv run --project backend ruff format backend --check
uv run --project backend mypy backend
uv run --project backend pytest
```

Build scripts under `scripts/` are gated with an explicit configuration path
because that directory has no `pyproject.toml` of its own; without `--config`
Ruff falls back to its defaults instead of the repository line length:

```bash
uv run --project backend ruff check --config backend/pyproject.toml scripts
uv run --project backend mypy --config-file backend/pyproject.toml scripts
```

Black, YAPF, autopep8, and isort are not allowed.

## 3.4 Rust Verification

```bash
cargo fmt --manifest-path src-tauri/Cargo.toml --all -- --check
cargo check --manifest-path src-tauri/Cargo.toml
cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path src-tauri/Cargo.toml
```

Apply formatting with:

```bash
cargo fmt --manifest-path src-tauri/Cargo.toml --all
```

## 3.5 Frontend Verification

```bash
npm test
npm run build
npm audit
```

UI changes also require desktop-width and 390px-width checks with no overlap,
horizontal overflow, or console warnings/errors. Verify actual interactions,
not only a static screenshot.

Regenerate platform icons after changing `src-tauri/icons/icon.svg`:

```bash
npm run tauri icon src-tauri/icons/icon.svg
```

## 3.6 Packaging

Freeze the Python backend into the Tauri sidecar binary, then build the desktop
bundle. The script derives the target triple from `rustc -vV` and writes
`src-tauri/binaries/backend-<target triple>`:

```bash
uv run --project backend python scripts/build_backend.py
npm run tauri build
```

`npm run package` runs both steps. Pass an explicit triple when the default is
not wanted:

```bash
uv run --project backend python scripts/build_backend.py --target x86_64-apple-darwin
```

PyInstaller cannot cross-compile. A triple must be built on a machine of that
architecture; the `--target` flag only names the produced file. Distributed
artifacts are unsigned and not notarized.

## 3.7 Full Pre-Commit Gate

Run in this order:

```bash
uv run --project backend ruff check backend
uv run --project backend ruff format backend --check
uv run --project backend ruff check --config backend/pyproject.toml scripts
uv run --project backend mypy backend
uv run --project backend mypy --config-file backend/pyproject.toml scripts
uv run --project backend pytest
npm test
npm run build
cargo fmt --manifest-path src-tauri/Cargo.toml --all -- --check
cargo check --manifest-path src-tauri/Cargo.toml
cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path src-tauri/Cargo.toml
git diff --check
```

Documentation-only work still runs `git diff --check` and validates all local
links and command references.

## 3.8 Git Synchronization And Push

```bash
git pull -p
git pull --rebase -p
git push origin HEAD
git push --force-with-lease origin HEAD
```

Plain `--force` is forbidden. Published history may be rewritten only with
`--force-with-lease` after confirming the remote has no newer work.

## 3.9 Pull Requests

```bash
gh pr create --title "feat(scope): summary" --body-file <path> --base main --head <branch>
gh pr list
gh pr view <number>
gh pr checkout <number>
```

Use a temporary file and `--body-file` for multiline pull-request bodies to
avoid shell-escaping corruption.

Last updated: 2026-07-25
