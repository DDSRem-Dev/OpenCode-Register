from datetime import UTC

from engine.events import FlowEventName, create_flow_event
from engine.models import FlowSession, FlowStatus, ManualIntervention, ManualInterventionReason


def test_initial_event_is_authoritative_snapshot() -> None:
    """
    验证新连接收到带 UTC 时间的权威流程快照
    """

    session = FlowSession(status=FlowStatus.GITHUB_REGISTER)

    event = create_flow_event(session, is_initial=True)

    assert event.event == FlowEventName.FLOW_SNAPSHOT
    assert event.payload == session
    assert event.timestamp.tzinfo == UTC


def test_manual_event_contains_no_secret_fields() -> None:
    """
    验证人工介入事件不包含密码或邮箱验证码字段
    """

    session = FlowSession(
        status=FlowStatus.MANUAL_VERIFY,
        screenshot_id="00000000-0000-4000-8000-000000000095",
        manual_intervention=ManualIntervention(
            reason=ManualInterventionReason.CAPTCHA,
            title="需要完成安全验证",
            instruction="请在浏览器中完成验证。",
        ),
    )

    event = create_flow_event(session)
    serialized = event.model_dump_json()

    assert event.event == FlowEventName.MANUAL_INTERVENTION_REQUIRED
    assert "password" not in serialized
    assert "verification_code" not in serialized
    assert '"screenshot_id":"00000000-0000-4000-8000-000000000095"' in serialized
    assert "data:image/png" not in serialized
    assert "base64" not in serialized


def test_payment_and_completion_events_match_flow_states() -> None:
    """
    验证付款人工状态与流程完成事件使用稳定名称
    """

    payment_session = FlowSession(
        status=FlowStatus.PENDING_PAYMENT,
        manual_intervention=ManualIntervention(
            reason=ManualInterventionReason.PAYMENT,
            title="等待手动支付",
            instruction="请在 OpenCode Go 页面完成支付。",
        ),
    )
    completed_session = FlowSession(status=FlowStatus.DONE, api_key_captured=True)

    assert create_flow_event(payment_session).event == FlowEventName.MANUAL_INTERVENTION_REQUIRED
    assert create_flow_event(completed_session).event == FlowEventName.FLOW_COMPLETED
