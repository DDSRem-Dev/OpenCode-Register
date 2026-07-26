from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class DuckMailProviderSettings(BaseModel):
    """
    DuckMail provider 配置
    """

    model_config = ConfigDict(extra="forbid")

    provider: Literal["duckmail"] = Field(default="duckmail", description="邮箱 provider 名称")
    base_url: str = Field(default="https://duckmail.pro", description="DuckMail API 基础地址")
    poll_interval_seconds: float = Field(default=3.0, gt=0, le=30, description="邮件轮询间隔秒数")
    request_timeout_seconds: float = Field(default=15.0, gt=0, le=60, description="单次请求超时秒数")


class DuckMailUser(BaseModel):
    """
    DuckMail 用户响应
    """

    model_config = ConfigDict(extra="ignore")

    user_id: int = Field(..., alias="id", description="DuckMail 用户标识")
    username: str = Field(..., min_length=1, description="DuckMail 用户名")
    email: str = Field(..., min_length=3, description="DuckMail 邮箱地址")


class DuckMailRegisterResponse(BaseModel):
    """
    DuckMail 注册响应
    """

    model_config = ConfigDict(extra="ignore")

    user: DuckMailUser = Field(..., description="DuckMail 用户数据")
    token: str = Field(..., min_length=1, description="DuckMail 访问令牌", repr=False)


class DuckMailEmail(BaseModel):
    """
    DuckMail 邮件响应
    """

    model_config = ConfigDict(extra="ignore")

    message_id: str = Field(..., alias="id", coerce_numbers_to_str=True, description="邮件标识")
    from_email: Optional[str] = Field(default=None, alias="fromEmail", description="发件人邮箱")
    to_addresses: List[str] = Field(default_factory=list, alias="toAddresses", description="收件人邮箱列表")
    subject: str = Field(default="", description="邮件主题")
    body: str = Field(default="", description="纯文本邮件正文")
    body_html: str = Field(default="", alias="bodyHtml", description="HTML 邮件正文")


class DuckMailEmailCollection(BaseModel):
    """
    DuckMail 邮件列表响应
    """

    model_config = ConfigDict(extra="ignore")

    emails: List[DuckMailEmail] = Field(..., description="邮件列表")


class MailboxSession(BaseModel):
    """
    provider 内部邮箱会话
    """

    model_config = ConfigDict(extra="forbid")

    address: str = Field(..., description="规范化邮箱地址")
    token: str = Field(..., description="邮箱访问令牌", repr=False)
    password: str = Field(..., description="邮箱账户密码", repr=False)
