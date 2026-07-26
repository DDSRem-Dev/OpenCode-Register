from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from engine.models import FlowSession, FlowStatus


class FlowEventName(StrEnum):
    """
    流程事件名称

    Attributes:
        FLOW_SNAPSHOT: 流程权威快照
        MANUAL_INTERVENTION_REQUIRED: 请求用户人工介入
        FLOW_COMPLETED: Phase 4 流程已完成
        FLOW_FAILED: 流程执行失败
        FLOW_CANCELLED: 流程已取消
    """

    FLOW_SNAPSHOT = "flow_snapshot"
    MANUAL_INTERVENTION_REQUIRED = "manual_intervention_required"
    FLOW_COMPLETED = "flow_completed"
    FLOW_FAILED = "flow_failed"
    FLOW_CANCELLED = "flow_cancelled"


class FlowEvent(BaseModel):
    """
    WebSocket 流程事件
    """

    model_config = ConfigDict(extra="forbid")

    event: FlowEventName = Field(..., description="稳定事件名称")
    version: Literal[1] = Field(default=1, description="事件结构版本")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="事件 UTC 时间")
    flow_id: str = Field(..., description="流程唯一标识")
    payload: FlowSession = Field(..., description="不包含凭据和验证码的流程快照")


def create_flow_event(session: FlowSession, is_initial: bool = False) -> FlowEvent:
    """
    根据流程状态创建稳定事件

    :param session (FlowSession): 当前流程会话快照
    :param is_initial (bool): 是否为连接后的初始权威快照

    :return FlowEvent: 可安全发送的类型化事件
    """

    event_name = FlowEventName.FLOW_SNAPSHOT
    if not is_initial:
        if session.manual_intervention is not None:
            event_name = FlowEventName.MANUAL_INTERVENTION_REQUIRED
        elif session.status == FlowStatus.DONE:
            event_name = FlowEventName.FLOW_COMPLETED
        elif session.status == FlowStatus.ERROR:
            event_name = FlowEventName.FLOW_FAILED
        elif session.status == FlowStatus.CANCELLED:
            event_name = FlowEventName.FLOW_CANCELLED
    return FlowEvent(event=event_name, flow_id=session.flow_id, payload=session)
