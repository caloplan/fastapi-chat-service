"""基础设施接入测试：Redis / PydanticAI 演示端点。

通过 dependency_overrides 注入 mock，不发起真实 Redis 连接 / AI 调用。
同时验证服务名白名单在基础设施端点同样生效。
"""

import json
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


class _StreamContext:
    """模拟 pydantic-ai run_stream 的返回值：异步上下文管理器（不可直接 await）。"""

    def __init__(self, result: "_StreamResult") -> None:
        self._result = result

    async def __aenter__(self) -> "_StreamResult":
        return self._result

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _StreamResult:
    """模拟 pydantic-ai StreamedRunResult（__aenter__ 产物）。"""

    def __init__(self, output: object, chunks: list[str], messages: list | None = None) -> None:
        self.output = output
        self._chunks = chunks
        self._messages = messages or []
        self.usage = SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15)

    async def stream_text(self, *, delta: bool = False, debounce_by: float | None = 0.1):
        # 真实 pydantic-ai：delta=True 时 yield 增量；mock 直接按增量块产出
        for c in self._chunks:
            yield c

    def all_messages(self) -> list:
        return self._messages


class _FakeAgent:
    """模拟 PydanticAI Agent（run 返回带 output/usage/all_messages 的结果）。"""

    def __init__(self, output: str = "你好，我是 Chat Service 助手。", chunks: list[str] | None = None) -> None:
        self._output = output
        self._chunks = chunks if chunks is not None else ([output] if output else [])

    @staticmethod
    def _usage() -> SimpleNamespace:
        return SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15)

    async def run(self, message: str, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            output=self._output,
            usage=self._usage,
            all_messages=lambda: [],
        )

    def run_stream(self, message: str, **kwargs: object) -> _StreamContext:
        # pydantic-ai 的 run_stream 为同步方法，返回 async context manager（不可直接 await）
        return _StreamContext(_StreamResult(self._output, self._chunks))


class _StreamApprovalAgent:
    """模拟 Agent：run_stream 无文本产出，output 为 DeferredToolRequests（命中审批）。"""

    @staticmethod
    def _usage() -> SimpleNamespace:
        return SimpleNamespace(input_tokens=20, output_tokens=0, total_tokens=20)

    def run_stream(self, message: str, **kwargs: object) -> _StreamContext:
        from pydantic_ai import DeferredToolRequests
        from pydantic_ai.messages import ModelResponse, ToolCallPart

        dtr = DeferredToolRequests(
            calls=[],
            approvals=[
                ToolCallPart(
                    tool_name="upsert_my_body",
                    args={"date": "2026-09-16", "weight": 75},
                    tool_call_id="call-1",
                )
            ],
        )
        # 审批 Tool 为 deferred 模式：消息中仅有 ToolCallPart（无 ToolReturnPart 配对）
        messages = [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="upsert_my_body",
                        args={"date": "2026-09-16", "weight": 75},
                        tool_call_id="call-1",
                    )
                ]
            )
        ]
        return _StreamContext(_StreamResult(dtr, [], messages=messages))


def _auth_headers(**overrides: object) -> dict[str, str]:
    token = create_test_token(**overrides)
    return {"Authorization": f"Bearer {token}"}


def _sse_events(text: str) -> list[dict]:
    """解析 SSE 响应体（data: <json> 帧）为事件列表。"""
    events = []
    for frame in text.strip().split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        assert frame.startswith("data: "), f"非法 SSE 帧: {frame!r}"
        events.append(json.loads(frame[6:]))
    return events


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


# ── SSE 流式对话（stream=true） ─────────────────────────────

async def test_ai_chat_stream_ok(client, monkeypatch):
    """stream=true 无审批：SSE 事件 = text 增量 × N + done（完整 reply）。"""
    monkeypatch.setattr(
        "app.ai.service.get_ai_agent",
        lambda: _FakeAgent(output="流式回复", chunks=["流式", "回复"]),
    )
    resp = await client.post(
        "/api/v1/ai/chat",
        headers=_auth_headers(service_name="default"),
        json={"message": "你好", "stream": True},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(resp.text)
    assert [e for e in events if e["type"] == "text"] == [
        {"type": "text", "content": "流式"},
        {"type": "text", "content": "回复"},
    ]
    done = events[-1]
    assert done["type"] == "done"
    assert done["reply"] == "流式回复"
    assert done["need_approval"] is False
    assert done["model"] == "deepseek-chat"
    assert done["usage"]["total_tokens"] == 15


async def test_ai_chat_stream_approval(client, monkeypatch):
    """stream=true 命中需审批 Tool：approval 事件 + done(need_approval=true)，无杂文本。"""
    monkeypatch.setattr("app.ai.service.get_ai_agent", lambda: _StreamApprovalAgent())

    class _StreamFakeRedis:
        def __init__(self) -> None:
            self.sets: list[tuple] = []

        async def set(self, key: str, value: str, ex: int | None = None) -> None:
            self.sets.append((key, value, ex))

    fr = _StreamFakeRedis()

    async def _fake_get_redis():
        return fr

    monkeypatch.setattr("app.ai.service.get_redis", _fake_get_redis)

    resp = await client.post(
        "/api/v1/ai/chat",
        headers=_auth_headers(service_name="default"),
        json={"message": "帮我记录今天体重 75kg", "stream": True},
    )
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    # 审批 Tool 为 deferred 模式：无任何 text 事件
    assert [e["type"] for e in events] == ["approval", "done"]
    approval = events[0]
    assert approval["pending_tools"][0]["name"] == "upsert_my_body"
    assert approval["pending_tools"][0]["arguments"] == {"date": "2026-09-16", "weight": 75}
    done = events[-1]
    assert done["type"] == "done"
    assert done["need_approval"] is True
    assert done["reply"] == ""
    assert done["taskid"] == approval["taskid"]
    assert done["pending_tools"] == approval["pending_tools"]
    # 快照已写入 Redis（TTL=AI_APPROVAL_TTL_SECONDS）
    assert len(fr.sets) == 1
    assert "approval:" in fr.sets[0][0]
    assert fr.sets[0][2] is not None


async def test_ai_chat_stream_default_json(client, monkeypatch):
    """stream 缺省（false）：仍返回普通 JSON，而非 SSE。"""
    monkeypatch.setattr("app.ai.service.get_ai_agent", lambda: _FakeAgent(output="普通回复"))
    resp = await client.post(
        "/api/v1/ai/chat",
        headers=_auth_headers(service_name="default"),
        json={"message": "你好"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["reply"] == "普通回复"


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


# ── 当前轮 message 支持内容块数组（text + image_url，url/base64） ───────────

async def test_ai_chat_message_content_blocks(client, monkeypatch):
    """当前轮 message 为 content 数组（text + image_url base64）→ prompt 转 TextContent/ImageUrl parts。"""
    from pydantic_ai.messages import ImageUrl, TextContent

    seen: dict[str, object] = {}

    class _Agent:
        async def run(self, message: object, **kwargs: object) -> SimpleNamespace:
            seen["prompt"] = message
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
            "message": [
                {"type": "text", "text": "分析这张图"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,CCCC"}},
            ]
        },
    )
    assert resp.status_code == 200
    prompt = seen["prompt"]
    assert isinstance(prompt, list)
    assert isinstance(prompt[0], TextContent)
    assert prompt[0].content == "分析这张图"
    assert isinstance(prompt[1], ImageUrl)
    assert prompt[1].url == "data:image/jpeg;base64,CCCC"


async def test_ai_chat_message_image_url_plain_string(client, monkeypatch):
    """message 的 image_url 兼容纯字符串 URL 形式（http(s) 外部链接）。"""
    from pydantic_ai.messages import ImageUrl

    seen: dict[str, object] = {}

    class _Agent:
        async def run(self, message: object, **kwargs: object) -> SimpleNamespace:
            seen["prompt"] = message
            return SimpleNamespace(
                output="ok",
                usage=lambda: SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
                all_messages=lambda: [],
            )

    monkeypatch.setattr("app.ai.service.get_ai_agent", lambda: _Agent())
    resp = await client.post(
        "/api/v1/ai/chat",
        headers=_auth_headers(service_name="default"),
        json={"message": [{"type": "image_url", "image_url": "https://example.com/a.jpg"}]},
    )
    assert resp.status_code == 200
    prompt = seen["prompt"]
    assert isinstance(prompt[1] if len(prompt) > 1 else prompt[0], ImageUrl)


async def test_ai_chat_stream_message_content_blocks(client, monkeypatch):
    """stream=true 时 message 数组同样转 parts 传入 run_stream。"""
    seen: dict[str, object] = {}

    class _Agent:
        def run_stream(self, message: object, **kwargs: object) -> _StreamContext:
            seen["prompt"] = message
            return _StreamContext(_StreamResult("ok", ["ok"]))

    monkeypatch.setattr("app.ai.service.get_ai_agent", lambda: _Agent())
    resp = await client.post(
        "/api/v1/ai/chat",
        headers=_auth_headers(service_name="default"),
        json={
            "message": [
                {"type": "image_url", "image_url": {"url": "https://example.com/b.jpg"}},
                {"type": "text", "text": "这是什么"},
            ],
            "stream": True,
        },
    )
    assert resp.status_code == 200
    assert _sse_events(resp.text)[-1]["type"] == "done"
    from pydantic_ai.messages import ImageUrl, TextContent

    prompt = seen["prompt"]
    assert isinstance(prompt, list)
    assert isinstance(prompt[0], ImageUrl)
    assert isinstance(prompt[1], TextContent)


async def test_ai_chat_message_empty_blocks_rejected(client):
    """message 内容块数组无有效块（如 image_url 为空）→ 422。"""
    resp = await client.post(
        "/api/v1/ai/chat",
        headers=_auth_headers(service_name="default"),
        json={"message": [{"type": "image_url", "image_url": None}]},
    )
    assert resp.status_code == 422
