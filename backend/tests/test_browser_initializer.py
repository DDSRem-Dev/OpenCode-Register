from pathlib import Path

import pytest

from browser.initializer import BrowserInitializationError, BrowserInitializationStatus, BrowserInitializer


@pytest.mark.anyio
async def test_browser_initializer_installs_binary_in_background(tmp_path: Path) -> None:
    """
    验证浏览器初始化在后台完成并进入就绪状态
    """

    calls = 0

    def install() -> str:
        """
        返回测试浏览器路径并记录调用次数

        :return str: 测试浏览器路径
        """

        nonlocal calls
        calls += 1
        return str(tmp_path / "chrome")

    initializer = BrowserInitializer(install)
    initializer.start()
    await initializer.wait_until_ready()

    assert calls == 1
    assert initializer.status == BrowserInitializationStatus.READY


@pytest.mark.anyio
async def test_browser_initializer_exposes_failure_and_allows_retry(tmp_path: Path) -> None:
    """
    验证浏览器初始化失败后保持稳定错误并允许显式重试
    """

    should_fail = True

    def install() -> str:
        """
        按测试状态返回路径或模拟下载失败

        :return str: 测试浏览器路径

        :raises RuntimeError: 测试指定初始化失败
        """

        if should_fail:
            raise RuntimeError("sensitive download detail")
        return str(tmp_path / "chrome")

    initializer = BrowserInitializer(install)
    with pytest.raises(BrowserInitializationError, match="浏览器初始化失败") as failure:
        await initializer.wait_until_ready()

    assert "sensitive download detail" not in str(failure.value)
    assert initializer.status == BrowserInitializationStatus.ERROR

    should_fail = False
    initializer.start(retry_failed=True)
    await initializer.wait_until_ready()

    assert initializer.status == BrowserInitializationStatus.READY
