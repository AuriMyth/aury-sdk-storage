"""AuriMyth Storage SDK - 多云存储 SDK。

支持：
- STS 临时凭证签发（腾讯云 COS、阿里云 OSS、AWS S3）
- S3 兼容存储操作
- 本地文件存储

使用示例:

    # STS 临时凭证
    from aury.sdk.storage.sts import (
        STSProviderFactory,
        ProviderType,
        STSRequest,
        ActionType,
    )

    provider = STSProviderFactory.create(
        ProviderType.TENCENT,
        secret_id="your-secret-id",
        secret_key="your-secret-key",
    )

    credentials = await provider.get_credentials(
        STSRequest(
            bucket="my-bucket-1250000000",
            region="ap-guangzhou",
            allow_path="user/123/",
            action_type=ActionType.WRITE,
        )
    )

    # 存储操作
    from aury.sdk.storage.storage import (
        StorageConfig,
        StorageBackend,
        StorageFile,
        S3Storage,
    )

    config = StorageConfig(
        backend=StorageBackend.COS,
        bucket_name="my-bucket-1250000000",
        region="ap-guangzhou",
        endpoint="https://cos.ap-guangzhou.myqcloud.com",
        access_key_id=credentials.access_key_id,
        access_key_secret=credentials.secret_access_key,
        session_token=credentials.session_token,
    )

    storage = S3Storage(config)
    result = await storage.upload_file(
        StorageFile(object_name="user/123/test.txt", data=b"hello")
    )
"""

__version__ = "0.1.0"

from .exceptions import (
    StorageBackendError,
    StorageError,
    StorageNotFoundError,
    StorageSDKError,
    STSError,
    STSRequestError,
    STSSignatureError,
)

__all__ = [
    "__version__",
    # Exceptions
    "StorageSDKError",
    "STSError",
    "STSSignatureError",
    "STSRequestError",
    "StorageError",
    "StorageNotFoundError",
    "StorageBackendError",
]
