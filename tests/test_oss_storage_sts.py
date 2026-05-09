from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from io import BytesIO

import pytest

from aury.sdk.storage.storage.factory import StorageFactory, StorageType
from aury.sdk.storage.storage.models import StorageBackend, StorageConfig, StorageFile
from aury.sdk.storage.storage.oss import OSSStorage
from aury.sdk.storage.sts import ProviderType, STSProviderFactory
from aury.sdk.storage.sts.models import (
    ActionType,
    OSSSTSConfig,
    STSRequest,
)
from aury.sdk.storage.sts.policy import AliyunPolicyBuilder
from aury.sdk.storage.sts.providers.aliyun import AliyunSTSProvider


class _ThreadRecordingBytesIO(BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.read_thread_name: str | None = None

    def read(self, *args, **kwargs) -> bytes:
        self.read_thread_name = threading.current_thread().name
        return super().read(*args, **kwargs)


@pytest.mark.asyncio
async def test_oss_upload_streams_file_like_data_off_event_loop() -> None:
    main_thread = threading.current_thread().name
    stream = _ThreadRecordingBytesIO(b"oss-payload")
    seen: dict[str, object] = {}
    uploaded_chunks: list[bytes] = []

    class _Response:
        status = 200
        headers = {"etag": '"etag-oss"'}

        def __init__(self, data) -> None:
            self._data = data

        async def __aenter__(self):
            async for chunk in self._data:
                uploaded_chunks.append(chunk)
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def text(self) -> str:
            return ""

    class _Session:
        def put(self, url, *, data, headers):
            seen["url"] = url
            seen["data"] = data
            seen["headers"] = headers
            return _Response(data)

    storage = OSSStorage(
        StorageConfig(
            backend=StorageBackend.OSS,
            bucket_name="bucket",
            region="cn-hangzhou",
            access_key_id="id",
            access_key_secret="secret",
            session_token="token",
        )
    )

    async def _fake_session() -> _Session:
        return _Session()

    storage._ensure_session = _fake_session

    result = await storage.upload_file(StorageFile(object_name="x.txt", data=stream))

    headers = seen["headers"]
    assert result.etag == "etag-oss"
    assert result.url == "https://bucket.oss-cn-hangzhou.aliyuncs.com/x.txt"
    assert uploaded_chunks == [b"oss-payload"]
    assert headers["content-length"] == str(len(b"oss-payload"))
    assert headers["x-oss-security-token"] == "token"
    assert headers["authorization"].startswith("OSS id:")
    assert stream.read_thread_name is not None
    assert stream.read_thread_name != main_thread


def test_storage_factory_creates_native_oss_storage() -> None:
    storage = StorageFactory.create(
        StorageType.OSS,
        bucket_name="bucket",
        region="oss-cn-hangzhou",
        access_key_id="id",
        access_key_secret="secret",
    )

    assert isinstance(storage, OSSStorage)


def test_aliyun_policy_builder_limits_to_allowed_prefix() -> None:
    policy = AliyunPolicyBuilder().build(
        STSRequest(
            bucket="bucket",
            region="cn-hangzhou",
            allow_path="user/123",
            action_type=ActionType.WRITE,
        )
    )

    data = json.loads(policy)
    statement = data["Statement"][0]
    assert data["Version"] == "1"
    assert "oss:PutObject" in statement["Action"]
    assert "oss:GetObject" not in statement["Action"]
    assert statement["Resource"] == ["acs:oss:*:*:bucket/user/123/*"]


def test_oss_sts_provider_is_registered() -> None:
    provider = STSProviderFactory.create(
        ProviderType.OSS,
        access_key_id="id",
        access_key_secret="secret",
        role_arn="acs:ram::123:role/upload",
    )

    assert isinstance(provider, AliyunSTSProvider)


@pytest.mark.asyncio
async def test_aliyun_sts_provider_assume_role_request_and_response() -> None:
    seen: dict[str, object] = {}
    expiration = datetime(2026, 1, 1, tzinfo=timezone.utc)

    class _Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def json(self):
            return {
                "RequestId": "req",
                "Credentials": {
                    "AccessKeyId": "tmp-id",
                    "AccessKeySecret": "tmp-secret",
                    "SecurityToken": "tmp-token",
                    "Expiration": expiration.isoformat().replace("+00:00", "Z"),
                },
            }

    class _Session:
        def post(self, url, *, data, headers):
            seen["url"] = url
            seen["data"] = data
            seen["headers"] = headers
            return _Response()

    provider = AliyunSTSProvider(
        OSSSTSConfig(
            access_key_id="id",
            access_key_secret="secret",
            role_arn="acs:ram::123:role/upload",
            role_session_name="upload-session",
        )
    )

    async def _fake_session() -> _Session:
        return _Session()

    provider._get_session = _fake_session

    credentials = await provider.get_credentials(
        STSRequest(
            bucket="bucket",
            region="oss-cn-hangzhou",
            allow_path="user/123",
            action_type=ActionType.WRITE,
            duration_seconds=60,
        )
    )

    payload = seen["data"]
    policy = json.loads(payload["Policy"])
    assert seen["url"] == "https://sts.aliyuncs.com"
    assert payload["Action"] == "AssumeRole"
    assert payload["RoleArn"] == "acs:ram::123:role/upload"
    assert payload["RoleSessionName"] == "upload-session"
    assert payload["DurationSeconds"] == 900
    assert payload["Signature"]
    assert policy["Statement"][0]["Resource"] == ["acs:oss:*:*:bucket/user/123/*"]
    assert credentials.access_key_id == "tmp-id"
    assert credentials.secret_access_key == "tmp-secret"
    assert credentials.session_token == "tmp-token"
    assert credentials.endpoint == "https://oss-cn-hangzhou.aliyuncs.com"
    assert credentials.bucket == "bucket"
