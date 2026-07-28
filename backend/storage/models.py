from datetime import UTC, datetime
from enum import StrEnum
from typing import List, Literal, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr


def utc_now() -> datetime:
    """
    获取带时区的当前 UTC 时间

    :return datetime: 当前 UTC 时间
    """

    return datetime.now(UTC)


class AccountStatus(StrEnum):
    """
    本地账号状态

    Attributes:
        ACTIVE: 账号可用
        EXHAUSTED: 账号额度已用尽
        INVALID: 账号已失效
        PENDING_SETUP: GitHub 已创建但 OpenCode 配置尚未完成
        PENDING_PAYMENT: 账号等待用户付款
        CANCELLED: 账号创建已取消
    """

    ACTIVE = "active"
    EXHAUSTED = "exhausted"
    INVALID = "invalid"
    PENDING_SETUP = "pending_setup"
    PENDING_PAYMENT = "pending_payment"
    CANCELLED = "cancelled"


class QuotaInvalidReason(StrEnum):
    """
    额度检查确认的账号失效原因

    Attributes:
        GITHUB_CREDENTIALS_INVALID: 保存的 GitHub 登录凭据已失效
        SUBSCRIPTION_REQUIRED: OpenCode Go 未订阅或订阅已到期
        UNKNOWN: 历史记录没有保存详细失效原因
    """

    GITHUB_CREDENTIALS_INVALID = "github_credentials_invalid"
    SUBSCRIPTION_REQUIRED = "subscription_required"
    UNKNOWN = "unknown"


class AccountCleanupState(StrEnum):
    """
    GitHub 账号清理持久化状态

    Attributes:
        REQUESTED: 用户已对精确目标发起远端删除
        REMOTE_DELETED: GitHub 公开资料已验证不存在，等待或正在清理本地状态
    """

    REQUESTED = "requested"
    REMOTE_DELETED = "remote_deleted"


class BrowserCookieState(BaseModel):
    """
    加密浏览器认证状态中的单个 Cookie
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=256, description="Cookie 名称")
    value: SecretStr = Field(..., max_length=16_384, description="Cookie 敏感值")
    domain: str = Field(..., min_length=1, max_length=253, description="Cookie 适用域")
    path: str = Field(..., min_length=1, max_length=2_048, description="Cookie 适用路径")
    expires: float = Field(..., description="Cookie Unix 过期时间，负数表示会话级")
    http_only: bool = Field(..., description="是否禁止页面脚本读取")
    secure: bool = Field(..., description="是否仅通过 HTTPS 发送")
    same_site: Literal["Strict", "Lax", "None"] = Field(..., description="Cookie SameSite 策略")


class BrowserStorageEntry(BaseModel):
    """
    加密浏览器认证状态中的单个 localStorage 条目
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=512, description="localStorage 条目名称")
    value: SecretStr = Field(..., max_length=131_072, description="localStorage 敏感值")


class BrowserOriginState(BaseModel):
    """
    单个受信任来源的浏览器存储状态
    """

    model_config = ConfigDict(extra="forbid")

    origin: str = Field(..., min_length=1, max_length=2_048, description="HTTPS 来源")
    local_storage: List[BrowserStorageEntry] = Field(
        default_factory=list,
        max_length=256,
        description="来源 localStorage 条目",
    )


class BrowserAuthState(BaseModel):
    """
    可加密持久化的版本化浏览器认证状态
    """

    model_config = ConfigDict(extra="forbid")

    format_version: int = Field(default=1, ge=1, le=1, description="浏览器认证状态格式版本")
    captured_at: datetime = Field(default_factory=utc_now, description="认证状态捕获时间")
    cookies: List[BrowserCookieState] = Field(
        default_factory=list,
        max_length=512,
        description="受信任域 Cookie 列表",
    )
    origins: List[BrowserOriginState] = Field(
        default_factory=list,
        max_length=8,
        description="受信任来源存储列表",
    )


class AutomaticConfigurationSettings(BaseModel):
    """
    本地账号自动配置设置
    """

    model_config = ConfigDict(extra="forbid")

    auto_configure_opencode: bool = Field(default=True, description="新增账号时是否自动写入 OpenCode 配置")
    auto_configure_omo: bool = Field(default=True, description="新增账号时是否自动写入 Oh My OpenCode 配置")


class AccountConfigurationUpdate(BaseModel):
    """
    单账号配置应用状态更新
    """

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(..., description="账号稳定唯一标识")
    opencode_configured: bool = Field(..., description="OpenCode 配置是否已写入")
    omo_configured: bool = Field(..., description="Oh My OpenCode 配置是否已写入")


class AccountCreate(BaseModel):
    """
    新增账号持久化输入
    """

    model_config = ConfigDict(extra="forbid")

    uuid: str = Field(default_factory=lambda: str(uuid4()), description="账号稳定唯一标识")
    github_username: str = Field(..., description="GitHub 用户名")
    github_email: str = Field(..., description="GitHub 注册邮箱")
    github_password: SecretStr = Field(..., description="GitHub 密码")
    github_created_at: datetime = Field(default_factory=utc_now, description="GitHub 账号创建时间")
    opencode_provider_name: str = Field(..., description="OpenCode provider 名称")
    opencode_workspace_id: str = Field(..., description="OpenCode 工作区标识")
    opencode_api_key: SecretStr = Field(..., description="OpenCode API Key")
    opencode_user_id: Optional[str] = Field(default=None, description="OpenCode 用户标识")
    github_auth_state: Optional[BrowserAuthState] = Field(default=None, description="加密保存前的 GitHub 认证状态")
    opencode_auth_state: Optional[BrowserAuthState] = Field(default=None, description="加密保存前的 OpenCode 认证状态")
    email_provider: str = Field(..., description="临时邮箱 provider 名称")
    temp_email: str = Field(..., description="临时邮箱地址")
    status: AccountStatus = Field(default=AccountStatus.ACTIVE, description="账号状态")
    opencode_configured: bool = Field(default=True, description="OpenCode 配置是否已写入")
    omo_configured: bool = Field(default=True, description="Oh My OpenCode 配置是否已写入")
    notes: Optional[str] = Field(default=None, description="用户备注")


class PendingAccountCreate(BaseModel):
    """
    GitHub 已创建但 OpenCode 尚未完成的持久化输入
    """

    model_config = ConfigDict(extra="forbid")

    uuid: str = Field(default_factory=lambda: str(uuid4()), description="账号稳定唯一标识")
    github_username: str = Field(..., description="GitHub 用户名")
    github_email: str = Field(..., description="GitHub 注册邮箱")
    github_password: SecretStr = Field(..., description="GitHub 密码")
    github_created_at: datetime = Field(default_factory=utc_now, description="GitHub 账号创建时间")
    github_auth_state: Optional[BrowserAuthState] = Field(default=None, description="加密保存前的 GitHub 认证状态")
    opencode_auth_state: Optional[BrowserAuthState] = Field(default=None, description="加密保存前的 OpenCode 认证状态")
    email_provider: str = Field(..., description="临时邮箱 provider 名称")
    temp_email: str = Field(..., description="临时邮箱地址")
    status: AccountStatus = Field(default=AccountStatus.PENDING_SETUP, description="账号状态")
    notes: Optional[str] = Field(default=None, description="用户备注")


class PendingAccount(PendingAccountCreate):
    """
    已持久化的未完成账号记录
    """

    created_at: datetime = Field(..., description="本地记录创建时间")
    updated_at: datetime = Field(..., description="本地记录更新时间")


class Account(AccountCreate):
    """
    已持久化账号记录
    """

    created_at: datetime = Field(..., description="本地记录创建时间")
    updated_at: datetime = Field(..., description="本地记录更新时间")
    quota_total: Optional[int] = Field(default=None, description="总额度")
    quota_used: Optional[int] = Field(default=None, description="已用额度")
    quota_updated_at: Optional[datetime] = Field(default=None, description="额度更新时间")
    quota_checked_at: Optional[datetime] = Field(default=None, description="最近一次确定性额度检查时间")
    quota_invalid_reason: Optional[QuotaInvalidReason] = Field(default=None, description="额度检查确认的失效原因")


class AccountSummary(BaseModel):
    """
    不包含敏感字段的账号列表摘要
    """

    model_config = ConfigDict(extra="forbid")

    uuid: str = Field(..., description="账号稳定唯一标识")
    github_username: str = Field(..., description="GitHub 用户名")
    github_email: str = Field(..., description="GitHub 注册邮箱")
    opencode_provider_name: Optional[str] = Field(default=None, description="OpenCode provider 名称")
    opencode_workspace_id: Optional[str] = Field(default=None, description="OpenCode 工作区标识")
    status: AccountStatus = Field(..., description="账号状态")
    opencode_configured: bool = Field(default=True, description="OpenCode 配置是否已写入")
    omo_configured: bool = Field(default=True, description="Oh My OpenCode 配置是否已写入")
    quota_total: Optional[int] = Field(default=None, description="总额度")
    quota_used: Optional[int] = Field(default=None, description="已用额度")
    quota_updated_at: Optional[datetime] = Field(default=None, description="额度更新时间")
    quota_checked_at: Optional[datetime] = Field(default=None, description="最近一次确定性额度检查时间")
    quota_invalid_reason: Optional[QuotaInvalidReason] = Field(default=None, description="额度检查确认的失效原因")
    created_at: datetime = Field(..., description="本地记录创建时间")
    updated_at: datetime = Field(..., description="本地记录更新时间")
    notes: Optional[str] = Field(default=None, description="用户备注")


AccountRecord = Union[Account, PendingAccount]
