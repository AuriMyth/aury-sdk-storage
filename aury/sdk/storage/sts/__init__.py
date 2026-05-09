"""STS 临时凭证模块。"""

from .factory import ProviderType, STSProviderFactory
from .models import (
    ActionType,
    AliyunSTSConfig,
    COSSTSConfig,
    OSSSTSConfig,
    STSCredentials,
    STSRequest,
    TencentSTSConfig,
)
from .policy import AliyunPolicyBuilder, IPolicyBuilder, TencentPolicyBuilder
from .provider import ISTSProvider
from .providers import AliyunSTSProvider, TencentSTSProvider

__all__ = [
    # Models
    "ActionType",
    "STSCredentials",
    "STSRequest",
    "AliyunSTSConfig",
    "COSSTSConfig",
    "OSSSTSConfig",
    "TencentSTSConfig",  # 别名，兼容
    # Provider
    "AliyunSTSProvider",
    "ISTSProvider",
    "TencentSTSProvider",
    # Policy
    "AliyunPolicyBuilder",
    "IPolicyBuilder",
    "TencentPolicyBuilder",
    # Factory
    "ProviderType",
    "STSProviderFactory",
]
