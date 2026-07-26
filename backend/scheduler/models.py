from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class QuotaRefreshStatus(StrEnum):
    """
    账号额度刷新业务状态

    Attributes:
        UPDATED: 额度已更新
        EXHAUSTED: 账号额度已用尽并已更新状态
        INVALID: 账号凭据已失效并已更新状态
        UNAVAILABLE: 本次检查无法得到可信结果
    """

    UPDATED = "updated"
    EXHAUSTED = "exhausted"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


class QuotaRefreshResult(BaseModel):
    """
    单个账号额度刷新结果
    """

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(..., description="账号稳定 UUID")
    status: QuotaRefreshStatus = Field(..., description="额度刷新状态")
    quota_total: Optional[int] = Field(default=None, ge=0, description="持久化额度总量")
    quota_used: Optional[int] = Field(default=None, ge=0, description="持久化已用额度")
    quota_updated_at: Optional[str] = Field(default=None, description="UTC 额度更新时间")
    message: str = Field(..., description="可安全展示的刷新结果说明")
