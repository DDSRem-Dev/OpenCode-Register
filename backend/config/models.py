from pathlib import Path
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OpenCodeConfigPaths(BaseModel):
    """
    OpenCode 与 OMO 配置文件路径
    """

    model_config = ConfigDict(extra="forbid")

    auth_path: Path = Field(..., description="OpenCode 认证配置文件路径")
    opencode_path: Path = Field(..., description="OpenCode provider 配置文件路径")
    omo_path: Path = Field(..., description="Oh My OpenAgent 配置文件路径")

    @field_validator("auth_path", "opencode_path", "omo_path", mode="before")
    @classmethod
    def resolve_path(cls, value: Path) -> Path:
        """
        展开并解析用户配置路径

        :param value (Path): 待解析路径

        :return Path: 绝对规范路径
        """

        return Path(value).expanduser().resolve()

    @field_validator("auth_path")
    @classmethod
    def validate_auth_name(cls, value: Path) -> Path:
        """
        校验认证配置文件名

        :param value (Path): 已解析路径

        :return Path: 文件名有效的路径

        :raises ValueError: 文件名不是 auth.json
        """

        if value.name != "auth.json":
            raise ValueError("认证配置目标必须是 auth.json")
        return value

    @field_validator("opencode_path")
    @classmethod
    def validate_opencode_name(cls, value: Path) -> Path:
        """
        校验 OpenCode 配置文件名

        :param value (Path): 已解析路径

        :return Path: 文件名有效的路径

        :raises ValueError: 文件名不是 opencode.json
        """

        if value.name != "opencode.json":
            raise ValueError("OpenCode 配置目标必须是 opencode.json")
        return value

    @field_validator("omo_path")
    @classmethod
    def validate_omo_name(cls, value: Path) -> Path:
        """
        校验 OMO 配置文件名

        :param value (Path): 已解析路径

        :return Path: 文件名有效的路径

        :raises ValueError: 文件名不是 oh-my-openagent.json
        """

        if value.name != "oh-my-openagent.json":
            raise ValueError("OMO 配置目标必须是 oh-my-openagent.json")
        return value


class OpenCodeModel(BaseModel):
    """
    新 provider 使用的 OpenCode 模型定义
    """

    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(..., min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", description="模型标识")
    name: str = Field(..., min_length=1, description="模型显示名称")
    provider_npm: Optional[Literal["@ai-sdk/anthropic", "@ai-sdk/openai"]] = Field(
        default=None,
        description="模型覆盖使用的 AI SDK 包",
    )


class ConfigWriteResult(BaseModel):
    """
    配置文件写入结果
    """

    model_config = ConfigDict(extra="forbid")

    target_path: Path = Field(..., description="已更新配置文件路径")
    backup_path: Optional[Path] = Field(default=None, description="本次写入创建的备份路径")
    provider_name: str = Field(..., description="本次写入的 provider 名称")
    changed: bool = Field(default=True, description="本次调用是否实际修改配置文件")


class ModelSyncResult(BaseModel):
    """
    OpenCode provider 模型同步结果
    """

    model_config = ConfigDict(extra="forbid")

    target_path: Path = Field(..., description="已检查或更新的配置文件路径")
    backup_path: Optional[Path] = Field(default=None, description="本次同步创建的备份路径")
    updated_providers: List[str] = Field(..., description="已同步模型的 provider 名称")


class OfficialModelItem(BaseModel):
    """
    OpenCode Go 官方模型条目
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", description="官方模型标识")
    object: Literal["model"] = Field(..., description="官方对象类型")
    created: int = Field(..., ge=0, description="官方响应创建时间戳")
    owned_by: Literal["opencode"] = Field(..., description="模型所有者")


class OfficialModelList(BaseModel):
    """
    OpenCode Go 官方模型列表响应
    """

    model_config = ConfigDict(extra="ignore")

    object: Literal["list"] = Field(..., description="官方列表对象类型")
    data: List[OfficialModelItem] = Field(..., min_length=1, description="官方模型条目")


class ModelCatalogProviderOverride(BaseModel):
    """
    Models.dev 模型级 provider 覆盖
    """

    model_config = ConfigDict(extra="ignore")

    npm: Literal["@ai-sdk/openai-compatible", "@ai-sdk/anthropic", "@ai-sdk/openai"] = Field(
        ...,
        description="模型使用的 AI SDK 包",
    )


class ModelCatalogItem(BaseModel):
    """
    Models.dev 的 OpenCode Go 模型元数据
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", description="模型标识")
    name: str = Field(..., min_length=1, description="模型显示名称")
    provider: Optional[ModelCatalogProviderOverride] = Field(default=None, description="模型级 provider 覆盖")


class OpenCodeGoCatalogProvider(BaseModel):
    """
    Models.dev 的 OpenCode Go provider 元数据
    """

    model_config = ConfigDict(extra="ignore")

    id: Literal["opencode-go"] = Field(..., description="OpenCode Go provider 标识")
    name: Literal["OpenCode Go"] = Field(..., description="OpenCode Go provider 名称")
    npm: Literal["@ai-sdk/openai-compatible"] = Field(..., description="provider 默认 AI SDK 包")
    api: Literal["https://opencode.ai/zen/go/v1"] = Field(..., description="OpenCode Go API 基础地址")
    models: Dict[str, ModelCatalogItem] = Field(..., min_length=1, description="OpenCode Go 模型元数据")


class PoolConfigWriteResult(BaseModel):
    """
    二级账号号池配置写入结果
    """

    model_config = ConfigDict(extra="forbid")

    provider_name: str = Field(..., description="新增 OpenCode Go provider 名称")
    model_count: int = Field(..., ge=1, description="本次使用的官方模型数量")
    opencode_result: ConfigWriteResult = Field(..., description="OpenCode 配置写入结果")
    omo_result: Optional[ConfigWriteResult] = Field(default=None, description="可选的 OMO 配置写入结果")


class OmoModelSyncResult(BaseModel):
    """
    OMO 已下线模型清理结果
    """

    model_config = ConfigDict(extra="forbid")

    target_path: Path = Field(..., description="已检查或更新的 OMO 配置路径")
    backup_path: Optional[Path] = Field(default=None, description="本次清理创建的备份路径")
    updated_agents: List[str] = Field(..., description="已清理 fallback 的 agent 名称")


class PoolModelSyncResult(BaseModel):
    """
    官方模型与全部号池配置同步结果
    """

    model_config = ConfigDict(extra="forbid")

    model_count: int = Field(..., ge=1, description="本次官方模型数量")
    opencode_result: ModelSyncResult = Field(..., description="OpenCode provider 同步结果")
    omo_result: OmoModelSyncResult = Field(..., description="OMO fallback 同步结果")


class PoolAccountRemovalResult(BaseModel):
    """
    账号号池配置清理结果
    """

    model_config = ConfigDict(extra="forbid")

    removed_provider_name: str = Field(..., description="已移除账号原 provider 名称")
    promoted_account_id: Optional[str] = Field(default=None, description="递补为首账号的稳定 UUID")
    promoted_provider_name: Optional[str] = Field(default=None, description="递补账号原 provider 名称")
    writes: List[ConfigWriteResult] = Field(..., description="本次执行的配置文件写入结果")
