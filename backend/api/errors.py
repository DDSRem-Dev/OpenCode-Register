from typing import Optional

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


class ApiError(Exception):
    """
    可映射为稳定 HTTP 错误信封的 API 异常
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        """
        初始化安全 API 异常

        :param status_code (int): HTTP 状态码
        :param code (str): 稳定机器错误码
        :param message (str): 安全用户提示
        """

        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message


class ErrorResponse(BaseModel):
    """
    本地 API 稳定错误信封
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., description="稳定机器错误码")
    message: str = Field(..., description="安全用户提示")
    details: Optional[str] = Field(default=None, description="可选的净化错误上下文")


async def api_error_handler(request: Request, error: Exception) -> JSONResponse:
    """
    将 API 异常转换为稳定错误响应

    :param request (Request): 当前 FastAPI 请求
    :param error (Exception): 已净化的 API 异常

    :return JSONResponse: 稳定错误信封响应
    """

    del request
    if not isinstance(error, ApiError):
        raise error
    response = ErrorResponse(code=error.code, message=error.message)
    return JSONResponse(status_code=error.status_code, content=response.model_dump())


async def validation_error_handler(request: Request, error: Exception) -> JSONResponse:
    """
    将请求校验错误转换为不回显输入值的稳定错误信封

    :param request (Request): 当前 FastAPI 请求
    :param error (Exception): 请求校验异常

    :return JSONResponse: 净化后的校验错误响应
    """

    del request
    if not isinstance(error, RequestValidationError):
        raise error
    response = ErrorResponse(code="request_validation_failed", message="请求数据格式无效")
    return JSONResponse(status_code=422, content=response.model_dump())


async def unexpected_error_handler(request: Request, error: Exception) -> JSONResponse:
    """
    将未预期异常转换为不泄露内部细节的稳定错误信封

    :param request (Request): 当前 FastAPI 请求
    :param error (Exception): 未预期内部异常

    :return JSONResponse: 净化后的服务错误响应
    """

    del request, error
    response = ErrorResponse(code="internal_error", message="本地服务发生未预期错误")
    return JSONResponse(status_code=500, content=response.model_dump())
