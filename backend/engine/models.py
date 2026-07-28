from enum import StrEnum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from storage.models import BrowserAuthState


class FlowStatus(StrEnum):
    """
    账号创建流程状态

    Attributes:
        IDLE: 流程尚未开始
        CREATING_EMAIL: 正在创建临时邮箱
        GITHUB_REGISTER: 正在填写 GitHub 注册表单
        MANUAL_VERIFY: 等待用户完成人工验证
        GITHUB_EMAIL_VERIFY: 正在等待并提交 GitHub 邮箱验证码
        OPENCODE_LOGIN: 正在通过 GitHub OAuth 登录 OpenCode
        PENDING_PAYMENT: 已打开 OpenCode Go 页面并等待用户付款
        FETCH_API_KEY: 用户确认付款后正在读取默认 API Key
        DONE: 账号创建流程已完成
        ERROR: 流程执行失败
        CANCELLED: 流程已取消
    """

    IDLE = "idle"
    CREATING_EMAIL = "creating_email"
    GITHUB_REGISTER = "github_register"
    MANUAL_VERIFY = "manual_verify"
    GITHUB_EMAIL_VERIFY = "github_email_verify"
    OPENCODE_LOGIN = "opencode_login"
    PENDING_PAYMENT = "pending_payment"
    FETCH_API_KEY = "fetch_api_key"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


class FlowStepStatus(StrEnum):
    """
    流程步骤结果状态

    Attributes:
        DONE: 步骤执行完成
        NEED_MANUAL: 步骤需要人工处理
        ERROR: 步骤执行失败
        CANCELLED: 步骤被取消
    """

    DONE = "done"
    NEED_MANUAL = "need_manual"
    ERROR = "error"
    CANCELLED = "cancelled"


class ManualInterventionReason(StrEnum):
    """
    人工介入原因

    Attributes:
        CAPTCHA: 页面出现验证码或风险控制挑战
        PHONE_VERIFICATION: 页面要求手机号或身份验证
        UNKNOWN_BLOCK: 页面处于无法安全判断的阻断状态
        TIMEOUT: 自动化步骤等待超时
        USER_PAUSED: 用户主动请求暂停
        PAYMENT: 等待用户手动完成 OpenCode Go 付款
        API_KEY_INPUT: 等待用户手动复制默认 API Key
    """

    CAPTCHA = "captcha"
    PHONE_VERIFICATION = "phone_verification"
    UNKNOWN_BLOCK = "unknown_block"
    TIMEOUT = "timeout"
    USER_PAUSED = "user_paused"
    PAYMENT = "payment"
    API_KEY_INPUT = "api_key_input"


class ManualIntervention(BaseModel):
    """
    当前人工介入请求
    """

    model_config = ConfigDict(extra="forbid")

    reason: ManualInterventionReason = Field(..., description="人工介入原因")
    title: str = Field(..., description="面向用户的简短标题")
    instruction: str = Field(..., description="不包含敏感信息的操作说明")


class FlowSession(BaseModel):
    """
    账号创建流程会话快照
    """

    model_config = ConfigDict(extra="forbid")

    flow_id: str = Field(default_factory=lambda: str(uuid4()), description="流程唯一标识")
    status: FlowStatus = Field(default=FlowStatus.IDLE, description="当前流程状态")
    email_provider: Optional[str] = Field(default=None, description="临时邮箱 provider 名称")
    temp_email: Optional[str] = Field(default=None, description="临时邮箱地址")
    github_username: Optional[str] = Field(default=None, description="生成的 GitHub 用户名")
    account_id: Optional[str] = Field(default=None, description="GitHub 创建后持久化账号的稳定标识")
    opencode_workspace_id: Optional[str] = Field(default=None, description="OpenCode 默认工作区标识")
    opencode_provider_name: Optional[str] = Field(default=None, description="已写入号池的 OpenCode provider 名称")
    api_key_captured: bool = Field(default=False, description="是否已安全取得 OpenCode API Key")
    manual_intervention: Optional[ManualIntervention] = Field(default=None, description="当前人工介入请求")
    screenshot_id: Optional[str] = Field(default=None, description="当前已遮罩人工介入截图标识")
    pause_requested: bool = Field(default=False, description="是否正在等待安全暂停点")
    error_code: Optional[str] = Field(default=None, description="稳定错误代码")
    error_message: Optional[str] = Field(default=None, description="安全错误消息")


class AccountCompletionData(BaseModel):
    """
    向加密持久化边界提交的完整账号数据
    """

    model_config = ConfigDict(extra="forbid")

    account_id: Optional[str] = Field(default=None, description="待提升未完成账号的稳定标识")
    github_username: str = Field(..., description="GitHub 用户名")
    github_email: str = Field(..., description="GitHub 注册邮箱")
    github_password: SecretStr = Field(..., description="GitHub 密码")
    opencode_workspace_id: str = Field(..., description="OpenCode 工作区标识")
    opencode_api_key: SecretStr = Field(..., description="OpenCode API Key")
    github_auth_state: Optional[BrowserAuthState] = Field(default=None, description="GitHub 浏览器认证状态")
    opencode_auth_state: Optional[BrowserAuthState] = Field(default=None, description="OpenCode 浏览器认证状态")
    email_provider: str = Field(..., description="临时邮箱 provider 名称")
    temp_email: str = Field(..., description="临时邮箱地址")


class PendingAccountData(BaseModel):
    """
    GitHub 注册完成后立即提交给加密持久化边界的数据
    """

    model_config = ConfigDict(extra="forbid")

    github_username: str = Field(..., description="GitHub 用户名")
    github_email: str = Field(..., description="GitHub 注册邮箱")
    github_password: SecretStr = Field(..., description="GitHub 密码")
    github_auth_state: Optional[BrowserAuthState] = Field(default=None, description="GitHub 浏览器认证状态")
    email_provider: str = Field(..., description="临时邮箱 provider 名称")
    temp_email: str = Field(..., description="临时邮箱地址")


class FlowStepResult(BaseModel):
    """
    账号创建流程步骤结果
    """

    model_config = ConfigDict(extra="forbid")

    status: FlowStepStatus = Field(..., description="步骤执行结果")
    session: FlowSession = Field(..., description="流程会话快照")


class StepExecutionResult(BaseModel):
    """
    原子流程步骤执行结果
    """

    model_config = ConfigDict(extra="forbid")

    status: FlowStepStatus = Field(..., description="步骤执行结果")
    email_provider: Optional[str] = Field(default=None, description="成功使用的邮箱 provider 名称")
    temp_email: Optional[str] = Field(default=None, description="成功创建的临时邮箱地址")
    error_code: Optional[str] = Field(default=None, description="稳定错误代码")
    error_message: Optional[str] = Field(default=None, description="安全错误消息")
