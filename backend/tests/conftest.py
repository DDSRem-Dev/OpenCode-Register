import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_test_sandbox = tempfile.TemporaryDirectory(prefix="opencode-register-tests-")
os.environ.setdefault("OPENCODE_REGISTER_SANDBOX_DIR", _test_sandbox.name)


@pytest.fixture
def anyio_backend() -> str:
    """
    指定异步测试使用 asyncio 后端

    :return str: AnyIO 后端名称
    """

    return "asyncio"
