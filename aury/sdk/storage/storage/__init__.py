"""存储模块。"""

from .base import IStorage, LocalStorage
from .models import StorageBackend, StorageConfig, StorageFile, UploadResult

# 延迟导入 S3Storage（可选依赖）
try:
    from .s3 import S3Storage
except ImportError:
    S3Storage = None  # type: ignore[assignment, misc]

__all__ = [
    "IStorage",
    "LocalStorage",
    "S3Storage",
    "StorageBackend",
    "StorageConfig",
    "StorageFile",
    "UploadResult",
]
