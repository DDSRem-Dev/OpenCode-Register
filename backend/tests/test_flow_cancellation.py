import asyncio

import pytest

from engine.models import AccountCompletionData, FlowStatus, FlowStepStatus
from tests.test_flow import FakeEmailProvider, FakeGitHubRegistrationClient, create_test_flow


@pytest.mark.anyio
async def test_flow_cancellation_disposes_mailbox_created_in_flight() -> None:
    """
    验证创建邮箱期间取消流程会释放随后创建的邮箱
    """

    create_gate = asyncio.Event()
    create_started = asyncio.Event()
    provider = FakeEmailProvider(create_gate, create_started)
    browser = FakeGitHubRegistrationClient()
    flow = create_test_flow([provider], browser)
    start_task = asyncio.create_task(flow.start())
    await create_started.wait()

    cancel_result = await flow.cancel()
    create_gate.set()
    start_result = await start_task

    assert cancel_result.status == FlowStepStatus.CANCELLED
    assert start_result.status == FlowStepStatus.CANCELLED
    assert provider.disposed_email == "phase2@example.test"
    assert browser.closed is True


@pytest.mark.anyio
async def test_task_cancellation_waits_for_mailbox_creation_and_disposes_it() -> None:
    """
    验证任务取消不会遗留已由远端创建的临时邮箱
    """

    create_gate = asyncio.Event()
    create_started = asyncio.Event()
    provider = FakeEmailProvider(create_gate, create_started)
    browser = FakeGitHubRegistrationClient()
    flow = create_test_flow([provider], browser)
    start_task = asyncio.create_task(flow.start())
    await create_started.wait()

    start_task.cancel()
    create_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await start_task
    await flow.cancel()

    assert provider.disposed_email == "phase2@example.test"
    assert browser.closed is True


@pytest.mark.anyio
async def test_task_cancellation_finishes_account_completion_atomically() -> None:
    """
    验证配置持久化开始后取消任务仍会完成账号并进入完成状态
    """

    completion_started = asyncio.Event()
    completion_gate = asyncio.Event()

    async def gated_completion(data: AccountCompletionData) -> str:
        del data
        completion_started.set()
        await completion_gate.wait()
        return "opencode-go2"

    provider = FakeEmailProvider()
    browser = FakeGitHubRegistrationClient()
    flow = create_test_flow(
        [provider],
        browser,
        completion_handler=gated_completion,
    )
    await flow.start()
    resume_task = asyncio.create_task(flow.resume())
    await completion_started.wait()

    resume_task.cancel()
    completion_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await resume_task

    snapshot = flow.snapshot()
    assert snapshot.status == FlowStatus.DONE
    assert snapshot.opencode_provider_name == "opencode-go2"
    assert provider.disposed_email == "phase2@example.test"
    assert browser.closed is True
