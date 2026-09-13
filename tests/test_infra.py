"""基础设施接入测试：Redis / PydanticAI 演示端点。

通过 dependency_overrides 注入 mock，不发起真实 Redis 连接 / AI 调用。
同时验证服务名白名单在基础设施端点同样生效。
"""

from types import SimpleNamespace

from app.ai.agent import get_ai_agent
from app.core.redis import get_redis
from app.main import app
from tests.conftest import create_test_token


class _FakeRedis:
    """模拟 redis.asyncio.Redis 客户端（ping 可用/不可用两种状态）。"""

    def __init__(self, ok: bool = True) -> None:
        self._ok = ok

    async def ping(self) -> bool:
        if not self._ok:
            raise ConnectionError("connection refused")
        return True


class _FakeAgent:
    """模拟 PydanticAI Agent（run 返回带 output 的结果）。"""

    def __init__(self, output: str = "你好，我是 Chat Service 助手。") -> None:
        self._output = output

    async def run(self, message: str) -> SimpleNamespace:
        return SimpleNamespace(output=self._output)


def _auth_headers(**overrides: object) -> dict[str, str]:
    token = create_test_token(**overrides)
    return {"Authorization": f"Bearer {token}"}


# ── Redis 演示端点 ──────────────────────────────────────────

async def test_redis_ping_ok(client):
    app.dependency_overrides[get_redis] = lambda: _FakeRedis(ok=True)
    try:
        resp = await client.get("/api/v1/redis/ping", headers=_auth_headers(service_name="default"))
    finally:
        app.dependency_overrides.pop(get_redis, None)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "latency_ms" in data


async def test_redis_ping_unavailable(client):
    app.dependency_overrides[get_redis] = lambda: _FakeRedis(ok=False)
    try:
        resp = await client.get("/api/v1/redis/ping", headers=_auth_headers(service_name="default"))
    finally:
        app.dependency_overrides.pop(get_redis, None)
    assert resp.status_code == 503
    assert "Redis" in resp.json()["detail"]


async def test_redis_ping_requires_auth(client):
    resp = await client.get("/api/v1/redis/ping")
    assert resp.status_code == 401


async def test_redis_ping_disallowed_service_forbidden(client):
    """非 superuser 且服务名未命中白名单 → 403（白名单在基础设施端点同样生效）。"""
    resp = await client.get(
        "/api/v1/redis/ping",
        headers=_auth_headers(service_name="forum", role="user"),
    )
    assert resp.status_code == 403


# ── PydanticAI 演示端点 ─────────────────────────────────────

async def test_ai_chat_ok(client):
    app.dependency_overrides[get_ai_agent] = lambda: _FakeAgent(output="这是测试回复")
    try:
        resp = await client.post(
            "/api/v1/ai/chat",
            headers=_auth_headers(service_name="default"),
            json={"message": "你好"},
        )
    finally:
        app.dependency_overrides.pop(get_ai_agent, None)
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "这是测试回复"
    assert data["model"] == "deepseek-chat"


async def test_ai_chat_requires_auth(client):
    resp = await client.post("/api/v1/ai/chat", json={"message": "你好"})
    assert resp.status_code == 401


async def test_ai_chat_invalid_body(client):
    resp = await client.post(
        "/api/v1/ai/chat",
        headers=_auth_headers(service_name="default"),
        json={},
    )
    assert resp.status_code == 422
