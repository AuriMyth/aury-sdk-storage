"""STS Provider 抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import STSCredentials, STSRequest


class ISTSProvider(ABC):
    """STS Provider 抽象接口。

    各云厂商实现此接口，提供统一的 STS 临时凭证获取能力。
    """

    @abstractmethod
    async def get_credentials(self, request: STSRequest) -> STSCredentials:
        """获取 STS 临时凭证。

        Args:
            request: STS 请求参数

        Returns:
            统一格式的 STS 临时凭证
        """
        pass


__all__ = [
    "ISTSProvider",
]
