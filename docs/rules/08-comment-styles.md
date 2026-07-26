# 08 · Comments And Documentation Style

## 8.1 Mandatory Python Docstrings

Every public Python class, method, and function has a Chinese docstring. This
includes:

- FastAPI route handlers;
- public service, flow, step, and repository methods;
- provider interface and implementation methods;
- `@staticmethod` and `@classmethod` methods;
- public utility functions;
- Pydantic models.

Python code without the required docstring is rejected in review.

Private underscore-prefixed helpers may omit a docstring when behavior is
obvious. Private helpers with edge cases, algorithms, or side effects still
need one.

## 8.2 Python Docstring Template

The order is fixed: one-line summary, optional detail, parameters, return,
raises, and yields.

```python
"""
<中文摘要>

<可选中文详细说明>

:param <name> (<Type>): <中文说明>

:return <Type>: <中文说明>

:raises <ExceptionType>: <中文说明>
:yields <Type>: <中文说明>
"""
```

Rules:

- Use triple double quotes with opening and closing edges on separate lines.
- Parameter format: `:param name (Type): description`.
- Return format: `:return Type: description`.
- Raise format: `:raises ExceptionType: description`.
- Yield format: `:yields Type: description`.
- Separate summary, params, return, raises, and yields blocks with blank lines.
- Do not put usage examples in docstrings; place them in tests or `examples/`.

```python
from typing import List, Optional


async def wait_for_code(
    email: str,
    timeout: int,
    allowed_senders: Optional[List[str]] = None,
) -> str:
    """
    等待临时邮箱收到验证码

    在超时范围内轮询邮件，并只接受允许来源的有效验证码

    :param email (str): 临时邮箱地址
    :param timeout (int): 最大等待秒数
    :param allowed_senders (List): 可接受的发件人列表

    :return str: 收到的验证码

    :raises TimeoutError: 超时仍未收到有效验证码
    """
```

## 8.3 Docstring Type Labels

Docstring labels use only the parent/container type, stripping `Optional` and
inner generic parameters. Code annotations remain fully detailed.

| Code annotation | Docstring label |
| --- | --- |
| `Optional[List[str]]` | `List` |
| `Optional[int]` | `int` |
| `Dict[str, int]` | `Dict` |
| `Tuple[str, ...]` | `Tuple` |
| `Generator[int, None, None]` | `Generator` |
| `str`, `int`, `bool` | unchanged |

## 8.4 Attributes And Pydantic Fields

Do not place a multiline docstring beneath every enum variant, dataclass field,
or class attribute. Collect them in the parent class `Attributes:` section.

```python
from enum import Enum


class FlowStatus(Enum):
    """
    账号流程状态

    Attributes:
        IDLE: 尚未开始
        RUNNING: 正在执行
        NEED_MANUAL: 等待用户处理
        DONE: 已完成
    """

    IDLE = "idle"
    RUNNING = "running"
    NEED_MANUAL = "need_manual"
    DONE = "done"
```

Every Pydantic field has a Chinese `Field` description:

```python
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """
    本地服务健康状态响应
    """

    status: str = Field(..., description="服务状态")
    service: str = Field(..., description="服务名称")
    version: str = Field(..., description="服务版本")
```

## 8.5 Inline Comments

Default to no inline comments. Names and structure explain what; comments are
reserved for non-obvious why:

- unstable or counterintuitive third-party page/API behavior;
- security, privacy, concurrency, or compatibility trade-offs;
- platform/version workarounds with an explicit removal condition;
- why an automation boundary must pause for the user.

Do not write comments that:

- restate code;
- describe the current task, pull request, or reviewer handoff;
- preserve a removed implementation;
- use visual line separators to organize a file;
- include passwords, API keys, codes, real emails, or raw payloads.

## 8.6 Rust Documentation

- Use `///` for public crate APIs, Tauri command contracts, and non-obvious
  lifecycle types.
- Document invariants, ownership, failures, and cleanup rather than restating
  implementation.
- Comment private helpers only for non-obvious algorithms or platform fixes.
- Any future `unsafe` block requires a `// SAFETY:` explanation of preconditions
  and preserved invariants.

## 8.7 TypeScript Documentation

- Use concise JSDoc for exported service contracts, complex hooks, and
  non-obvious protocol adapters.
- Ordinary components do not need comments describing JSX structure.
- Props names/types express the contract; document only cross-field invariants.
- Workaround comments include the dependency version or removal condition.

## 8.8 Deprecation Markers

```python
# @deprecated: use AccountRepository.get_by_id instead; remove in 0.3.0.
```

Every marker identifies a replacement and removal milestone. Deprecated code
is removed within one minor release; Git history is the only archive.

## 8.9 Changelog Readability

Release notes are generated from commit subjects. Subjects must be clear enough
to publish verbatim; `update`, `fix issue`, and `changes` are unacceptable.

Last updated: 2026-07-25
