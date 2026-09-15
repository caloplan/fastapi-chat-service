"""基础设施接入测试：Redis / PydanticAI 演示端点。

通过 dependency_overrides 注入 mock，不发起真实 Redis 连接 / AI 调用。
同时验证服务名白名单在基础设施端点同样生效。
"""

from types import SimpleNamespace

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
    """模拟 PydanticAI Agent（run 返回带 output/usage/all_messages 的结果）。"""

    def __init__(self, output: str = "你好，我是 Chat Service 助手。") -> None:
        self._output = output

    @staticmethod
    def _usage() -> SimpleNamespace:
        return SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15)

    async def run(self, message: str, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            output=self._output,
            usage=self._usage,
            all_messages=lambda: [],
        )


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

async def test_ai_chat_ok(client, monkeypatch):
    monkeypatch.setattr("app.ai.service.get_ai_agent", lambda: _FakeAgent(output="这是测试回复"))
    resp = await client.post(
        "/api/v1/ai/chat",
        headers=_auth_headers(service_name="default"),
        json={"message": "你好"},
    )
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


def test_extract_tool_results_str_args_regression():
    """回归：pydantic-ai 2.x ToolCallPart.args 为 JSON 字符串，须规范化为 dict。"""
    from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart

    from app.ai.service import _extract_tool_results
    call = ToolCallPart(tool_name="create_food", args='{"name": "苹果"}', tool_call_id="c1")
    ret = ToolReturnPart(tool_name="create_food", content="ok", tool_call_id="c1")
    results = _extract_tool_results([ModelResponse(parts=[call]), ModelResponse(parts=[ret])])
    assert len(results) == 1
    assert results[0].name == "create_food"
    assert results[0].arguments == {"name": "苹果"}
    assert results[0].result == "ok"


# ── 多模态历史：content 数组（含 image_url）兼容 ─────────────

def test_build_message_history_supports_image_url_content():
    """user 消息 content 数组（text + image_url）→ TextContent + ImageUrl parts；assistant 数组拼接文本。"""
    from pydantic_ai.messages import ImageUrl, TextContent, TextPart, UserPromptPart

    from app.ai.service import build_message_history
    from app.schemas.ai import ChatMessage, ContentBlock, MessageRole

    history = [
        ChatMessage(
            role=MessageRole.user,
            content=[
                ContentBlock(type="text", text="看这张图"),
                ContentBlock(type="image_url", image_url={"url": "data:image/jpeg;base64,AAAA"}),
            ],
        ),
        ChatMessage(role=MessageRole.assistant, content="看到了"),
    ]
    msgs = build_message_history(history)
    assert len(msgs) == 2

    user_part = msgs[0].parts[0]
    assert isinstance(user_part, UserPromptPart)
    assert isinstance(user_part.content, list)
    assert isinstance(user_part.content[0], TextContent)
    assert user_part.content[0].content == "看这张图"
    assert isinstance(user_part.content[1], ImageUrl)
    assert user_part.content[1].url == "data:image/jpeg;base64,AAAA"

    # assistant 仅文本（拼接 text 块）
    assert isinstance(msgs[1].parts[0], TextPart)
    assert msgs[1].parts[0].content == "看到了"


def test_build_message_history_image_url_as_plain_string():
    """image_url 块兼容字符串形式（非 {"url": ...} 包裹）。"""
    from pydantic_ai.messages import ImageUrl

    from app.ai.service import build_message_history
    from app.schemas.ai import ChatMessage, ContentBlock, MessageRole

    history = [
        ChatMessage(
            role=MessageRole.user,
            content=[ContentBlock(type="image_url", image_url="https://example.com/a.jpg")],
        ),
    ]
    msgs = build_message_history(history)
    part = msgs[0].parts[0]
    assert isinstance(part.content[0], ImageUrl)
    assert part.content[0].url == "https://example.com/a.jpg"


async def test_ai_chat_accepts_image_url_history(client, monkeypatch):
    """端到端：请求体 history 带 image_url content 数组 → 200，且历史正确含 ImageUrl part。"""
    from pydantic_ai.messages import ImageUrl

    seen: dict[str, object] = {}

    class _Agent:
        async def run(self, message: str, **kwargs: object) -> SimpleNamespace:
            seen["history"] = kwargs.get("message_history", [])
            return SimpleNamespace(
                output="ok",
                usage=lambda: SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
                all_messages=lambda: [],
            )

    monkeypatch.setattr("app.ai.service.get_ai_agent", lambda: _Agent())
    resp = await client.post(
        "/api/v1/ai/chat",
        headers=_auth_headers(service_name="default"),
        json={
            "message": "继续",
            "history": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "看看这张图"},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BBBB"}},
                    ],
                }
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["reply"] == "ok"
    hist = seen["history"]
    assert len(hist) == 1
    parts = hist[0].parts[0].content
    assert any(isinstance(p, ImageUrl) for p in parts)
