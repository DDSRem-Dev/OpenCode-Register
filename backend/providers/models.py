from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TempMailProviderSettings(BaseModel):
    """
    Temp-Mail provider 配置
    """

    model_config = ConfigDict(extra="forbid")

    provider: Literal["temp_mail"] = Field(default="temp_mail", description="邮箱 provider 名称")
    page_url: Literal["https://temp-mail.org/en/"] = Field(
        default="https://temp-mail.org/en/",
        description="Temp-Mail 页面地址",
    )
    poll_interval_seconds: float = Field(default=15.0, gt=0, le=30, description="空收件箱后的轮询间隔秒数")
    page_timeout_seconds: float = Field(default=30.0, gt=0, le=60, description="页面操作超时秒数")


class TempMailMessage(BaseModel):
    """
    Temp-Mail 页面邮件
    """

    model_config = ConfigDict(extra="forbid")

    sender: str = Field(..., min_length=1, max_length=320, description="邮件发件人")
    subject: str = Field(default="", max_length=500, description="邮件主题")
    body: str = Field(default="", max_length=200_000, description="邮件正文", repr=False)
