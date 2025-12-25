"""STS 临时凭证模块。"""

from .factory import ProviderType, STSProviderFactory
from .models import ActionType, STSCredentials, STSRequest, TencentSTSConfig
from .policy import IPolicyBuilder, TencentPolicyBuilder
from .provider import ISTSProvider
from .providers import TencentSTSProvider

__all__ = [
    # Models
    "ActionType",
    "STSCredentials",
    "STSRequest",
    "TencentSTSConfig",
    # Provider
    "ISTSProvider",
    "TencentSTSProvider",
    # Policy
    "IPolicyBuilder",
    "TencentPolicyBuilder",
    # Factory
    "ProviderType",
    "STSProviderFactory",
]
