import re

from providers.errors import EmailProviderResponseError

EMAIL_PATTERN = re.compile(
    r"^(?=.{3,254}$)[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


def normalize_email_address(address: str, provider_name: str) -> str:
    """
    规范化并校验 provider 返回的邮箱地址

    :param address (str): provider 返回的邮箱地址
    :param provider_name (str): provider 稳定名称

    :return str: 规范化后的邮箱地址

    :raises EmailProviderResponseError: 邮箱地址格式无效
    """

    normalized = address.strip().lower()
    if EMAIL_PATTERN.fullmatch(normalized) is None:
        raise EmailProviderResponseError(f"{provider_name} 返回了无效邮箱地址")
    return normalized
