import warnings
from typing import AsyncGenerator, List

from fastapi import FastAPI

from api.websocket import create_websocket_router
from engine.events import FlowEvent, FlowEventName, create_flow_event
from engine.models import FlowSession, FlowStatus
from engine.service import CreateAccountService


class FakeEventService(CreateAccountService):
    """
    WebSocket 路由测试用事件服务
    """

    def __init__(self, events: List[FlowEvent]) -> None:
        """
        初始化预设事件服务

        :param events (List): 连接后依次发送的事件
        """

        self._test_events = events

    async def events(self, flow_id: str) -> AsyncGenerator[FlowEvent, None]:
        """
        发送与指定流程匹配的预设事件

        :param flow_id (str): 流程唯一标识

        :yields FlowEvent: 预设流程事件
        """

        for event in self._test_events:
            if event.flow_id == flow_id:
                yield event


def test_flow_websocket_sends_versioned_snapshot() -> None:
    """
    验证流程 WebSocket 首条消息符合版本化事件契约
    """

    session = FlowSession(status=FlowStatus.GITHUB_REGISTER)
    service = FakeEventService([create_flow_event(session, is_initial=True)])
    app = FastAPI()
    app.include_router(create_websocket_router(service), prefix="/ws")

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient` is deprecated")
        from starlette.testclient import TestClient

        with TestClient(app) as client:
            with client.websocket_connect(f"/ws/flow/{session.flow_id}") as websocket:
                message = FlowEvent.model_validate(websocket.receive_json())

    assert message.event == FlowEventName.FLOW_SNAPSHOT
    assert message.version == 1
    assert message.flow_id == session.flow_id
    assert message.payload.status == FlowStatus.GITHUB_REGISTER
