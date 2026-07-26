from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from engine.models import ManualIntervention


class AccountCleanupStatus(StrEnum):
    """
    GitHub 账号清理流程状态

    Attributes:
        STARTING: 正在读取目标并启动浏览器
        MANUAL_REQUIRED: 等待用户处理登录验证
        LOCAL_CLEANUP: 远端删除已验证，正在清理本地记录与配置
        DONE: 远端与本地清理全部完成
        ERROR: 清理流程安全失败并保留可重试状态
        CANCELLED: 用户在远端删除完成前取消流程
    """

    STARTING = "starting"
    MANUAL_REQUIRED = "manual_required"
    LOCAL_CLEANUP = "local_cleanup"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


class AccountCleanupSession(BaseModel):
    """
    不包含凭据的 GitHub 账号清理流程快照
    """

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(..., description="待清理账号稳定 UUID")
    github_username: str = Field(..., description="已由用户确认的 GitHub 用户名")
    status: AccountCleanupStatus = Field(..., description="当前清理流程状态")
    manual_intervention: Optional[ManualIntervention] = Field(default=None, description="当前人工介入请求")
    promoted_account_id: Optional[str] = Field(default=None, description="递补为首账号的稳定 UUID")
    error_code: Optional[str] = Field(default=None, description="稳定错误代码")
    error_message: Optional[str] = Field(default=None, description="不包含凭据的安全错误消息")
