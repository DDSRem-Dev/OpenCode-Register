# 11 · Quality And Security

## 11.1 Acceptance Gates

All applicable gates must pass before a pull request is mergeable.

| Gate | Threshold | Command/source |
| --- | --- | --- |
| Python format | 100% | Ruff format check |
| Python lint | 0 errors | Ruff check |
| Python types | 0 errors | Mypy |
| Python tests | all pass | Pytest |
| Rust format | 100% | rustfmt check |
| Rust compile | success | Cargo check |
| Rust lint | 0 warnings | Clippy with `-D warnings` |
| Rust tests | all pass | Cargo test |
| TypeScript types/build | success | production build |
| Frontend tests | all pass | Vitest |
| Dependency audit | no unaccepted relevant vulnerabilities | npm/uv/Cargo audit process |
| Whitespace | no errors | `git diff --check` |

Exact commands are in `03-commands.md`.

## 11.2 Structural Thresholds

- Cyclomatic complexity: at most 12 per function.
- Function length: hard limit 100 lines, review warning at 60.
- File length: hard limit 500 lines.
- Duplicate code is flagged and resolved during review.
- No public boundary accepts `Any`, raw dictionaries, arbitrary JSON strings,
  or unvalidated third-party objects.
- No new top-level dependency without pull-request justification.

Generated framework files may exceed a threshold only when they are not hand
maintained and the exception is documented.

## 11.3 Test Strategy

### Python

- Unit-test workflow transitions, adapters, validation, crypto round trips,
  repositories, and failure mapping.
- Use fake providers and browser ports; do not mock internal implementation
  details when a public contract is available.
- Use temporary databases and config directories.
- Async tests set an explicit backend and do not rely on timing sleeps.

### Rust

- Unit-test command-independent lifecycle and path logic.
- Test repeated start/stop, crashed-child status, missing executable, and
  cleanup behavior.
- Platform-specific behavior gets target-gated tests where practical.

### Frontend

- Test service adapters and user-visible state transitions.
- Prefer accessible role/name queries.
- Cover loading, success, offline/error, manual intervention, cancellation, and
  retry for changed workflows.
- Visual checks cover desktop and 390px widths when layout changes.

### End-To-End

- Local process/health/IPC smoke tests may run automatically.
- Live GitHub registration, verification, payment, API-key extraction, and
  account deletion require controlled manual validation and are never CI
  automation.

## 11.4 Regression Requirements

- A bug fix includes a test that fails before the fix and passes after it.
- Contract changes test both producer and consumer.
- Persistence changes test migration and rollback/failure behavior.
- Cancellation and timeout paths are tested for long-running operations.
- A narrow test cannot be used as evidence for an untested cross-process flow.

## 11.5 Secret And Privacy Gate

Reject any change that can expose:

- GitHub passwords;
- OpenCode API keys;
- OAuth tokens/cookies;
- email verification codes;
- encryption keys, salts paired with plaintext, or master passwords;
- unmasked personal data;
- sensitive screenshots or raw third-party responses.

Scan staged changes before committing. Fixtures use conspicuously fake values.
Secret values must not implement revealing `repr`, Debug, or serialization by
default.

## 11.6 Human-Control Gate

The following always stop automation and request the user:

- CAPTCHA or risk-control challenge;
- phone or identity verification;
- unknown blocking verification;
- payment confirmation;
- destructive remote account deletion when explicit intent is not already
  established for the exact target.

Do not add CAPTCHA solvers, payment automation, stealth bypasses, identity
fabrication, or logic intended to evade third-party protections.

## 11.7 Network And Browser Security

- Allow only expected HTTPS hosts for external operations.
- Set finite timeouts, bounded retries, and cancellation.
- Validate redirect hosts before following sensitive flows.
- Treat DOM, URL, clipboard, and downloaded content as untrusted.
- Browser launch arguments and anti-detection settings must not weaken local
  security or bypass a verification control.
- Do not execute page-provided scripts outside the browser context.

## 11.8 Dependency And Release Security

- Lock all runtime dependencies.
- Review security advisories before release.
- A vulnerability affecting FastAPI/Uvicorn transport, CloakBrowser, encryption,
  SQLite handling, Tauri, or config writers blocks release unless a documented
  mitigation is approved.
- Avoid `npm audit fix --force` or equivalent blind breaking upgrades; update
  intentionally and rerun the full gate.
- Signed/notarized distribution work must be verified on its target platform.

## 11.9 Incident Response

If a credential, personal record, or encrypted export is exposed:

1. Stop affected processes and distribution.
2. Identify exact affected data and scope without spreading it further.
3. Revoke or rotate affected credentials.
4. Remove exposed artifacts from active distribution and preserve audit facts
   through an approved private channel.
5. Implement and test remediation.
6. Publish a sanitized post-mortem with a `chore(security):` change.
7. Backport the fix to supported release lines when distributed builds are
   affected.

Last updated: 2026-07-25
