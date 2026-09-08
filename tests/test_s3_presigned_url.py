from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from aury.sdk.storage.storage.models import StorageBackend, StorageConfig
from aury.sdk.storage.storage.s3 import S3Storage

pytest.importorskip("aioboto3")


@pytest.mark.asyncio
@pytest.mark.parametrize("addressing_style", ["path", "virtual"])
@pytest.mark.parametrize("session_token", [None, "test-session-token+/="])
@pytest.mark.parametrize(
    "object_name",
    ["reports/finance(1).xlsx", "reports/中文 报告(1)+%#?.xlsx"],
)
async def test_presigned_get_uses_sigv4(
    addressing_style: str,
    session_token: str | None,
    object_name: str,
) -> None:
    # Presigning uses the real botocore signer locally; no storage request is sent.
    storage = S3Storage(
        StorageConfig(
            backend=StorageBackend.AWS,
            bucket_name="test-bucket",
            region="us-east-1",
            endpoint="https://storage.example.invalid",
            addressing_style=addressing_style,
            access_key_id="test-access-key",
            access_key_secret="test-secret-key",
            session_token=session_token,
        )
    )

    url = await storage.get_file_url(object_name, expires_in=300)

    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert query.get("X-Amz-Algorithm") == ["AWS4-HMAC-SHA256"]
    credential = query["X-Amz-Credential"][0]
    assert credential.startswith("test-access-key/")
    assert credential.endswith("/us-east-1/s3/aws4_request")
    assert query["X-Amz-Expires"] == ["300"]
    assert query["X-Amz-SignedHeaders"] == ["host"]
    assert len(query["X-Amz-Signature"][0]) == 64
    assert not {"AWSAccessKeyId", "Signature", "Expires"}.intersection(query)
    if session_token is None:
        assert "X-Amz-Security-Token" not in query
    else:
        assert query["X-Amz-Security-Token"] == [session_token]

    if addressing_style == "path":
        assert parsed.hostname == "storage.example.invalid"
        assert unquote(parsed.path) == f"/test-bucket/{object_name}"
    else:
        assert parsed.hostname == "test-bucket.storage.example.invalid"
        assert unquote(parsed.path) == f"/{object_name}"
