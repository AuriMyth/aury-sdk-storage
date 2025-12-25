"""统一异常定义。"""

from __future__ import annotations


class StorageSDKError(Exception):
    """Storage SDK 基础异常。"""

    def __init__(self, message: str, code: str | None = None) -> None:
        self.message = message
        self.code = code
        super().__init__(message)

    def __str__(self) -> str:
        if self.code:
            return f"[{self.code}] {self.message}"
        return self.message


class STSError(StorageSDKError):
    """STS 相关错误。"""

    pass


class STSSignatureError(STSError):
    """STS 签名错误。"""

    pass


class STSRequestError(STSError):
    """STS 请求错误（网络/API 调用失败）。"""

    def __init__(
        self,
        message: str,
        code: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message, code)
        self.request_id = request_id


class StorageError(StorageSDKError):
    """存储操作相关错误。"""

    pass


class StorageNotFoundError(StorageError):
    """存储文件不存在。"""

    pass


class StorageBackendError(StorageError):
    """存储后端错误。"""

    pass


__all__ = [
    "StorageSDKError",
    "STSError",
    "STSSignatureError",
    "STSRequestError",
    "StorageError",
    "StorageNotFoundError",
    "StorageBackendError",
]
