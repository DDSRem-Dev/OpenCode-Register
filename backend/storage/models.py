from datetime import UTC, datetime
from enum import StrEnum
from typing import Optional, Union
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
