"""阿里云 OSS 存储实现（纯 aiohttp 异步版本）。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import time
import xml.etree.ElementTree as ET
from email.utils import formatdate
from urllib.parse import quote, urlencode

import aiohttp

from aury.sdk.storage.exceptions import StorageBackendError, StorageNotFoundError

from .base import IStorage, get_storage_file_data_size, iter_storage_file_data_chunks
from .models import StorageConfig, StorageFile, UploadResult

_OSS_SUBRESOURCES = {
    "acl",
    "append",
    "bucketInfo",
    "cname",
    "comp",
    "cors",
    "delete",
    "encryption",
    "lifecycle",
    "location",
    "logging",
    "mime",
    "objectInfo",
    "objectMeta",
    "partNumber",
    "policy",
    "position",
    "referer",
    "requestPayment",
    "response-cache-control",
    "response-content-disposition",
    "response-content-encoding",
    "response-content-language",
    "response-content-type",
    "response-expires",
    "security-token",
    "style",
    "tagging",
    "torrent",
    "uploadId",
    "uploads",
    "versionId",
    "versioning",
    "versions",
    "website",
}


class OSSStorage(IStorage):
    """阿里云 OSS 存储实现。"""

    def __init__(self, config: StorageConfig) -> None:
        self._config = config
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保 aiohttp 会话已创建。"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=None, connect=30.0, sock_read=300.0)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _get_bucket(self, bucket_name: str | None) -> str:
        """获取桶名。"""
        bucket = bucket_name or self._config.bucket_name
        if not bucket:
            raise StorageBackendError("桶名未指定")
        return bucket

    def _get_endpoint_host(self) -> str:
        """获取不包含 bucket 的 OSS endpoint host。"""
        if self._config.endpoint:
            endpoint = self._config.endpoint
            if endpoint.startswith("https://"):
                endpoint = endpoint[8:]
            elif endpoint.startswith("http://"):
                endpoint = endpoint[7:]
            return endpoint.rstrip("/")

        if not self._config.region:
            raise StorageBackendError("Region 或 Endpoint 必须指定")
        region = self._config.region.removeprefix("oss-")
        return f"oss-{region}.aliyuncs.com"

    def _get_host(self, bucket: str) -> str:
        """获取请求 Host。"""
        endpoint_host = self._get_endpoint_host()
        if self._config.addressing_style == "path":
            return endpoint_host
        if endpoint_host.startswith(f"{bucket}.") or endpoint_host == bucket:
            return endpoint_host
        return f"{bucket}.{endpoint_host}"

    def _get_path(self, bucket: str, object_name: str = "") -> str:
        """获取 HTTP 请求 path。"""
        object_path = quote(object_name, safe="/-_.~")
        if self._config.addressing_style == "path":
            return f"/{bucket}/{object_path}" if object_path else f"/{bucket}/"
        return f"/{object_path}" if object_path else "/"

    def _build_url(self, bucket: str, object_name: str) -> str:
        """构建对象永久 URL。"""
        host = self._get_host(bucket)
        path = self._get_path(bucket, object_name)
        return f"https://{host}{path}"

    def _canonicalized_oss_headers(self, headers: dict[str, str]) -> str:
        """构建 CanonicalizedOSSHeaders。"""
        oss_headers: list[tuple[str, str]] = []
        for key, value in headers.items():
            key_lower = key.lower()
            if key_lower.startswith("x-oss-"):
                oss_headers.append((key_lower, " ".join(str(value).strip().split())))
        return "".join(f"{key}:{value}\n" for key, value in sorted(oss_headers))

    def _canonicalized_resource(
        self,
        bucket: str,
        object_name: str = "",
        params: dict[str, str] | None = None,
    ) -> str:
        """构建 CanonicalizedResource。"""
        resource = f"/{bucket}/{object_name}" if object_name else f"/{bucket}/"
        if not params:
            return resource

        subresources: list[tuple[str, str]] = []
        for key, value in params.items():
            if key in _OSS_SUBRESOURCES:
                subresources.append((key, value))
        if not subresources:
            return resource

        query_parts = [
            key if value == "" else f"{key}={value}"
            for key, value in sorted(subresources)
        ]
        return f"{resource}?{'&'.join(query_parts)}"

    def _signature(
        self,
        method: str,
        bucket: str,
        object_name: str = "",
        *,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
        date_or_expires: str | None = None,
    ) -> str:
        """计算 OSS V1 签名。"""
        access_key_secret = self._config.access_key_secret
        if not self._config.access_key_id or not access_key_secret:
            raise StorageBackendError("缺少访问密钥")

        content_md5 = headers.get("content-md5", "")
        content_type = headers.get("content-type", "")
        date_value = date_or_expires if date_or_expires is not None else headers.get("date", "")
        string_to_sign = (
            f"{method}\n"
            f"{content_md5}\n"
            f"{content_type}\n"
            f"{date_value}\n"
            f"{self._canonicalized_oss_headers(headers)}"
            f"{self._canonicalized_resource(bucket, object_name, params)}"
        )
        digest = hmac.new(
            access_key_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _authorization(
        self,
        method: str,
        bucket: str,
        object_name: str = "",
        *,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
    ) -> str:
        """生成 Authorization header。"""
        return f"OSS {self._config.access_key_id}:{self._signature(method, bucket, object_name, headers=headers, params=params)}"

    def _base_headers(self, bucket: str) -> dict[str, str]:
        """构建基础请求头。"""
        headers = {
            "host": self._get_host(bucket),
            "date": formatdate(time.time(), usegmt=True),
        }
        if self._config.session_token:
            headers["x-oss-security-token"] = self._config.session_token
        return headers

    async def list_objects(
        self,
        prefix: str = "",
        *,
        bucket_name: str | None = None,
    ) -> list[str]:
        """列出对象名（按 prefix 过滤）。"""
        session = await self._ensure_session()
        bucket = self._get_bucket(bucket_name)
        params: dict[str, str] = {"max-keys": "1000"}
        if prefix:
            params["prefix"] = prefix

        objects: list[str] = []
        marker = ""
        while True:
            if marker:
                params["marker"] = marker
            else:
                params.pop("marker", None)

            headers = self._base_headers(bucket)
            headers["authorization"] = self._authorization("GET", bucket, headers=headers)
            url = f"https://{headers['host']}{self._get_path(bucket)}?{urlencode(params)}"

            try:
                async with session.get(url, headers=headers) as response:
                    if response.status >= 400:
                        text = await response.text()
                        raise StorageBackendError(f"OSS 列表失败: {response.status} {text}")
                    xml_text = await response.text()
            except aiohttp.ClientError as e:
                raise StorageBackendError(f"OSS 请求失败: {e}") from e

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
        content_length = await get_storage_file_data_size(file)
        data = file.data if isinstance(file.data, bytes) else iter_storage_file_data_chunks(file)

        headers = self._base_headers(bucket)
        headers["content-length"] = str(content_length)
        if file.content_type:
            headers["content-type"] = file.content_type
        if file.metadata:
            for key, value in file.metadata.items():
                headers[f"x-oss-meta-{key}"] = value
        headers["authorization"] = self._authorization(
            "PUT",
            bucket,
            file.object_name,
            headers=headers,
        )
        url = self._build_url(bucket, file.object_name)

        try:
            async with session.put(url, data=data, headers=headers) as response:
                if response.status >= 400:
                    text = await response.text()
                    raise StorageBackendError(f"OSS 上传失败: {response.status} {text}")
                etag = response.headers.get("etag", "").strip('"')
        except aiohttp.ClientError as e:
            raise StorageBackendError(f"OSS 请求失败: {e}") from e

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
        headers = self._base_headers(bucket)
        headers["authorization"] = self._authorization("DELETE", bucket, object_name, headers=headers)
        url = self._build_url(bucket, object_name)

        try:
            async with session.delete(url, headers=headers) as response:
                if response.status not in (200, 204, 404):
                    text = await response.text()
                    raise StorageBackendError(f"OSS 删除失败: {response.status} {text}")
        except aiohttp.ClientError as e:
            raise StorageBackendError(f"OSS 请求失败: {e}") from e

    async def get_file_url(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
        expires_in: int | None = None,
    ) -> str:
        """获取文件 URL。"""
        bucket = self._get_bucket(bucket_name)
        if not expires_in:
            return self._build_url(bucket, object_name)

        expires = str(int(time.time()) + expires_in)
        params = {
            "OSSAccessKeyId": self._config.access_key_id or "",
            "Expires": expires,
        }
        sign_params: dict[str, str] = {}
        if self._config.session_token:
            params["security-token"] = self._config.session_token
            sign_params["security-token"] = self._config.session_token

        signature = self._signature(
            "GET",
            bucket,
            object_name,
            headers={},
            params=sign_params,
            date_or_expires=expires,
        )
        params["Signature"] = signature
        return f"{self._build_url(bucket, object_name)}?{urlencode(params)}"

    async def file_exists(
        self,
        object_name: str,
        *,
        bucket_name: str | None = None,
    ) -> bool:
        """检查文件是否存在。"""
        session = await self._ensure_session()
        bucket = self._get_bucket(bucket_name)
        headers = self._base_headers(bucket)
        headers["authorization"] = self._authorization("HEAD", bucket, object_name, headers=headers)
        url = self._build_url(bucket, object_name)

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
        headers = self._base_headers(bucket)
        headers["authorization"] = self._authorization("GET", bucket, object_name, headers=headers)
        url = self._build_url(bucket, object_name)

        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 404:
                    raise StorageNotFoundError(f"文件不存在: {object_name}")
                if response.status >= 400:
                    text = await response.text()
                    raise StorageBackendError(f"OSS 下载失败: {response.status} {text}")
                return await response.read()
        except aiohttp.ClientError as e:
            raise StorageBackendError(f"OSS 请求失败: {e}") from e

    async def append_file(
        self,
        object_name: str,
        data: bytes,
        *,
        bucket_name: str | None = None,
        position: int | None = None,
    ) -> int:
        """追加内容到文件（使用 OSS AppendObject 接口）。"""
        session = await self._ensure_session()
        bucket = self._get_bucket(bucket_name)
        if position is None:
            position = await self._get_file_size(bucket, object_name)

        params = {"append": "", "position": str(position)}
        headers = self._base_headers(bucket)
        headers["content-length"] = str(len(data))
        headers["authorization"] = self._authorization(
            "POST",
            bucket,
            object_name,
            headers=headers,
            params=params,
        )
        url = f"{self._build_url(bucket, object_name)}?append=&position={position}"

        try:
            async with session.post(url, data=data, headers=headers) as response:
                if response.status >= 400:
                    text = await response.text()
                    raise StorageBackendError(f"OSS 追加失败: {response.status} {text}")
                next_position = response.headers.get("x-oss-next-append-position")
                return int(next_position) if next_position else position + len(data)
        except aiohttp.ClientError as e:
            raise StorageBackendError(f"OSS 请求失败: {e}") from e

    async def _get_file_size(self, bucket: str, object_name: str) -> int:
        """获取文件大小，如果文件不存在返回 0。"""
        session = await self._ensure_session()
        headers = self._base_headers(bucket)
        headers["authorization"] = self._authorization("HEAD", bucket, object_name, headers=headers)
        url = self._build_url(bucket, object_name)

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
    "OSSStorage",
]
