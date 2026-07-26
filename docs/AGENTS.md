# Documentation Agent Rules

This file applies to `docs/`.

- `architecture.md` is the product and system architecture source of truth.
- `rules/` defines how contributors implement and validate that architecture.
- Write all AI-facing `AGENTS.md` and `docs/rules/` documents in English.
  Product or user-facing documents may use Chinese when consistent with their
  existing audience. Keep commands, identifiers, paths, and protocol names exact.
- Never document speculative behavior as implemented. Label proposed behavior
  explicitly.
- Update cross-references whenever files are renamed or responsibilities move.
- Examples must use this repository's terminology and sanitized placeholder
  data. Never include real credentials, account data, cookies, tokens, or local
  user paths.
- Architecture changes require a rationale, affected boundaries, migration
  impact, test impact, and rollback considerations.
- Rule changes must update `rules/README.md` and the relevant task mapping in
  the root `AGENTS.md` when scope changes.
