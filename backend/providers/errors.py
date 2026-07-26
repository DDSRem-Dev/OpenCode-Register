class EmailProviderError(Exception):
    """
    临时邮箱 provider 基础异常
    """


class EmailProviderConfigurationError(EmailProviderError):
    """
    临时邮箱 provider 配置异常
    """


class EmailProviderResponseError(EmailProviderError):
    """
    临时邮箱 provider 响应异常
    """


class EmailProviderTimeoutError(EmailProviderError):
    """
    临时邮箱验证码等待超时异常
    """
