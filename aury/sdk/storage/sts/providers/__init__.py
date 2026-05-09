"""STS Providers."""

from .aliyun import AliyunSTSProvider
from .tencent import TencentSTSProvider

__all__ = [
    "AliyunSTSProvider",
    "TencentSTSProvider",
]
