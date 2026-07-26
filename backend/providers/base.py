from abc import ABC, abstractmethod


class EmailProvider(ABC):
    """
    临时邮箱 provider 抽象接口
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """
        获取 provider 稳定名称

        :return str: provider 稳定名称
        """

    @abstractmethod
    async def create_email(self) -> str:
        """
        创建临时邮箱

        :return str: 规范化后的临时邮箱地址

        :raises EmailProviderError: provider 创建邮箱失败
        """

    @abstractmethod
    async def wait_for_code(self, email: str, timeout: int) -> str:
        """
        等待邮箱收到有效验证码

        :param email (str): 临时邮箱地址
        :param timeout (int): 最大等待秒数

        :return str: 收到的验证码

        :raises EmailProviderError: provider 拉取或解析邮件失败
        :raises EmailProviderTimeoutError: 超时仍未收到有效验证码
        """

    @abstractmethod
    async def dispose(self, email: str) -> None:
        """
        尽力释放临时邮箱资源

        :param email (str): 临时邮箱地址

        :return None: 无返回值
        """
