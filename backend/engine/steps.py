import asyncio
from abc import ABC, abstractmethod
from typing import List, Optional

from engine.models import FlowStepStatus, StepExecutionResult
from providers.base import EmailProvider
from providers.errors import EmailProviderError


class Step(ABC):
    """
    原子流程步骤抽象接口
    """

    @abstractmethod
    async def execute(self) -> StepExecutionResult:
        """
        执行原子流程步骤

        :return StepExecutionResult: 类型化步骤结果
        """


class CreateEmailStep(Step):
    """
    按优先级创建临时邮箱的流程步骤
    """

    def __init__(self, providers: List[EmailProvider]) -> None:
        """
        初始化临时邮箱创建步骤

        :param providers (List): 按优先级排列的邮箱 provider
        """

        self._providers = providers
        self._selected_provider: Optional[EmailProvider] = None
        self._cancelled = False

    @property
    def selected_provider(self) -> Optional[EmailProvider]:
        """
        获取本次成功创建邮箱的 provider

        :return EmailProvider: 已选 provider，尚未成功时返回空值
        """

        return self._selected_provider

    async def execute(self) -> StepExecutionResult:
        """
        按优先级尝试创建临时邮箱

        :return StepExecutionResult: 邮箱创建步骤结果
        """

        for provider in self._providers:
            if self._cancelled:
                return StepExecutionResult(status=FlowStepStatus.CANCELLED)
            create_task = asyncio.create_task(provider.create_email())
            try:
                email = await asyncio.shield(create_task)
            except asyncio.CancelledError:
                try:
                    email = await create_task
                except EmailProviderError:
                    raise
                await provider.dispose(email)
                raise
            except EmailProviderError:
                continue
            self._selected_provider = provider
            return StepExecutionResult(
                status=FlowStepStatus.DONE,
                email_provider=provider.provider_name,
                temp_email=email,
            )
        return StepExecutionResult(
            status=FlowStepStatus.ERROR,
            error_code="email_provider_failed",
            error_message="临时邮箱创建失败",
        )

    def cancel(self) -> None:
        """
        请求步骤停止后续 provider 尝试

        :return None: 无返回值
        """

        self._cancelled = True
