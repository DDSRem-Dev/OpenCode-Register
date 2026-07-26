from typing import List, Literal, Optional

import httpx
from pydantic import ValidationError

from config.errors import ModelCatalogError
from config.models import (
    ModelCatalogProviderOverride,
    OfficialModelList,
    OpenCodeGoCatalogProvider,
    OpenCodeModel,
)

OPENCODE_GO_MODELS_URL = "https://opencode.ai/zen/go/v1/models"
MODELS_DEV_CATALOG_URL = "https://models.dev/api.json"


class OpenCodeGoModelClient:
    """
    OpenCode Go 官方模型目录客户端
    """

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        """
        初始化官方模型目录客户端

        :param http_client (AsyncClient): 共享异步 HTTP 客户端
        """

        self._http_client = http_client

    async def fetch_models(self) -> List[OpenCodeModel]:
        """
        获取当前模型 ID 并合并结构化 provider 元数据

        OpenCode Go 端点决定当前可用集合，Models.dev 提供显示名和模型级 AI SDK override

        :return List: 当前可用的类型化模型列表

        :raises ModelCatalogError: 请求失败、响应无效、模型重复或两个来源不一致
        """

        payload = await self._fetch_official_models()
        model_ids = [item.id for item in payload.data]
        if len(model_ids) != len(set(model_ids)):
            raise ModelCatalogError("OpenCode Go 官方模型目录包含重复标识")
        if any(model_id.startswith("alpha-") for model_id in model_ids):
            raise ModelCatalogError("OpenCode Go 官方模型目录包含未发布模型")
        catalog_provider = await self._fetch_catalog_provider()
        if any(model_id != model.id for model_id, model in catalog_provider.models.items()):
            raise ModelCatalogError("Models.dev 的 OpenCode Go 模型键与标识不一致")
        merged_model_ids = [model_id for model_id in model_ids if model_id in catalog_provider.models]
        if not merged_model_ids:
            raise ModelCatalogError("OpenCode Go 与 Models.dev 模型目录没有可用交集")
        return [
            OpenCodeModel(
                model_id=model_id,
                name=catalog_provider.models[model_id].name,
                provider_npm=_provider_override(catalog_provider.models[model_id].provider),
            )
            for model_id in merged_model_ids
        ]

    async def _fetch_official_models(self) -> OfficialModelList:
        try:
            response = await self._http_client.get(
                OPENCODE_GO_MODELS_URL,
                headers={"Accept": "application/json"},
                timeout=10.0,
            )
            response.raise_for_status()
            return OfficialModelList.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError) as error:
            raise ModelCatalogError("OpenCode Go 官方模型目录不可用") from error

    async def _fetch_catalog_provider(self) -> OpenCodeGoCatalogProvider:
        try:
            response = await self._http_client.get(
                MODELS_DEV_CATALOG_URL,
                headers={"Accept": "application/json"},
                timeout=10.0,
            )
            response.raise_for_status()
            catalog = response.json()
            if not isinstance(catalog, dict) or "opencode-go" not in catalog:
                raise ValueError("missing opencode-go provider")
            return OpenCodeGoCatalogProvider.model_validate(catalog["opencode-go"])
        except (httpx.HTTPError, ValueError, ValidationError) as error:
            raise ModelCatalogError("Models.dev 的 OpenCode Go 元数据不可用") from error


def _provider_override(
    provider: Optional[ModelCatalogProviderOverride],
) -> Optional[Literal["@ai-sdk/anthropic", "@ai-sdk/openai"]]:
    if provider is None or provider.npm == "@ai-sdk/openai-compatible":
        return None
    return provider.npm
