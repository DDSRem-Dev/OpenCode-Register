# 10 · Data And Persistence

## 10.1 Ownership

`backend/storage/` exclusively owns SQLite access, schema migrations, field
encryption, transactions, and repositories. Other modules call storage
contracts and never issue SQL or open database connections directly.

`backend/config/` owns typed application settings. A dedicated config adapter
owns OpenCode/OMO files outside the application database.

## 10.2 Schema And Migration Rules

- `docs/architecture.md` defines the initial logical schema.
- Every schema change uses a numbered, forward migration checked into source.
- Never mutate an existing released migration.
- Migrations run in a transaction where SQLite permits it.
- A migration is idempotently tracked and cannot partially apply unnoticed.
- Destructive changes require backup, compatibility analysis, and explicit
  rollback/recovery instructions.
- Repository tests start from an empty temporary database and verify upgrade
  from the previous supported schema when migrations exist.

## 10.3 Model Boundaries

- Database rows are converted immediately to typed storage/domain models.
- API response models are distinct from persistence models when their contracts
  differ.
- Timestamps are UTC and stored in an unambiguous ISO-8601 or database-native
  representation.
- Status values use a validated enum or constrained model.
- UUIDs are stable external identifiers; integer primary keys remain internal.

## 10.4 Transactions And Concurrency

- One business invariant defines one transaction boundary.
- Do not hold a database transaction across browser, network, or manual user
  waits.
- Account status transitions use compare-and-set/version checks where concurrent
  tasks can race.
- Configure SQLite busy timeout and foreign keys explicitly.
- Scheduler jobs and user actions must not update the same account silently.
- Failed writes roll back completely and surface a typed error.

## 10.5 Sensitive Fields

At minimum, GitHub passwords and OpenCode API keys are encrypted before they
reach SQLite. Future sensitive fields follow the same policy.

- Use `cryptography` and authenticated encryption (AES-GCM as designed).
- Derive encryption keys from the user master password with the documented KDF
  and a random salt.
- Generate a unique nonce for every encryption operation; never reuse it with
  the same key.
- Store ciphertext, nonce, salt/version metadata, and authentication tag in an
  unambiguous versioned format.
- Persist an authenticated verifier ciphertext so an empty initialized vault
  still rejects an incorrect master password; never persist the password or
  derived key as the verifier.
- Never store the master password or derived key on disk.
- Keep plaintext lifetime short and do not include it in exceptions, logs,
  events, screenshots, repr output, fixtures, or test snapshots.
- Use dummy secrets in tests and temporary storage only.

## 10.6 Settings

- Settings are represented by typed models with defaults and validation.
- Environment or file overrides are parsed once at startup.
- `OPENCODE_REGISTER_SANDBOX_DIR`, when set, takes precedence over every
  individual persistence path and redirects SQLite plus all OpenCode/OMO files
  beneath that directory.
- Paths expand user markers and resolve safely before use.
- Secret settings are excluded from model repr and logs.
- Unknown keys fail clearly unless an explicit forward-compatibility policy
  says otherwise.
- Runtime mutation is serialized and persisted atomically when supported.

## 10.7 OpenCode And OMO Config Writes

Before modifying `auth.json`, `opencode.json`, or `oh-my-openagent.json`:

1. resolve and validate the exact target path;
2. read and parse the file with a structured JSON parser;
3. validate relevant existing structure;
4. create a timestamped backup with restrictive permissions;
5. modify only owned keys while preserving unrelated user configuration;
6. write to a sibling temporary file;
7. flush and atomically replace the target;
8. parse and validate the final file;
9. restore the backup if replacement or validation fails.

Never edit these files with string concatenation. Tests use a temporary
directory and never touch a developer's real home configuration.

## 10.8 Import And Export

- Export packages use authenticated encryption, not password-protected ZIP
  alone.
- The manifest includes a format version and integrity metadata.
- Import validates file size, entry paths, schema version, and authentication
  before writing anything.
- Reject path traversal, symlinks, duplicate entries, oversized decompression,
  and unknown critical fields.
- Import is transactional: validate all records first, then commit.
- Duplicate account behavior is explicit and tested; never overwrite silently.

## 10.9 Logs And Screenshots

- Logs store stable account/flow IDs, level, step, safe message, and timestamp.
- Mask email local parts and all credentials before logging.
- Screenshots are opt-in, stored outside source/config directories, and never
  committed.
- Define retention and deletion behavior before persistent screenshot capture
  is enabled.
- Account deletion removes owned local records and associated screenshots only
  after the remote destructive operation reaches its documented result.

## 10.10 File Permissions And Backups

- Sensitive database, export, and backup files use the most restrictive
  practical user-only permissions on each platform.
- Backups have bounded retention and never enter version control.
- Failure messages may identify the logical config target but must not dump its
  contents.

Last updated: 2026-07-25
