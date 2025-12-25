"""STS Provider 工厂。"""

from __future__ import annotations

from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel

from .models import TencentSTSConfig
from .provider import ISTSProvider
from .providers.tencent import TencentSTSProvider


class ProviderType(str, Enum):
    """Provider 类型。"""

    TENCENT = "tencent"
    ALIYUN = "aliyun"
    AWS = "aws"


class STSProviderFactory:
    """STS Provider 工厂。

    使用示例:
        # 方式1: 直接创建
        provider = STSProviderFactory.create(
            ProviderType.TENCENT,
            secret_id="...",
            secret_key="...",
        )

        # 方式2: 从 Pydantic 配置创建
        config = TencentSTSConfig(secret_id="...", secret_key="...")
        provider = STSProviderFactory.from_config(ProviderType.TENCENT, config)
    """

    _providers: ClassVar[dict[ProviderType, type[ISTSProvider]]] = {
        ProviderType.TENCENT: TencentSTSProvider,
    }

    _config_classes: ClassVar[dict[ProviderType, type[BaseModel]]] = {
        ProviderType.TENCENT: TencentSTSConfig,
    }

    @classmethod
    def register(
        cls,
        provider_type: ProviderType,
        provider_class: type[ISTSProvider],
        config_class: type[BaseModel],
    ) -> None:
        """注册新的 Provider。"""
        cls._providers[provider_type] = provider_class
        cls._config_classes[provider_type] = config_class

    @classmethod
    def create(cls, provider_type: ProviderType, **kwargs: Any) -> ISTSProvider:
        """创建 Provider 实例。

        Args:
            provider_type: Provider 类型
            **kwargs: 配置参数

        Returns:
            Provider 实例
        """
        if provider_type not in cls._providers:
            available = ", ".join(p.value for p in cls._providers.keys())
            raise ValueError(
                f"Provider '{provider_type.value}' 未注册。可用: {available}"
            )

        config_class = cls._config_classes[provider_type]
        config = config_class(**kwargs)

        provider_class = cls._providers[provider_type]
        return provider_class(config)

    @classmethod
    def from_config(
        cls,
        provider_type: ProviderType,
        config: BaseModel,
    ) -> ISTSProvider:
        """从 Pydantic 配置创建 Provider。

        Args:
            provider_type: Provider 类型
            config: Pydantic 配置对象

        Returns:
            Provider 实例
        """
        if provider_type not in cls._providers:
            available = ", ".join(p.value for p in cls._providers.keys())
            raise ValueError(
                f"Provider '{provider_type.value}' 未注册。可用: {available}"
            )

        provider_class = cls._providers[provider_type]
        return provider_class(config)

    @classmethod
    def get_available_providers(cls) -> list[ProviderType]:
        """获取可用的 Provider 类型列表。"""
        return list(cls._providers.keys())


__all__ = [
    "ProviderType",
    "STSProviderFactory",
]
