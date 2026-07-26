from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from engine.events import FlowEventName
from engine.service import CreateAccountService, FlowNotFoundError


def create_websocket_router(service: CreateAccountService) -> APIRouter:
    """
    创建流程与人工介入 WebSocket 路由

    :param service (CreateAccountService): 账号创建流程生命周期服务

    :return APIRouter: 配置完成的 WebSocket 路由
    """

    router = APIRouter()

    @router.websocket("/flow/{flow_id}")
    async def flow_events(websocket: WebSocket, flow_id: str) -> None:
        """
        推送指定流程的权威快照与状态事件

        :param websocket (WebSocket): 当前 WebSocket 连接
        :param flow_id (str): 流程唯一标识

        :return None: 无返回值
        """

        await _stream_events(service, websocket, flow_id, manual_only=False)

    @router.websocket("/manual/{flow_id}")
    async def manual_events(websocket: WebSocket, flow_id: str) -> None:
        """
        推送指定流程的人工介入请求和终止状态

        :param websocket (WebSocket): 当前 WebSocket 连接
        :param flow_id (str): 流程唯一标识

        :return None: 无返回值
        """

        await _stream_events(service, websocket, flow_id, manual_only=True)

    return router


async def _stream_events(
    service: CreateAccountService,
    websocket: WebSocket,
    flow_id: str,
    manual_only: bool,
) -> None:
    await websocket.accept()
    try:
        async for event in service.events(flow_id):
            if manual_only and event.event not in {
                FlowEventName.FLOW_SNAPSHOT,
                FlowEventName.MANUAL_INTERVENTION_REQUIRED,
                FlowEventName.FLOW_FAILED,
                FlowEventName.FLOW_CANCELLED,
            }:
                continue
            await websocket.send_json(event.model_dump(mode="json"))
    except FlowNotFoundError:
        await websocket.close(code=4404, reason="flow_not_found")
    except WebSocketDisconnect:
        return
