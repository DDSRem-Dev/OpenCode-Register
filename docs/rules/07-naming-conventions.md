# 07 · Naming Conventions

Symbols are always English. Chinese is restricted to docstrings, logs, rule
examples that demonstrate Chinese documentation, and user-facing UI text.

## 7.1 Domain Vocabulary

| Term | Meaning | Preferred symbol |
| --- | --- | --- |
| Account | One locally managed authorized account | `Account`, `account_id` |
| Flow | High-level account creation or cleanup orchestration | `CreateAccountFlow` |
| Flow session | One pausable/resumable workflow instance | `FlowSession` |
| Step | One atomic workflow transition | `CreateEmailStep` |
| Manual intervention | An action the user must perform personally | `ManualIntervention` |
| Provider | Replaceable external-service adapter | `EmailProvider` |
| Browser client | CloakBrowser lifecycle and browser operations | `CloakBrowserClient` |
| Repository | Persistence query boundary | `AccountRepository` |
| Pool | OMO account fallback configuration | `AccountPool` |
| Quota | OpenCode quota state | `QuotaSnapshot` |
| Sidecar | Python process managed by Tauri | `PythonSidecar` |

Do not use `user` for GitHub user, OpenCode user, and local account
interchangeably. Include the exact origin, such as `github_username` and
`opencode_user_id`.

## 7.2 Python Naming

- Class: PascalCase, for example `FlowSession` and `AccountRepository`.
- Function/method: snake_case, for example `create_email`.
- Local variable: snake_case with complete words.
- Constant: UPPER_SNAKE_CASE, for example `DEFAULT_BACKEND_PORT`.
- Private member: one underscore prefix, for example `_browser_context`.
- Module singleton: lowercase and only for a truly unique owner.
- File: short lowercase `snake_case.py`.
- Directory/package: short lowercase without hyphens.

A direct package `__init__.py` may explicitly re-export public API and declare
`__all__`. Broad re-exports at `backend/` or high-level parent packages are
forbidden because they hide cross-layer dependencies.

## 7.3 Rust Naming

- Types, traits, and enum variants: PascalCase.
- Functions, methods, modules, and locals: snake_case.
- Constants/statics: SCREAMING_SNAKE_CASE.
- Tauri command names: snake_case and exactly equal to frontend invoke strings.
- Lifetimes stay short and idiomatic; business variables do not use arbitrary
  abbreviations.
- Boolean values use `is_`, `has_`, `can_`, or `should_` when applicable.

## 7.4 TypeScript And React Naming

- React components and component `.tsx` files: PascalCase.
- Hooks: `use` + PascalCase, for example `useFlowEvents`.
- Functions, variables, and service methods: camelCase.
- Types/interfaces: PascalCase. Prefer `type`; use `interface` for intentional
  declaration merging or extensible contracts.
- Module-local constants use camelCase; shared configuration constants use
  UPPER_SNAKE_CASE.
- Non-component `.ts` files use short camelCase names such as `flowEvents.ts`.
- CSS classes use kebab-case and clear state modifiers, not encoded DOM depth.
- Local event handlers use `handleX`; callback props use `onX`.

## 7.5 Suffix Rules

- `*Provider`: replaceable external capability contract or implementation.
- `*Client`: protocol, transport, or browser client.
- `*Service`: stateless use-case service.
- `*Repository`: persistence boundary.
- `*Flow`: high-level orchestration.
- `*Step`: atomic workflow step.
- `*Session`: runtime session with explicit start and end.
- `*Manager`: unique subsystem lifecycle owner, never a vague utility class.
- `*Request`/`*Response`: wire model.
- `*Event`: immutable event payload.
- `*Status`/`*StatusCode`: status enum or code collection.
- `*Settings`: application runtime configuration.
- `*ConfigWriter`: dedicated external-config writer.

A type gets one primary layer suffix. Names such as `FlowServiceManager` are
forbidden.

## 7.6 Forbidden Naming

- Unclear abbreviations such as `acct_mgr`, `cfg_svc`, or `tmp_obj`.
- Broad names such as `data`, `info`, `item`, or `handler` without narrow
  context.
- Repeating directory meaning in a symbol or filename.
- Names that misrepresent the actual source or ownership layer.
- Raw string identifiers where an existing enum/status/event applies.
- All-uppercase long acronyms. Treat acronyms over three characters as words,
  for example `HttpClient` and `ApiKeyResponse`.

Last updated: 2026-07-25
