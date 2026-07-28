from abc import ABC, abstractmethod
from typing import List, Optional

from pydantic import SecretStr

from browser.models import (
    GitHubCleanupPageResult,
    GitHubPageResult,
    OpenCodePageResult,
    OpenCodeQuotaPageResult,
)
from storage.models import BrowserAuthState


class GitHubRegistrationClient(ABC):
    """
    GitHub 注册浏览器边界

    实现必须在每次敏感操作前验证主机和页面状态，并把未知状态交给用户
    """

    @abstractmethod
    async def start_registration(self, email: str, username: str, password: str) -> GitHubPageResult:
        """
        打开 GitHub 注册页并填写注册表单

        :param email (str): 注册邮箱
        :param username (str): 生成的 GitHub 用户名
        :param password (str): 生成的 GitHub 密码

        :return GitHubPageResult: 页面操作后的类型化状态
        """

    @abstractmethod
    async def inspect_after_manual(self) -> GitHubPageResult:
        """
        在用户确认完成人工操作后重新检查页面

        :return GitHubPageResult: 当前页面的类型化状态
        """

    @abstractmethod
    async def submit_email_code(self, code: str) -> GitHubPageResult:
        """
        向 GitHub 注册页提交邮箱验证码

        :param code (str): 已验证的八位邮箱验证码

        :return GitHubPageResult: 提交后的类型化状态
        """

    @abstractmethod
    async def close(self) -> None:
        """
        关闭本次注册使用的浏览器资源

        :return None: 无返回值
        """

    async def capture_sanitized_screenshot(self, sensitive_texts: List[str]) -> Optional[bytes]:
        """
        捕获已遮罩的当前页面截图

        测试替身和不支持截图的适配器默认返回空值

        :param sensitive_texts (List): 必须额外遮罩的已知敏感文本

        :return bytes: 已遮罩 PNG；不支持时返回空值
        """

        del sensitive_texts
        return None


class OpenCodeAutomationClient(ABC):
    """
    OpenCode Go 登录、支付导航与密钥读取浏览器边界

    实现只导航到支付入口，最终付款始终由用户在可见浏览器中完成
    """

    @abstractmethod
    async def start_login(self) -> OpenCodePageResult:
        """
        使用当前 GitHub 会话登录 OpenCode 并打开 Go 页面

        :return OpenCodePageResult: 页面操作后的类型化状态
        """

    @abstractmethod
    async def inspect_after_manual(self) -> OpenCodePageResult:
        """
        用户处理登录阻断后重新检查 OpenCode 页面

        :return OpenCodePageResult: 当前页面的类型化状态
        """

    @abstractmethod
    async def confirm_payment(self) -> OpenCodePageResult:
        """
        用户确认付款后读取默认 API Key

        :return OpenCodePageResult: 密钥读取后的类型化状态
        """

    @abstractmethod
    async def submit_api_key(self, api_key: str) -> OpenCodePageResult:
        """
        校验用户手动复制的 OpenCode API Key

        :param api_key (str): 用户提交的 OpenCode API Key

        :return OpenCodePageResult: 密钥校验后的类型化状态
        """


class GitHubCleanupClient(ABC):
    """
    GitHub 账号删除浏览器边界

    实现仅在精确用户名确认后登录、验证目标身份并提交删除；安全挑战必须交给用户
    """

    @abstractmethod
    async def start_cleanup(
        self,
        username: str,
        password: SecretStr,
        github_auth_state: BrowserAuthState,
    ) -> GitHubCleanupPageResult:
        """
        登录目标 GitHub 账号并提交已确认目标的删除流程

        :param username (str): 待删除 GitHub 用户名
        :param password (SecretStr): 待删除 GitHub 账号密码
        :param github_auth_state (BrowserAuthState): 已保存 GitHub 浏览器认证状态

        :return GitHubCleanupPageResult: 当前页面状态
        """

    @abstractmethod
    async def inspect_after_manual(self) -> GitHubCleanupPageResult:
        """
        用户处理安全验证后继续删除并检查结果

        :return GitHubCleanupPageResult: 当前页面状态
        """

    @abstractmethod
    async def close(self) -> None:
        """
        关闭清理流程浏览器资源并清空内存身份

        :return None: 无返回值
        """


class OpenCodeQuotaBrowserClient(ABC):
    """
    OpenCode Go 后台浏览器额度检查边界

    实现必须验证 GitHub 身份和 OpenCode workspace，未知验证状态必须安全停止
    """

    @abstractmethod
    async def start_check(
        self,
        github_username: str,
        workspace_id: str,
        github_auth_state: BrowserAuthState,
        opencode_auth_state: BrowserAuthState,
    ) -> OpenCodeQuotaPageResult:
        """
        登录目标账号并检查 OpenCode Go 仪表盘额度

        :param github_username (str): 待检查账号的 GitHub 用户名
        :param workspace_id (str): 待检查账号的 OpenCode workspace 标识
        :param github_auth_state (BrowserAuthState): 已加密保存的 GitHub 认证状态
        :param opencode_auth_state (BrowserAuthState): 已加密保存的 OpenCode 认证状态

        :return OpenCodeQuotaPageResult: 当前仪表盘额度检查结果
        """

    @abstractmethod
    async def close(self) -> None:
        """
        关闭额度检查浏览器资源并清空内存身份

        :return None: 无返回值
        """
