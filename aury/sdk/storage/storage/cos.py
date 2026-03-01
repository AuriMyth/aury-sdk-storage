"""腾讯云 COS 存储实现（纯 aiohttp 异步版本）。

不依赖官方 SDK，直接使用 aiohttp 调用 COS REST API。
支持全球加速域名和自定义域名。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlencode

import aiohttp

from aury.sdk.storage.exceptions import StorageBackendError, StorageNotFoundError

from .base import IStorage
from .models import StorageConfig, StorageFile, UploadResult


class COSStorage(IStorage):
    """腾讯云 COS 存储实现（纯 aiohttp 异步版本）。

    直接使用 aiohttp 调用 COS REST API，无需安装 cos-python-sdk-v5。
    支持全球加速域名和自定义域名。
    """

    def __init__(self, config: StorageConfig) -> None:
        """初始化 COS 存储。

        Args:
            config: 存储配置
        """
        self._config = config
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保 aiohttp 会话已创建。"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=60.0)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _get_bucket(self, bucket_name: str | None) -> str:
        """获取桶名。"""
        bucket = bucket_name or self._config.bucket_name
        if not bucket:
            raise StorageBackendError("桶名未指定")
        return bucket

    def _get_host(self, bucket: str) -> str:
        """获取请求 Host。

        支持:
        - 自定义 endpoint（全球加速域名等）
        - 默认域名格式: {bucket}.cos.{region}.myqcloud.com
        """
        if self._config.endpoint:
            endpoint = self._config.endpoint
            if endpoint.startswith("https://"):
                endpoint = endpoint[8:]
            elif endpoint.startswith("http://"):
                endpoint = endpoint[7:]
            endpoint = endpoint.rstrip("/")
            # 如果 endpoint 已包含 bucket，直接返回
            if bucket in endpoint:
                return endpoint
            return f"{bucket}.{endpoint}"

        if not self._config.region:
            raise StorageBackendError("Region 或 Endpoint 必须指定")
        return f"{bucket}.cos.{self._config.region}.myqcloud.com"

    def _get_base_url(self, bucket: str) -> str:
        """获取基础 URL。"""
        host = self._get_host(bucket)
        return f"https://{host}"

    def _read_file_data(self, file: StorageFile) -> bytes:
        """读取文件数据。"""
        if file.data is None:
            return b""
        if isinstance(file.data, bytes):
            return file.data
        return file.data.read()

    # ==================== COS 签名算法 ====================

    def _sign(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        expire: int = 10000,
    ) -> str:
        """生成 COS 签名。

        COS 签名算法文档: https://cloud.tencent.com/document/product/436/7778

        Args:
            method: HTTP 方法 (GET/PUT/POST/DELETE/HEAD)
            path: 请求路径 (以 / 开头)
            params: URL 参数
            headers: 请求头
            expire: 签名有效期（秒）

        Returns:
            Authorization header 值
        """
        secret_id = self._config.access_key_id
        secret_key = self._config.access_key_secret

        if not secret_id or not secret_key:
            raise StorageBackendError("缺少访问密钥")

        params = params or {}
        headers = headers or {}

        # 1. 过滤并格式化 headers
        sign_headers = self._filter_headers(headers)

        # 2. 构建 HttpString
        # 格式: {method}\n{path}\n{params}\n{headers}\n
        param_str = "&".join(
            f"{quote(k.lower(), safe='-_.~')}={quote(str(v), safe='-_.~')}"
            for k, v in sorted(params.items())
        )
        header_str = "&".join(
            f"{quote(k.lower(), safe='-_.~')}={quote(str(v), safe='-_.~')}"
            for k, v in sorted(sign_headers.items())
        )
        http_string = f"{method.lower()}\n{path}\n{param_str}\n{header_str}\n"

        # 3. 计算签名时间
        start_time = int(time.time()) - 60
        end_time = start_time + expire + 60
        sign_time = f"{start_time};{end_time}"

        # 4. 计算 StringToSign
        sha1_hash = hashlib.sha1(http_string.encode("utf-8")).hexdigest()
        string_to_sign = f"sha1\n{sign_time}\n{sha1_hash}\n"

        # 5. 计算签名
        sign_key = hmac.new(
            secret_key.encode("utf-8"),
            sign_time.encode("utf-8"),
            hashlib.sha1,
        ).hexdigest()
        signature = hmac.new(
            sign_key.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).hexdigest()

        # 6. 拼接 Authorization
        auth = (
            f"q-sign-algorithm=sha1"
            f"&q-ak={secret_id}"
            f"&q-sign-time={sign_time}"
            f"&q-key-time={sign_time}"
            f"&q-header-list={';'.join(sorted(k.lower() for k in sign_headers.keys()))}"
            f"&q-url-param-list={';'.join(sorted(k.lower() for k in params.keys()))}"
            f"&q-signature={signature}"
        )

        return auth

    def _filter_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """过滤参与签名的 headers。

        只有以下 headers 参与签名:
        - host
        - content-type
        - content-md5
        - content-length
        - x-cos-* 开头的
        """
        valid_headers = {
            "cache-control",
            "content-disposition",
            "content-encoding",
            "content-type",
            "content-md5",
            "content-length",
            "expect",
            "expires",
            "host",
            "if-match",
            "if-modified-since",
            "if-none-match",
            "if-unmodified-since",
            "origin",
            "range",
            "transfer-encoding",
        }
        result = {}
        for k, v in headers.items():
            key_lower = k.lower()
            if key_lower in valid_headers or key_lower.startswith("x-cos-"):
                result[k] = v
        return result

    def _build_url(self, bucket: str, object_name: str) -> str:
        """构建对象永久 URL。"""
        host = self._get_host(bucket)
        # object_name 需要 URL 编码，但保留 /
        encoded_name = quote(object_name, safe="/-_.~")
        return f"https://{host}/{encoded_name}"

    def _get_presigned_url(
        self,
        bucket: str,
        object_name: str,
        method: str = "GET",
        expires_in: int = 3600,
    ) -> str:
        """生成预签名 URL。"""
        host = self._get_host(bucket)
        path = "/" + object_name if not object_name.startswith("/") else object_name
        encoded_path = quote(path, safe="/-_.~")

        # 签名需要包含 host
        headers = {"host": host}
        auth = self._sign(method, path, params={}, headers=headers, expire=expires_in)

        return f"https://{host}{encoded_path}?{auth}"

    # ==================== IStorage 接口实现 ====================

    async def list_objects(
        self,
        prefix: str = "",
        *,
        bucket_name: str | None = None,
    ) -> list[str]:
        """列出对象名（按 prefix 过滤）。"""
        session = await self._ensure_session()
        bucket = self._get_bucket(bucket_name)
        host = self._get_host(bucket)
        path = "/"
        params: dict[str, str] = {}
        if prefix:
            params["prefix"] = prefix
        params["max-keys"] = "1000"

        objects: list[str] = []
        marker = ""

        while True:
            if marker:
                params["marker"] = marker
            elif "marker" in params:
                params.pop("marker", None)

            query = urlencode(params)
            url = f"https://{host}/?{query}"
            headers: dict[str, str] = {"host": host}
            if self._config.session_token:
                headers["x-cos-security-token"] = self._config.session_token
            headers["authorization"] = self._sign("GET", path, params=params, headers=headers)

            try:
                async with session.get(url, headers=headers) as response:
                    if response.status >= 400:
                        text = await response.text()
                        raise StorageBackendError(f"COS 列表失败: {response.status} {text}")
                    xml_text = await response.text()
            except aiohttp.ClientError as e:
                raise StorageBackendError(f"COS 请求失败: {e}") from e

            root = ET.fromstring(xml_text)
            for item in root.iter():
                tag = item.tag.rsplit("}", 1)[-1]
                if tag == "Key" and item.text:
                    objects.append(item.text)

            is_truncated = any(
                node.text and node.text.lower() == "true"
                for node in root.iter()
                if node.tag.rsplit("}", 1)[-1] == "IsTruncated"
            )
            next_marker = ""
            for node in root.iter():
                if node.tag.rsplit("}", 1)[-1] == "NextMarker" and node.text:
                    next_marker = node.text
                    break

            if not is_truncated:
                break
            marker = next_marker or (objects[-1] if objects else "")
            if not marker:
                break

        return objects

    async def upload_file(
        self,
        file: StorageFile,
        *,
        bucket_name: str | None = None,
    ) -> UploadResult:
        """上传文件。"""
        session = await self._ensure_session()
        bucket = self._get_bucket(bucket_name or file.bucket_name)
        data = self._read_file_data(file)

        host = self._get_host(bucket)
        path = "/" + file.object_name if not file.object_name.startswith("/") else file.object_name
        url = f"https://{host}{quote(path, safe='/-_.~')}"

        # 构建请求头
        headers: dict[str, str] = {
            "host": host,
            "content-length": str(len(data)),
        }
        if file.content_type:
            headers["content-type"] = file.content_type

        # 添加自定义元数据
        if file.metadata:
            for k, v in file.metadata.items():
                headers[f"x-cos-meta-{k}"] = v

        # 添加 STS Token
        if self._config.session_token:
            headers["x-cos-security-token"] = self._config.session_token

        # 生成签名
        headers["authorization"] = self._sign("PUT", path, params={}, headers=headers)

        try:
            async with session.put(url, data=data, headers=headers) as response:
                if response.status >= 400:
                    text = await response.text()
                    raise StorageBackendError(f"COS 上传失败: {response.status} {text}")
                etag = response.headers.get("etag", "").strip('"')
        except aiohttp.ClientError as e:
            raise StorageBackendError(f"COS 请求失败: {e}") from e

        return UploadResult(
            url=self._build_url(bucket, file.object_name),
            bucket_name=bucket,
            object_name=file.object_name,
            etag=etag or None,
        )

    async def upload_files(
        self,
        files: list[StorageFile],
        *,
        bucket_name: str | None = None,
    ) -> list[UploadResult]:
        """批量上传文件。"""
        tasks = [self.upload_file(f, bucket_name=bucket_name) for f in files]
        return await asyncio.gather(*tasks)

    async def delete_file(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
    ) -> None:
        """删除文件。"""
        session = await self._ensure_session()
        bucket = self._get_bucket(bucket_name)

        host = self._get_host(bucket)
        path = "/" + object_name if not object_name.startswith("/") else object_name
        url = f"https://{host}{quote(path, safe='/-_.~')}"

        headers: dict[str, str] = {"host": host}
        if self._config.session_token:
            headers["x-cos-security-token"] = self._config.session_token
        headers["authorization"] = self._sign("DELETE", path, params={}, headers=headers)

        try:
            async with session.delete(url, headers=headers) as response:
                # 404 也算成功（文件本来就不存在）
                if response.status not in (200, 204, 404):
                    text = await response.text()
                    raise StorageBackendError(f"COS 删除失败: {response.status} {text}")
        except aiohttp.ClientError as e:
            raise StorageBackendError(f"COS 请求失败: {e}") from e

    async def get_file_url(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
        expires_in: int | None = None,
    ) -> str:
        """获取文件 URL。"""
        bucket = self._get_bucket(bucket_name)

        if expires_in:
            return self._get_presigned_url(bucket, object_name, "GET", expires_in)
        return self._build_url(bucket, object_name)

    async def file_exists(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
    ) -> bool:
        """检查文件是否存在。"""
        session = await self._ensure_session()
        bucket = self._get_bucket(bucket_name)

        host = self._get_host(bucket)
        path = "/" + object_name if not object_name.startswith("/") else object_name
        url = f"https://{host}{quote(path, safe='/-_.~')}"

        headers: dict[str, str] = {"host": host}
        if self._config.session_token:
            headers["x-cos-security-token"] = self._config.session_token
        headers["authorization"] = self._sign("HEAD", path, params={}, headers=headers)

        try:
            async with session.head(url, headers=headers) as response:
                return response.status == 200
        except aiohttp.ClientError:
            return False

    async def download_file(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
    ) -> bytes:
        """下载文件。"""
        session = await self._ensure_session()
        bucket = self._get_bucket(bucket_name)

        host = self._get_host(bucket)
        path = "/" + object_name if not object_name.startswith("/") else object_name
        url = f"https://{host}{quote(path, safe='/-_.~')}"

        headers: dict[str, str] = {"host": host}
        if self._config.session_token:
            headers["x-cos-security-token"] = self._config.session_token
        headers["authorization"] = self._sign("GET", path, params={}, headers=headers)

        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 404:
                    raise StorageNotFoundError(f"文件不存在: {object_name}")
                if response.status >= 400:
                    text = await response.text()
                    raise StorageBackendError(f"COS 下载失败: {response.status} {text}")
                return await response.read()
        except aiohttp.ClientError as e:
            raise StorageBackendError(f"COS 请求失败: {e}") from e

    async def append_file(
        self,
        object_name: str,
        data: bytes,
        *,
        bucket_name: str | None = None,
        position: int | None = None,
    ) -> int:
        """追加内容到文件（使用 COS APPEND Object 接口）。

        COS APPEND Object 文档: https://cloud.tencent.com/document/product/436/7741

        Args:
            object_name: 对象名
            data: 追加的数据
            bucket_name: 桶名
            position: 追加位置（None 表示自动获取当前文件大小）

        Returns:
            下一次追加的位置
        """
        session = await self._ensure_session()
        bucket = self._get_bucket(bucket_name)

        # 如果没有指定 position，先获取当前文件大小
        if position is None:
            position = await self._get_file_size(bucket, object_name)

        host = self._get_host(bucket)
        path = "/" + object_name if not object_name.startswith("/") else object_name

        # 构建 URL 和参数
        params = {"append": "", "position": str(position)}
        url = f"https://{host}{quote(path, safe='/-_.~')}?append=&position={position}"

        # 构建请求头
        headers: dict[str, str] = {
            "host": host,
            "content-length": str(len(data)),
        }
        if self._config.session_token:
            headers["x-cos-security-token"] = self._config.session_token

        # 生成签名（注意：append 和 position 参数需要参与签名）
        headers["authorization"] = self._sign("POST", path, params=params, headers=headers)

        try:
            async with session.post(url, data=data, headers=headers) as response:
                if response.status >= 400:
                    text = await response.text()
                    raise StorageBackendError(f"COS 追加失败: {response.status} {text}")
                # 返回下一次追加的位置
                next_position = response.headers.get("x-cos-next-append-position")
                if next_position:
                    return int(next_position)
                return position + len(data)
        except aiohttp.ClientError as e:
            raise StorageBackendError(f"COS 请求失败: {e}") from e

    async def _get_file_size(self, bucket: str, object_name: str) -> int:
        """获取文件大小，如果文件不存在返回 0。"""
        session = await self._ensure_session()
        host = self._get_host(bucket)
        path = "/" + object_name if not object_name.startswith("/") else object_name
        url = f"https://{host}{quote(path, safe='/-_.~')}"

        headers: dict[str, str] = {"host": host}
        if self._config.session_token:
            headers["x-cos-security-token"] = self._config.session_token
        headers["authorization"] = self._sign("HEAD", path, params={}, headers=headers)

        try:
            async with session.head(url, headers=headers) as response:
                if response.status == 200:
                    content_length = response.headers.get("content-length")
                    return int(content_length) if content_length else 0
                return 0
        except aiohttp.ClientError:
            return 0

    async def close(self) -> None:
        """关闭 aiohttp 会话。"""
        if self._session:
            await self._session.close()
            self._session = None


__all__ = [
    "COSStorage",
]
