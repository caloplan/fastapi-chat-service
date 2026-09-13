"""Pytest 共享 fixtures：测试用 RSA 密钥对、JWT 签发、JWKS mock 与客户端。

对齐 mservice-fastapi-metastorage 的测试约定：
测试中用本地生成的 RSA 密钥对模拟 user-service 签发 JWT，并 mock JWKS 拉取，
避免真实请求 user-service。本服务无数据库，无需数据库 fixture。
"""

import base64
import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from jose import jwt

# ── 测试环境变量（在导入 app 前设置）───────────────────────
os.environ["USER_SERVICE_URL"] = "http://localhost:8000"
os.environ["ALGORITHM"] = "RS256"
# 测试 superuser 使用 user_id=999, username=superuser, role=superuser（三重 AND 校验）
os.environ["SUPERUSER_USERNAMES"] = '["superuser"]'
os.environ["SUPERUSER_USER_IDS"] = "[999]"
# 服务名白名单：非 superuser 请求必须命中，否则 403（白名单相关用例单独用 forum 等未命中名）
os.environ["ALLOWED_SERVICE_NAMES"] = '["default", "chat"]'
# Redis / AI 配置（测试中依赖注入 mock，不发起真实连接/调用）
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["AI_PROVIDER"] = "deepseek"
os.environ["AI_MODEL_NAME"] = "deepseek-chat"
os.environ["AI_API_KEY"] = "test-key"
os.environ["AI_ENABLED_TOOLS"] = '["*"]'

from app.main import app  # noqa: E402

# ── 测试用 RSA 密钥对（模块级生成一次）────────────────────
_TEST_KID = "test-kid-001"
_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_key = _private_key.public_key()
_public_numbers = _public_key.public_numbers()


def _int_to_base64url(n: int) -> str:
    """将整数转为 base64url 编码（JWK 格式）。"""
    return base64.urlsafe_b64encode(
        n.to_bytes((n.bit_length() + 7) // 8, "big")
    ).rstrip(b"=").decode()


_TEST_JWK = {
    "kty": "RSA",
    "kid": _TEST_KID,
    "use": "sig",
    "alg": "RS256",
    "n": _int_to_base64url(_public_numbers.n),
    "e": _int_to_base64url(_public_numbers.e),
}

_TEST_PRIVATE_PEM = _private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)


def create_test_token(
    *,
    user_id: int = 1,
    username: str = "testuser",
    service_name: str = "default",
    role: str = "user",
    token_type: str = "access",
    expires_minutes: int = 30,
    kid: str = _TEST_KID,
) -> str:
    """签发测试用 JWT（使用测试私钥，RS256，携带 kid）。"""
    expire = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    payload = {
        "sub": username,
        "user_id": user_id,
        "service_name": service_name,
        "role": role,
        "type": token_type,
        "exp": expire,
    }
    return jwt.encode(
        payload,
        _TEST_PRIVATE_PEM,
        algorithm="RS256",
        headers={"kid": kid},
    )


@pytest.fixture(autouse=True)
def mock_jwks(monkeypatch):
    """mock JWKS 拉取，返回测试公钥（避免真实请求 user-service）。"""

    async def _fake_fetch_jwks():
        return {_TEST_KID: _TEST_JWK}

    monkeypatch.setattr("app.core.security._fetch_jwks", _fake_fetch_jwks)
    # 清空 JWKS 缓存
    import app.core.security as sec

    sec._jwks_cache = {}
    sec._jwks_cache_expires_at = 0.0


@pytest_asyncio.fixture
async def client():
    """提供异步测试客户端。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
