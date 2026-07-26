import pytest

from browser.models import GitHubPageResult, GitHubPageStatus
from engine.flow import FlowTransitionError
from engine.models import FlowStatus, FlowStepStatus, ManualInterventionReason
from tests.test_flow import (
    FailingCodeProvider,
    FakeEmailProvider,
    FakeGitHubRegistrationClient,
    create_test_flow,
)


@pytest.mark.anyio
async def test_flow_rejects_duplicate_start() -> None:
    """
    验证基础流程拒绝重复启动造成副作用
    """

    flow = create_test_flow([FakeEmailProvider()], FakeGitHubRegistrationClient())
    await flow.start()

    with pytest.raises(FlowTransitionError, match="pending_payment -> creating_email"):
        await flow.start()


@pytest.mark.anyio
async def test_flow_pauses_and_resumes_for_manual_verification() -> None:
    """
    验证未知验证状态暂停流程且用户确认后可恢复
    """

    browser = FakeGitHubRegistrationClient(
        [
            GitHubPageResult(
                status=GitHubPageStatus.MANUAL_REQUIRED,
                manual_reason=ManualInterventionReason.CAPTCHA,
            ),
            GitHubPageResult(status=GitHubPageStatus.COMPLETED),
        ]
    )
    flow = create_test_flow([FakeEmailProvider()], browser)

    paused = await flow.start()
    pending = await flow.resume()
    resumed = await flow.resume()

    assert paused.status == FlowStepStatus.NEED_MANUAL
    assert paused.session.status == FlowStatus.MANUAL_VERIFY
    assert paused.session.manual_intervention is not None
    assert paused.session.manual_intervention.reason == ManualInterventionReason.CAPTCHA
    assert pending.session.status == FlowStatus.PENDING_PAYMENT
    assert resumed.status == FlowStepStatus.DONE
    assert resumed.session.status == FlowStatus.DONE


@pytest.mark.anyio
async def test_flow_keeps_manual_state_when_page_is_still_blocked() -> None:
    """
    验证用户过早继续时流程保持人工介入状态且不重复注册
    """

    manual_result = GitHubPageResult(
        status=GitHubPageStatus.MANUAL_REQUIRED,
        manual_reason=ManualInterventionReason.UNKNOWN_BLOCK,
    )
    browser = FakeGitHubRegistrationClient([manual_result, manual_result])
    flow = create_test_flow([FakeEmailProvider()], browser)

    await flow.start()
    result = await flow.resume()

    assert result.status == FlowStepStatus.NEED_MANUAL
    assert result.session.status == FlowStatus.MANUAL_VERIFY


@pytest.mark.anyio
async def test_flow_submits_provider_code_without_exposing_it_in_snapshot() -> None:
    """
    验证流程自动提交邮箱验证码且不会在会话快照中暴露验证码
    """

    browser = FakeGitHubRegistrationClient(
        [
            GitHubPageResult(status=GitHubPageStatus.EMAIL_CODE_REQUIRED),
            GitHubPageResult(status=GitHubPageStatus.COMPLETED),
        ]
    )
    flow = create_test_flow([FakeEmailProvider()], browser)

    pending = await flow.start()
    result = await flow.resume()

    assert pending.session.status == FlowStatus.PENDING_PAYMENT
    assert result.status == FlowStepStatus.DONE
    assert browser.submitted_code == "12345678"
    assert "12345678" not in result.session.model_dump_json()


@pytest.mark.anyio
async def test_flow_rejects_resume_outside_manual_state() -> None:
    """
    验证非人工介入状态不能执行恢复操作
    """

    flow = create_test_flow([FakeEmailProvider()], FakeGitHubRegistrationClient())

    with pytest.raises(FlowTransitionError, match="当前流程不可恢复: idle"):
        await flow.resume()


@pytest.mark.anyio
async def test_email_code_failure_releases_browser_and_mailbox() -> None:
    """
    验证邮箱验证码失败会回收浏览器与临时邮箱资源
    """

    provider = FailingCodeProvider()
    browser = FakeGitHubRegistrationClient([GitHubPageResult(status=GitHubPageStatus.EMAIL_CODE_REQUIRED)])
    flow = create_test_flow([provider], browser)

    result = await flow.start()

    assert result.status == FlowStepStatus.ERROR
    assert result.session.error_code == "github_email_code_failed"
    assert provider.disposed_email == "phase2@example.test"
    assert browser.closed is True


@pytest.mark.anyio
async def test_browser_failure_releases_browser_and_mailbox() -> None:
    """
    验证浏览器失败会回收浏览器与临时邮箱资源
    """

    provider = FakeEmailProvider()
    browser = FakeGitHubRegistrationClient(
        [
            GitHubPageResult(
                status=GitHubPageStatus.ERROR,
                error_code="github_browser_failed",
                error_message="GitHub 注册页面操作失败",
            )
        ]
    )
    flow = create_test_flow([provider], browser)

    result = await flow.start()

    assert result.status == FlowStepStatus.ERROR
    assert provider.disposed_email == "phase2@example.test"
    assert browser.closed is True


@pytest.mark.anyio
async def test_pause_waits_for_safe_point_and_resumes_without_duplicate_email() -> None:
    """
    验证暂停请求在邮箱创建后生效并从同一邮箱继续注册
    """

    provider = FakeEmailProvider()
    browser = FakeGitHubRegistrationClient()
    flow = create_test_flow([provider], browser)

    requested = flow.request_pause()
    paused = await flow.start()

    assert requested.session.pause_requested is True
    assert paused.status == FlowStepStatus.NEED_MANUAL
    assert paused.session.status == FlowStatus.MANUAL_VERIFY
    assert paused.session.manual_intervention is not None
    assert paused.session.manual_intervention.reason == ManualInterventionReason.USER_PAUSED
    assert browser.started_email is None
    pending = await flow.resume()
    resumed = await flow.resume()
    assert pending.session.status == FlowStatus.PENDING_PAYMENT
    assert resumed.session.status == FlowStatus.DONE
    assert browser.started_email == "phase2@example.test"
