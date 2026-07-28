from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from engine.models import ManualInterventionReason
from storage.models import BrowserAuthState

GITHUB_USERNAME_UNAVAILABLE_ERROR_CODE = "github_username_unavailable"


class GitHubPageStatus(StrEnum):
    """
    GitHub 注册页面状态

    Attributes:
        EMAIL_CODE_REQUIRED: 页面等待邮箱验证码
        MANUAL_REQUIRED: 页面需要用户亲自处理
        COMPLETED: GitHub 注册已经完成
        ERROR: 页面操作安全失败
    """

    EMAIL_CODE_REQUIRED = "email_code_required"
    MANUAL_REQUIRED = "manual_required"
    COMPLETED = "completed"
    ERROR = "error"


class GitHubPageResult(BaseModel):
    """
    GitHub 注册页面操作结果
    """

    model_config = ConfigDict(extra="forbid")

    status: GitHubPageStatus = Field(..., description="当前页面状态")
    github_auth_state: Optional[BrowserAuthState] = Field(default=None, description="GitHub 登录完成后的认证状态")
    manual_reason: Optional[ManualInterventionReason] = Field(default=None, description="需要人工介入的原因")
    error_code: Optional[str] = Field(default=None, description="稳定错误代码")
    error_message: Optional[str] = Field(default=None, description="不包含页面原文的安全错误消息")


class OpenCodePageStatus(StrEnum):
    """
    OpenCode 页面状态

    Attributes:
        PAYMENT_REQUIRED: 已打开 OpenCode Go 页面并等待用户付款
        API_KEY_INPUT_REQUIRED: 自动复制失败，需要用户手动提交密钥
        MANUAL_REQUIRED: OAuth 或页面状态需要用户亲自处理
        COMPLETED: 已取得并校验默认 API Key
        ERROR: 页面操作安全失败
    """

    PAYMENT_REQUIRED = "payment_required"
    API_KEY_INPUT_REQUIRED = "api_key_input_required"
    MANUAL_REQUIRED = "manual_required"
    COMPLETED = "completed"
    ERROR = "error"


class OpenCodePageResult(BaseModel):
    """
    OpenCode 页面操作结果

    API Key 使用 SecretStr，避免对象日志和异常表示泄露明文
    """

    model_config = ConfigDict(extra="forbid")

    status: OpenCodePageStatus = Field(..., description="当前页面状态")
    workspace_id: Optional[str] = Field(default=None, description="已验证的 OpenCode 工作区标识")
    api_key: Optional[SecretStr] = Field(default=None, description="已验证且禁止字符串展示的 OpenCode API Key")
    github_auth_state: Optional[BrowserAuthState] = Field(default=None, description="OAuth 完成后的 GitHub 认证状态")
    opencode_auth_state: Optional[BrowserAuthState] = Field(default=None, description="OpenCode 登录后的认证状态")
    manual_reason: Optional[ManualInterventionReason] = Field(default=None, description="需要人工介入的原因")
    error_code: Optional[str] = Field(default=None, description="稳定错误代码")
    error_message: Optional[str] = Field(default=None, description="不包含页面原文的安全错误消息")


class GitHubCleanupPageStatus(StrEnum):
    """
    GitHub 账号清理页面状态

    Attributes:
        AUTH_REQUIRED: 保存的 GitHub 浏览器认证状态缺失或已经失效
        MANUAL_REQUIRED: 登录验证需要用户亲自处理
        DELETED: 公开资料已确认不存在
        INVALID: GitHub 登录凭据已失效
        ERROR: 页面操作安全失败
    """

    AUTH_REQUIRED = "auth_required"
    MANUAL_REQUIRED = "manual_required"
    DELETED = "deleted"
    INVALID = "invalid"
    ERROR = "error"


class GitHubCleanupPageResult(BaseModel):
    """
    GitHub 账号清理浏览器操作结果
    """

    model_config = ConfigDict(extra="forbid")

    status: GitHubCleanupPageStatus = Field(..., description="当前 GitHub 清理页面状态")
    manual_reason: Optional[ManualInterventionReason] = Field(default=None, description="需要人工介入的原因")
    error_code: Optional[str] = Field(default=None, description="稳定错误代码")
    error_message: Optional[str] = Field(default=None, description="不包含页面原文的安全错误消息")


class OpenCodeQuotaPageStatus(StrEnum):
    """
    OpenCode Go 仪表盘额度检查状态

    Attributes:
        UPDATED: 已从受信任页面边界取得额度
        AUTH_REQUIRED: 保存的浏览器认证状态缺失或已经失效
        MANUAL_REQUIRED: 安全验证阻止本次后台检查
        INVALID: 保存的 GitHub 登录凭据无效
        SUBSCRIPTION_REQUIRED: OpenCode Go 当前没有有效订阅
        UNAVAILABLE: 页面或额度数据暂时不可用
    """

    UPDATED = "updated"
    AUTH_REQUIRED = "auth_required"
    MANUAL_REQUIRED = "manual_required"
    INVALID = "invalid"
    SUBSCRIPTION_REQUIRED = "subscription_required"
    UNAVAILABLE = "unavailable"


class OpenCodeQuotaPageResult(BaseModel):
    """
    OpenCode Go 仪表盘额度检查结果
    """

    model_config = ConfigDict(extra="forbid")

    status: OpenCodeQuotaPageStatus = Field(..., description="当前仪表盘检查状态")
    usage_percent: Optional[int] = Field(default=None, ge=0, le=100, description="每月用量的已用百分比")
    github_auth_state: Optional[BrowserAuthState] = Field(default=None, description="保活后的 GitHub 认证状态")
    opencode_auth_state: Optional[BrowserAuthState] = Field(default=None, description="刷新后的 OpenCode 认证状态")
    manual_reason: Optional[ManualInterventionReason] = Field(default=None, description="阻止后台检查的安全验证原因")
    error_code: Optional[str] = Field(default=None, description="稳定错误代码")
    error_message: Optional[str] = Field(default=None, description="不包含页面原文的安全错误消息")
