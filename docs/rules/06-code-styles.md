# 06 · Code Standards And Style

## 6.1 Shared Principles

- Prefer simple, explicit code over clever compression.
- Abstract only after a second real caller exists or a framework contract
  requires the abstraction.
- Functions have a hard limit of 100 lines and receive a split warning at 60.
- Files have a hard limit of 500 lines.
- Cyclomatic complexity must not exceed 12 per function.
- Dead code, commented-out implementations, ownerless TODO branches, and
  redundant compatibility shims are forbidden.
- Comments explain why, not what. See `08-comment-styles.md`.

## 6.2 Python Formatter And Linter

PEP 8 is the Python baseline. The repository rules below are stricter where
they differ.

Ruff owns both formatting and linting:

- double quotes;
- four-space indentation, no tabs;
- platform-native line endings;
- readability first, normally within approximately 120 characters;
- no Black, YAPF, autopep8, isort, or personal formatter configuration.

Mypy is the mandatory static type gate. basedpyright may be used only as an
auxiliary check and never replaces Mypy.

## 6.3 Python Import Order

```python
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from engine.events import FlowEvent
from storage.models import Account

from .base import EmailProvider
```

The groups are:

1. standard library;
2. third party;
3. internal cross-package absolute imports;
4. same-package relative imports.

Separate groups with one blank line. Wildcard imports are forbidden. Import
specific symbols instead of importing a long module and repeatedly selecting
members. Absolute imports expose cross-package dependencies; relative imports
express same-package cohesion.

## 6.4 Python Formatting And Control Flow

- Use parentheses for multiline continuation, never backslashes.
- Use one blank line between methods and two between top-level declarations.
- Prefer early returns over deeply nested conditions.
- Use `if`/`elif`/`else`; `match`/`case` is forbidden to keep control flow
  uniform.
- Use context managers for resources and explicit UTF-8 for text files.
- Shipped code must not use `print` or interactive `input`; use structured logs
  and the manual-intervention protocol.

## 6.5 Python Type Hints

Use `typing` module syntax uniformly:

```python
from typing import List, Optional, Tuple


def select_provider(
    priorities: List[str],
    preferred: Optional[str] = None,
) -> Tuple[int, str]:
    ...
```

Mandatory rules:

- Fully annotate every public function and method.
- Use `Optional[T]`, never `T | None`.
- Use `Union[A, B]`, never `A | B`.
- Use `List[T]`, `Dict[K, V]`, and `Tuple[T, ...]`; native generic syntax such
  as `list[T]`, `dict[K, V]`, and `tuple[T, ...]` is forbidden.
- `Any` is forbidden at boundaries. If unavoidable in a low-level crypto or
  parser helper, keep it private and explain why in a comment.
- Data-carrying structures use Pydantic models. Every field uses `Field` with a
  Chinese description.
- Raw dictionaries, SDK responses, `httpx.Response`, and database rows do not
  cross layer boundaries.

Docstring type labels use only the parent/container type. See
`08-comment-styles.md`.

## 6.6 Python Error Handling

Expected control flow uses typed status/result models. Exceptions are reserved
for programming errors, resource failures, and unrecoverable conditions.

```python
class StepStatusCode:
    """
    流程步骤状态码

    Attributes:
        SUCCESS: 步骤执行成功
        NEED_MANUAL: 需要用户人工处理
        ERROR: 步骤执行失败
    """

    SUCCESS = 0
    NEED_MANUAL = 100001
    ERROR = 999999
```

- Callers explicitly branch on expected status values.
- File, socket, process, or encryption failures may become contextual domain
  exceptions.
- Bare `except:` is forbidden.
- Catching `Exception` requires rethrowing, translating, logging sanitized
  context, or a comment proving that best-effort suppression is safe.
- API boundaries map failures to the response contract in
  `09-external-interfaces.md`.

## 6.7 Python Async And Resources

- FastAPI routes, WebSockets, CloakBrowser, and asynchronous providers may use
  `async` where their contracts require it.
- Never execute blocking I/O directly in an async function.
- Polling has a timeout, cancellation path, and reasonable backoff.
- Do not create untracked background tasks. Every task has a lifecycle owner
  and is reaped during shutdown.
- Create and close `httpx.AsyncClient`, CloakBrowser browser/context/page, and
  similar shared resources centrally rather than per loop or request.

## 6.8 Rust Structure And Formatting

- rustfmt is the only formatter. Clippy warnings are failures.
- Keep modules focused and public APIs small.
- Choose explicit loops or iterators based on readability, not novelty.
- Model state with enums, newtypes, and structs, not magic strings or loose
  tuples.
- Give shared mutable state one clear owner; keep lock scopes minimal and never
  hold a lock during blocking external work.
- Use `Arc` only for shared ownership and locks only for required interior
  mutability.

## 6.9 Rust Error Handling

- Recoverable paths return `Result<T, E>`.
- Runtime paths must not use `unwrap`, context-free `expect`, or `panic!`.
- `expect` is allowed only for a compile-time/framework invariant and states the
  invariant in its message or nearby documentation.
- Keep internal errors structured; map them to stable, sanitized payloads at
  the Tauri command boundary.
- Add operation context to process, path, and I/O failures without exposing
  secrets or sensitive path contents.
- Drop performs best-effort cleanup only; important cleanup also has an
  explicit shutdown path.

## 6.10 TypeScript And React Style

- Keep TypeScript strict mode enabled.
- Use double quotes, semicolons, two-space indentation, and trailing commas,
  matching current files.
- `any`, `@ts-ignore`, unsafe double casts, and unnecessary non-null assertions
  are forbidden.
- Exported functions declare return types; obvious local values may infer them.
- Use discriminated unions for UI/request states instead of contradictory
  boolean combinations.
- Never mutate props or perform side effects during render.
- Components render state and capture interaction; calls go through `services/`.

## 6.11 React State And Effects

- Compute derived values during render instead of duplicating them in state.
- Effects synchronize external systems, not ordinary derived data.
- Every subscription, timer, WebSocket, and request has cleanup or cancellation.
- Split large pages when state or interaction has an independent responsibility.
- Use stable business identifiers for list keys, never indexes in reorderable
  lists.
- User-facing errors are actionable; technical detail belongs in sanitized
  logs.
- Never store secrets or complete account records in browser storage.

## 6.12 CSS And UI

- Reuse established tokens, layouts, and component patterns.
- Constrain fixed-format UI dimensions so dynamic text/icons do not shift
  layout.
- Support the desktop minimum window and a 390px viewport.
- Text overlap, horizontal overflow, unreachable actions, and color-only state
  are release failures.
- Interactive elements need semantic roles, accessible names, visible focus,
  keyboard operation, and reasonable target sizes.

Last updated: 2026-07-25
