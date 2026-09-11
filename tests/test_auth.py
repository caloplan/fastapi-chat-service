"""认证层测试：验证本服务正确消费 user-service 签发的 JWT（对齐 mservice-fastapi-user 协议）。

覆盖：
- 公开端点（/health、/）正常响应；
- get_current_user：有效 access token 解析正确；无 token / 伪造 / 过期 / refresh token /
  未知 kid / 缺 user_id → 401（HTTPException）；
- is_superuser / require_superuser：三重 AND 权限判定（role + 用户名白名单 + user_id 白名单）。
"""

import pytest
from fastapi import HTTPException

from app.core.dependencies import get_current_user, is_superuser, require_superuser
from app.schemas.auth import CurrentUser
from tests.conftest import create_test_token


# ── 公开端点 ──────────────────────────────────────────────

async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["service"] == "Chat Service"


async def test_root(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["docs"] == "/docs"
    assert "user_service" in data


# ── get_current_user：成功 ────────────────────────────────

async def test_get_current_user_valid():
    token = create_test_token(
        user_id=42,
        username="alice",
        service_name="forum",
        role="user",
    )
    user = await get_current_user(token)
    assert isinstance(user, CurrentUser)
    assert user.user_id == 42
    assert user.sub == "alice"
    assert user.service_name == "forum"
    assert user.role == "user"
    assert user.type == "access"


# ── get_current_user：失败（401）──────────────────────────

async def _assert_unauthorized(token: str | None):
    with pytest.raises(HTTPException) as exc:
        await get_current_user(token)
    assert exc.value.status_code == 401


async def test_get_current_user_no_token():
    await _assert_unauthorized(None)


async def test_get_current_user_fake_token():
    await _assert_unauthorized("not-a-real-token")


async def test_get_current_user_expired_token():
    await _assert_unauthorized(create_test_token(expires_minutes=-1))


async def test_get_current_user_refresh_token_rejected():
    await _assert_unauthorized(create_test_token(token_type="refresh"))


async def test_get_current_user_unknown_kid():
    await _assert_unauthorized(create_test_token(kid="unknown-kid-999"))


async def test_get_current_user_missing_user_id():
    """JWT payload 缺少 user_id 声明 → 401（伪造/残缺令牌）。"""
    from datetime import datetime, timedelta, timezone

    from jose import jwt as jose_jwt

    from tests.conftest import _TEST_PRIVATE_PEM

    payload = {
        "sub": "testuser",
        "role": "user",
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
    }
    token = jose_jwt.encode(payload, _TEST_PRIVATE_PEM, algorithm="RS256", headers={"kid": "test-kid-001"})
    await _assert_unauthorized(token)


# ── is_superuser：三重 AND 判定 ───────────────────────────

def test_is_superuser_all_match():
    user = CurrentUser(sub="superuser", user_id=999, service_name="default", role="superuser", type="access")
    assert is_superuser(user) is True


def test_is_superuser_role_mismatch():
    user = CurrentUser(sub="superuser", user_id=999, service_name="default", role="user", type="access")
    assert is_superuser(user) is False


def test_is_superuser_username_not_whitelisted():
    user = CurrentUser(sub="root", user_id=999, service_name="default", role="superuser", type="access")
    assert is_superuser(user) is False


def test_is_superuser_id_not_whitelisted():
    user = CurrentUser(sub="superuser", user_id=1000, service_name="default", role="superuser", type="access")
    assert is_superuser(user) is False


# ── require_superuser：依赖判定 ───────────────────────────

async def test_require_superuser_allowed():
    token = create_test_token(role="superuser", user_id=999, username="superuser")
    user = await require_superuser(await get_current_user(token))
    assert user.role == "superuser"


async def test_require_superuser_normal_user_forbidden():
    token = create_test_token(role="user", user_id=1, username="testuser")
    with pytest.raises(HTTPException) as exc:
        await require_superuser(await get_current_user(token))
    assert exc.value.status_code == 403
