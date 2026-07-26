from typing import Dict, List, Optional

import httpx
import pytest

from config.errors import ModelCatalogError
from config.model_catalog import MODELS_DEV_CATALOG_URL, OPENCODE_GO_MODELS_URL, OpenCodeGoModelClient
from config.models import OpenCodeModel


def official_payload(model_ids: List[str]) -> object:
    """
    创建符合官方源码契约的测试响应

    :param model_ids (List): 测试模型标识

    :return object: 官方列表形状的响应值
    """

    return {
        "object": "list",
        "data": [
            {"id": model_id, "object": "model", "created": 1784900000, "owned_by": "opencode"} for model_id in model_ids
        ],
    }


def catalog_payload(
    model_names: Dict[str, str],
    anthropic_model_ids: Optional[List[str]] = None,
    openai_model_ids: Optional[List[str]] = None,
) -> object:
    """
    创建 Models.dev OpenCode Go 元数据测试响应

    :param model_names (Dict): 模型标识与显示名
    :param anthropic_model_ids (List): 使用 Anthropic SDK 的模型标识
    :param openai_model_ids (List): 使用 OpenAI SDK 的模型标识

    :return object: Models.dev 目录形状的响应值
    """

    anthropic_ids = set(anthropic_model_ids or [])
    openai_ids = set(openai_model_ids or [])
    return {
        "opencode-go": {
            "id": "opencode-go",
            "name": "OpenCode Go",
            "npm": "@ai-sdk/openai-compatible",
            "api": "https://opencode.ai/zen/go/v1",
            "models": {
                model_id: {
                    "id": model_id,
                    "name": model_name,
                    **(
                        {"provider": {"npm": "@ai-sdk/anthropic"}}
                        if model_id in anthropic_ids
                        else ({"provider": {"npm": "@ai-sdk/openai"}} if model_id in openai_ids else {})
                    ),
                }
                for model_id, model_name in model_names.items()
            },
        }
    }


@pytest.mark.anyio
async def test_model_client_fetches_current_official_ids() -> None:
    """
    验证模型客户端只访问官方 HTTPS 端点并返回类型化列表
    """

    def respond(request: httpx.Request) -> httpx.Response:
        """
        返回测试官方目录

        :param request (Request): 模拟 HTTP 请求

        :return Response: 模拟官方响应
        """

        assert request.headers["Accept"] == "application/json"
        if str(request.url) == OPENCODE_GO_MODELS_URL:
            return httpx.Response(200, json=official_payload(["kimi-k2.7-code", "minimax-m3", "grok-4.5"]))
        assert str(request.url) == MODELS_DEV_CATALOG_URL
        return httpx.Response(
            200,
            json=catalog_payload(
                {"kimi-k2.7-code": "Kimi K2.7 Code", "minimax-m3": "MiniMax-M3", "grok-4.5": "Grok 4.5"},
                ["minimax-m3"],
                ["grok-4.5"],
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        models = await OpenCodeGoModelClient(http_client).fetch_models()

    assert models == [
        OpenCodeModel(model_id="kimi-k2.7-code", name="Kimi K2.7 Code"),
        OpenCodeModel(model_id="minimax-m3", name="MiniMax-M3", provider_npm="@ai-sdk/anthropic"),
        OpenCodeModel(model_id="grok-4.5", name="Grok 4.5", provider_npm="@ai-sdk/openai"),
    ]


@pytest.mark.anyio
async def test_model_client_rejects_duplicate_or_malformed_payload() -> None:
    """
    验证重复模型和未验证第三方字段不会进入配置层
    """

    responses = [
        official_payload(["kimi-k2.7-code", "kimi-k2.7-code"]),
        {"object": "list", "data": [{"id": "bad id", "object": "model"}]},
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        """
        依次返回无效官方响应

        :param request (Request): 模拟 HTTP 请求

        :return Response: 模拟无效响应
        """

        del request
        return httpx.Response(200, json=responses.pop(0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        client = OpenCodeGoModelClient(http_client)
        with pytest.raises(ModelCatalogError):
            await client.fetch_models()
        with pytest.raises(ModelCatalogError):
            await client.fetch_models()


@pytest.mark.anyio
async def test_model_client_sanitizes_upstream_failure() -> None:
    """
    验证官方端点失败不会暴露第三方响应正文
    """

    def respond(request: httpx.Request) -> httpx.Response:
        """
        返回包含不安全正文的服务故障

        :param request (Request): 模拟 HTTP 请求

        :return Response: 模拟服务故障
        """

        del request
        return httpx.Response(503, text="upstream internal details")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        with pytest.raises(ModelCatalogError, match="官方模型目录不可用") as error:
            await OpenCodeGoModelClient(http_client).fetch_models()

    assert "upstream internal details" not in str(error.value)


@pytest.mark.anyio
async def test_model_client_uses_valid_intersection_during_catalog_publish_lag() -> None:
    """
    验证 Models.dev 发布延迟时只返回双方均有元数据的模型
    """

    def respond(request: httpx.Request) -> httpx.Response:
        """
        返回来源不一致的模型目录

        :param request (Request): 模拟 HTTP 请求

        :return Response: 模拟目录响应
        """

        if str(request.url) == OPENCODE_GO_MODELS_URL:
            return httpx.Response(200, json=official_payload(["kimi-k2.7-code", "new-model"]))
        return httpx.Response(200, json=catalog_payload({"kimi-k2.7-code": "Kimi K2.7 Code"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        models = await OpenCodeGoModelClient(http_client).fetch_models()

    assert models == [OpenCodeModel(model_id="kimi-k2.7-code", name="Kimi K2.7 Code")]


@pytest.mark.anyio
async def test_model_client_rejects_empty_catalog_intersection() -> None:
    """
    验证双源模型目录没有交集时停止同步
    """

    def respond(request: httpx.Request) -> httpx.Response:
        """
        返回完全不相交的模型目录

        :param request (Request): 模拟 HTTP 请求

        :return Response: 模拟目录响应
        """

        if str(request.url) == OPENCODE_GO_MODELS_URL:
            return httpx.Response(200, json=official_payload(["official-only"]))
        return httpx.Response(200, json=catalog_payload({"catalog-only": "Catalog Only"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        with pytest.raises(ModelCatalogError, match="没有可用交集"):
            await OpenCodeGoModelClient(http_client).fetch_models()


@pytest.mark.anyio
async def test_model_client_rejects_unknown_model_sdk() -> None:
    """
    验证未知模型 SDK 不会进入配置层
    """

    def respond(request: httpx.Request) -> httpx.Response:
        """
        返回包含未知 SDK 的模型元数据

        :param request (Request): 模拟 HTTP 请求

        :return Response: 模拟目录响应
        """

        if str(request.url) == OPENCODE_GO_MODELS_URL:
            return httpx.Response(200, json=official_payload(["new-model"]))
        payload = catalog_payload({"new-model": "New Model"})
        assert isinstance(payload, dict)
        payload["opencode-go"]["models"]["new-model"]["provider"] = {"npm": "unknown-sdk"}
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        with pytest.raises(ModelCatalogError, match="元数据不可用"):
            await OpenCodeGoModelClient(http_client).fetch_models()
