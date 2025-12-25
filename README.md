# Aury Storage SDK

多云存储 SDK，支持 STS 临时凭证签发和 S3 兼容存储操作。

## 特性

- **STS 临时凭证签发**：支持腾讯云 COS（不依赖第三方 SDK，自实现 TC3-HMAC-SHA256 签名）
- **S3 兼容存储**：支持 AWS S3、MinIO、腾讯云 COS、阿里云 OSS 等
- **统一接口**：不同云厂商使用统一的 API
- **类型安全**：完整的 Pydantic 模型和类型注解
- **异步优先**：基于 asyncio，支持高并发场景

## 安装

```bash
# 基础安装（STS + 本地存储）
uv add aury-sdk-storage

# 包含 S3 存储支持
uv add "aury-sdk-storage[aws]"
```

## 快速开始

### STS 临时凭证

```python
import asyncio
from aury.sdk.storage.sts import (
    STSProviderFactory,
    ProviderType,
    STSRequest,
    ActionType,
)

async def main():
    # 创建腾讯云 STS Provider
    provider = STSProviderFactory.create(
        ProviderType.TENCENT,
        secret_id="your-secret-id",
        secret_key="your-secret-key",
    )

    # 获取临时凭证
    credentials = await provider.get_credentials(
        STSRequest(
            bucket="my-bucket-1250000000",
            region="ap-guangzhou",
            allow_path="user/123/",
            action_type=ActionType.WRITE,
            duration_seconds=900,
        )
    )

    print(f"AccessKeyId: {credentials.access_key_id}")
    print(f"SecretAccessKey: {credentials.secret_access_key}")
    print(f"SessionToken: {credentials.session_token}")
    print(f"Expiration: {credentials.expiration}")

asyncio.run(main())
```

### 存储操作

```python
import asyncio
from aury.sdk.storage.storage import (
    StorageConfig,
    StorageBackend,
    StorageFile,
    S3Storage,
)

async def main():
    config = StorageConfig(
        backend=StorageBackend.COS,
        bucket_name="my-bucket-1250000000",
        region="ap-guangzhou",
        endpoint="https://cos.ap-guangzhou.myqcloud.com",
        access_key_id="your-access-key",
        access_key_secret="your-secret-key",
        session_token="your-session-token",  # 可选，使用 STS 临时凭证时需要
    )

    storage = S3Storage(config)

    # 上传文件
    result = await storage.upload_file(
        StorageFile(
            object_name="user/123/test.txt",
            data=b"Hello, World!",
            content_type="text/plain",
        )
    )
    print(f"Uploaded: {result.url}")

    # 检查文件是否存在
    exists = await storage.file_exists("user/123/test.txt")
    print(f"Exists: {exists}")

    # 获取预签名 URL
    url = await storage.get_file_url(
        "user/123/test.txt",
        expires_in=3600,
    )
    print(f"Presigned URL: {url}")

asyncio.run(main())
```

## 核心概念

### STS 临时凭证

STS（Security Token Service）用于生成临时访问凭证，适合以下场景：
- 前端直传：后端签发临时凭证，前端使用 S3 SDK 直接上传
- 权限隔离：每个用户只能访问自己的目录
- 最小权限：只授予必要的操作权限

#### 支持的操作类型

```python
class ActionType(str, Enum):
    READ = "read"    # 读取：GetObject, HeadObject
    WRITE = "write"  # 写入：PutObject, PostObject, 分片上传等
    ALL = "all"      # 读写全部
```

#### 腾讯云 STS 两种模式

1. **GetFederationToken**（默认）：联合身份，不需要预建 Role，适合简单场景
2. **AssumeRole**：角色扮演，需要配置 `role_arn`，适合跨账号或更细粒度控制

```python
# AssumeRole 模式
provider = STSProviderFactory.create(
    ProviderType.TENCENT,
    secret_id="your-secret-id",
    secret_key="your-secret-key",
    role_arn="qcs::cam::uin/100000000001:roleName/my-role",  # 指定角色
)
```

### 策略翻译

`TencentPolicyBuilder` 自动将业务意图翻译为腾讯云 CAM Policy：

```python
# 输入
STSRequest(
    bucket="my-bucket-1250000000",
    region="ap-guangzhou",
    allow_path="user/123/",
    action_type=ActionType.WRITE,
)

# 输出 Policy
{
    "version": "2.0",
    "statement": [{
        "effect": "allow",
        "action": [
            "cos:PutObject",
            "cos:PostObject",
            "cos:InitiateMultipartUpload",
            "cos:ListMultipartUploads",
            "cos:ListParts",
            "cos:UploadPart",
            "cos:CompleteMultipartUpload",
            "cos:AbortMultipartUpload"
        ],
        "resource": ["qcs::cos:ap-guangzhou:uid/1250000000:my-bucket-1250000000/user/123/*"]
    }]
}
```

### 统一凭证格式

不同云厂商的凭证字段名不同，SDK 统一为 AWS 标准命名：

| SDK 字段 | AWS | 腾讯云 | 阿里云 |
|---------|-----|-------|-------|
| access_key_id | AccessKeyId | TmpSecretId | AccessKeyId |
| secret_access_key | SecretAccessKey | TmpSecretKey | AccessKeySecret |
| session_token | SessionToken | Token | SecurityToken |

前端可直接使用这些字段配置 AWS S3 SDK。

## 架构设计

```
aury.sdk.storage/
├── sts/                    # STS 临时凭证模块
│   ├── models.py          # Pydantic 数据模型
│   ├── policy.py          # 策略构建器（翻译业务意图为各厂商 Policy）
│   ├── provider.py        # Provider 抽象接口
│   ├── factory.py         # Provider 工厂
│   └── providers/
│       └── tencent.py     # 腾讯云实现（自实现 TC3 签名）
├── storage/               # 存储操作模块
│   ├── models.py          # Pydantic 数据模型
│   ├── base.py           # IStorage 接口 + LocalStorage
│   └── s3.py             # S3 兼容存储实现
└── exceptions.py          # 异常定义
```

### 三层抽象

1. **接口层**：统一的 `STSCredentials`、`STSRequest` 等数据结构
2. **策略层**：`PolicyBuilder` 将业务意图翻译为各厂商的 Policy JSON
3. **凭证层**：`ISTSProvider` 抽象 + 各厂商实现

### 为什么不依赖第三方云 SDK？

1. **依赖轻量**：只需 `httpx` + `pydantic`，无需安装各云厂商的 SDK
2. **统一体验**：所有云厂商使用相同的调用方式
3. **可控性强**：签名算法自己实现，便于调试和定制
4. **包体积小**：腾讯云 SDK 依赖众多，而我们只需要 STS 功能

## API 参考

### STSRequest

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|-----|------|-----|-------|------|
| bucket | str | ✓ | - | 桶名（腾讯云格式：name-appid） |
| region | str | ✓ | - | 区域（如 ap-guangzhou） |
| allow_path | str | - | "" | 允许访问的路径前缀 |
| action_type | ActionType | - | WRITE | 操作类型 |
| duration_seconds | int | - | 900 | 凭证有效期（60-43200秒） |

### STSCredentials

| 字段 | 类型 | 说明 |
|-----|------|------|
| access_key_id | str | 临时 AccessKeyId |
| secret_access_key | str | 临时 SecretAccessKey |
| session_token | str | 临时 SessionToken |
| expiration | datetime | 过期时间（UTC） |
| region | str | 区域 |
| endpoint | str | S3 端点 |
| bucket | str | 桶名 |

### TencentSTSConfig

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|-----|------|-----|-------|------|
| secret_id | str | ✓ | - | 腾讯云 SecretId |
| secret_key | str | ✓ | - | 腾讯云 SecretKey |
| region | str | - | ap-guangzhou | 默认区域 |
| role_arn | str | - | None | AssumeRole 模式的角色 ARN |
| appid | str | - | None | 腾讯云 AppId（可从 bucket 名解析） |

## 更多示例

### 完整的前端直传流程

```python
import asyncio
from aury.sdk.storage.sts import (
    STSProviderFactory,
    ProviderType,
    STSRequest,
    ActionType,
)

async def get_upload_credentials(user_id: str):
    """后端 API：为用户生成上传凭证"""
    provider = STSProviderFactory.create(
        ProviderType.TENCENT,
        secret_id="your-secret-id",
        secret_key="your-secret-key",
    )

    try:
        credentials = await provider.get_credentials(
            STSRequest(
                bucket="my-bucket-1250000000",
                region="ap-guangzhou",
                allow_path=f"user/{user_id}/",  # 每个用户只能访问自己的目录
                action_type=ActionType.WRITE,
                duration_seconds=1800,  # 30分钟
            )
        )

        # 返回给前端
        return {
            "credentials": {
                "accessKeyId": credentials.access_key_id,
                "secretAccessKey": credentials.secret_access_key,
                "sessionToken": credentials.session_token,
                "expiration": credentials.expiration.isoformat(),
            },
            "bucket": credentials.bucket,
            "region": credentials.region,
            "endpoint": credentials.endpoint,
            "allowPath": f"user/{user_id}/",
        }
```

### 不同操作类型

```python
from aury.sdk.storage.sts import STSRequest, ActionType

# 只读权限（下载/查看）
read_request = STSRequest(
    bucket="my-bucket-1250000000",
    region="ap-guangzhou",
    allow_path="public/",
    action_type=ActionType.READ,
)

# 只写权限（上传）
write_request = STSRequest(
    bucket="my-bucket-1250000000",
    region="ap-guangzhou",
    allow_path="uploads/",
    action_type=ActionType.WRITE,
)

# 读写全部权限
all_request = STSRequest(
    bucket="my-bucket-1250000000",
    region="ap-guangzhou",
    allow_path="workspace/",
    action_type=ActionType.ALL,
)

# 整个 bucket 的访问权限（谨慎使用）
full_access_request = STSRequest(
    bucket="my-bucket-1250000000",
    region="ap-guangzhou",
    allow_path="",  # 空字符串表示整个 bucket
    action_type=ActionType.ALL,
)
```

### 直接使用 Pydantic 配置

```python
from aury.sdk.storage.sts import (
    TencentSTSConfig,
    TencentSTSProvider,
    STSRequest,
)

# 从环境变量或配置文件加载
import os

config = TencentSTSConfig(
    secret_id=os.environ["TENCENT_SECRET_ID"],
    secret_key=os.environ["TENCENT_SECRET_KEY"],
    region="ap-guangzhou",
    appid="1250000000",  # 可选，也可从 bucket 名自动提取
)

# 直接实例化 Provider
provider = TencentSTSProvider(config)
credentials = await provider.get_credentials(
    STSRequest(bucket="my-bucket-1250000000", region="ap-guangzhou")
)
```

### AssumeRole 模式

```python
from aury.sdk.storage.sts import STSProviderFactory, ProviderType

# 使用 AssumeRole 模式（适合跨账号或更细粒度控制）
provider = STSProviderFactory.create(
    ProviderType.TENCENT,
    secret_id="your-secret-id",
    secret_key="your-secret-key",
    role_arn="qcs::cam::uin/100000000001:roleName/my-storage-role",
)
```

### 本地存储（开发测试）

```python
from aury.sdk.storage.storage import LocalStorage, StorageFile

# 本地文件系统存储，用于开发测试
storage = LocalStorage(base_path="./dev_storage")

# 上传文件
result = await storage.upload_file(
    StorageFile(
        object_name="images/avatar.png",
        data=open("avatar.png", "rb").read(),
        content_type="image/png",
    )
)
print(f"URL: {result.url}")  # file:///path/to/dev_storage/default/images/avatar.png

# 下载
content = await storage.download_file("images/avatar.png")

# 删除
await storage.delete_file("images/avatar.png")
```

### S3 兼容存储（腾讯云 COS）

```python
from aury.sdk.storage.storage import (
    S3Storage,
    StorageConfig,
    StorageBackend,
    StorageFile,
)

# 使用长期密钥
config = StorageConfig(
    backend=StorageBackend.COS,
    bucket_name="my-bucket-1250000000",
    region="ap-guangzhou",
    endpoint="https://cos.ap-guangzhou.myqcloud.com",
    access_key_id="your-secret-id",
    access_key_secret="your-secret-key",
)

storage = S3Storage(config)

# 上传带元数据的文件
result = await storage.upload_file(
    StorageFile(
        object_name="documents/report.pdf",
        data=open("report.pdf", "rb").read(),
        content_type="application/pdf",
        metadata={
            "author": "John",
            "version": "1.0",
        },
    )
)

# 批量上传
results = await storage.upload_files([
    StorageFile(object_name="img/1.jpg", data=b"..."),
    StorageFile(object_name="img/2.jpg", data=b"..."),
    StorageFile(object_name="img/3.jpg", data=b"..."),
])

# 获取预签名 URL（无需凭证即可访问）
url = await storage.get_file_url(
    "documents/report.pdf",
    expires_in=3600,  # 1小时有效
)
print(f"下载链接: {url}")
```

### 结合 STS 和存储

```python
from aury.sdk.storage.sts import (
    STSProviderFactory,
    ProviderType,
    STSRequest,
    ActionType,
)
from aury.sdk.storage.storage import (
    S3Storage,
    StorageConfig,
    StorageBackend,
    StorageFile,
)

async def upload_with_sts(user_id: str, file_data: bytes, filename: str):
    """server to server, 服务端直接上传，使用 STS 临时凭证"""

    # 1. 获取 STS 凭证
    sts_provider = STSProviderFactory.create(
        ProviderType.TENCENT,
        secret_id="your-secret-id",
        secret_key="your-secret-key",
    )

    credentials = await sts_provider.get_credentials(
        STSRequest(
            bucket="my-bucket-1250000000",
            region="ap-guangzhou",
            allow_path=f"user/{user_id}/",
            action_type=ActionType.WRITE,
        )
    )

    # 2. 使用临时凭证上传
    storage_config = StorageConfig(
        backend=StorageBackend.COS,
        bucket_name=credentials.bucket,
        region=credentials.region,
        endpoint=credentials.endpoint,
        access_key_id=credentials.access_key_id,
        access_key_secret=credentials.secret_access_key,
        session_token=credentials.session_token,
    )

    storage = S3Storage(storage_config)
    result = await storage.upload_file(
        StorageFile(
            object_name=f"user/{user_id}/{filename}",
            data=file_data,
        )
    )
    return result.url
```

### 自定义 Policy 构建

```python
from aury.sdk.storage.sts.policy import TencentPolicyBuilder
from aury.sdk.storage.sts.models import STSRequest, ActionType

# 默认 Policy 构建器
builder = TencentPolicyBuilder(appid="1250000000")

request = STSRequest(
    bucket="my-bucket-1250000000",
    region="ap-guangzhou",
    allow_path="user/123/",
    action_type=ActionType.WRITE,
)

policy_json = builder.build(request)
print(policy_json)
# 输出:
# {"version":"2.0","statement":[{"effect":"allow","action":["cos:PutObject",...],"resource":["qcs::cos:ap-guangzhou:uid/1250000000:my-bucket-1250000000/user/123/*"]}]}
```

### 自己实现 TC3 签名（调试用）

```python
from aury.sdk.storage.sts.providers.tencent import TencentTC3Signer

signer = TencentTC3Signer(
    secret_id="your-secret-id",
    secret_key="your-secret-key",
)

# 生成签名头
headers = signer.sign(
    action="GetFederationToken",
    payload={"Name": "test", "Policy": "{}", "DurationSeconds": 1800},
    region="ap-guangzhou",
)

# 返回的 headers 可直接用于 HTTP 请求
print(headers)
# {
#     'Host': 'sts.tencentcloudapi.com',
#     'Content-Type': 'application/json',
#     'X-TC-Action': 'GetFederationToken',
#     'X-TC-Timestamp': '1702540800',
#     'X-TC-Version': '2018-08-13',
#     'X-TC-Region': 'ap-guangzhou',
#     'Authorization': 'TC3-HMAC-SHA256 Credential=...',
# }
```

## 错误处理

```python
from aury.sdk.storage import (
    StorageSDKError,
    STSError,
    STSRequestError,
    StorageError,
    StorageBackendError,
)

try:
    credentials = await provider.get_credentials(request)
except STSRequestError as e:
    # API 调用失败（凭证错误、权限不足等）
    print(f"API 错误: [{e.code}] {e.message}")
    print(f"RequestId: {e.request_id}")  # 用于向腾讯云提工单
except STSError as e:
    # 其他 STS 错误（网络错误等）
    print(f"STS 错误: {e}")
except StorageSDKError as e:
    # SDK 基础错误
    print(f"SDK 错误: {e}")

# 存储错误处理
try:
    await storage.upload_file(file)
except StorageBackendError as e:
    print(f"存储后端错误: {e}")
except StorageError as e:
    print(f"存储错误: {e}")
```

## 与 FastAPI 集成

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from aury.sdk.storage.sts import (
    STSProviderFactory,
    ProviderType,
    STSRequest,
    ActionType,
    STSRequestError,
)

app = FastAPI()

# 全局 Provider（复用 HTTP 连接）
sts_provider = STSProviderFactory.create(
    ProviderType.TENCENT,
    secret_id="your-secret-id",
    secret_key="your-secret-key",
)

class UploadCredentialsResponse(BaseModel):
    access_key_id: str
    secret_access_key: str
    session_token: str
    expiration: str
    bucket: str
    region: str
    endpoint: str

@app.get("/api/upload-credentials")
async def get_upload_credentials(user_id: str) -> UploadCredentialsResponse:
    try:
        credentials = await sts_provider.get_credentials(
            STSRequest(
                bucket="my-bucket-1250000000",
                region="ap-guangzhou",
                allow_path=f"user/{user_id}/",
                action_type=ActionType.WRITE,
                duration_seconds=1800,
            )
        )
        return UploadCredentialsResponse(
            access_key_id=credentials.access_key_id,
            secret_access_key=credentials.secret_access_key,
            session_token=credentials.session_token,
            expiration=credentials.expiration.isoformat(),
            bucket=credentials.bucket,
            region=credentials.region,
            endpoint=credentials.endpoint,
        )
    except STSRequestError as e:
        raise HTTPException(status_code=500, detail=f"STS error: {e.message}")

```

## License

MIT
