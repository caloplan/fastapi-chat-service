"""审批流测试：taskid 生成/存储/消费、批准执行、拒绝不执行、过期 410、越权 403。

通过 monkeypatch 替换 app.ai.service 模块中的 get_ai_agent / get_redis
（service 层为直接 import 调用，不走 FastAPI Depends），
验证完整审批链路（不发起真实连接/调用）。
"""

import json
from types import SimpleNamespace

import pytest
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from tests.conftest import create_test_token

# Redis Key 前缀与 config.REDIS_PREFIX 一致
_PREFIX = "chat_service"


class _FakeRedis:
    """内存版 redis.asyncio.Redis（set/get/delete/eval）。

    eval 模拟 service 中 Lua 脚本语义：0=不存在，-1=创建者不匹配（保留），
    其他=返回快照 JSON 并原子删除（消费）。
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> str | int:
        """模拟 redis-py 的 eval(script, numkeys, *keys_and_args) 可变参数形态。"""
        key = keys_and_args[0]
        user_id = keys_and_args[1]
        raw = self._store.get(key)
        if raw is None:
            return 0
        if str(json.loads(raw)["user_id"]) != user_id:
            return -1
        del self._store[key]
        return raw


class _ApprovalAgent:
    """模拟 Agent：首次 run 返回需审批调用，续跑按审批决定返回结果。"""

    def __init__(self) -> None:
        self.calls = 0

    @staticmethod
    def _usage() -> SimpleNamespace:
        return SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15)

    async def run(self, prompt: str | None, **kwargs: object) -> SimpleNamespace:
        self.calls += 1
        deferred: DeferredToolResults | None = kwargs.get("deferred_tool_results")
        if self.calls == 1:
            dtr = DeferredToolRequests(
                calls=[],
                approvals=[
                    ToolCallPart(
                        tool_name="deploy_service",
                        args={"service_name": "nginx"},
                        tool_call_id="call-1",
                    )
                ],
            )
            return SimpleNamespace(
                output=dtr,
                usage=self._usage,
                all_messages=lambda: [ModelRequest(parts=[UserPromptPart(content="帮我部署 nginx")])],
            )
        # 续跑：根据审批决定分支
        if deferred and deferred.approvals.get("call-1") is False:
            return SimpleNamespace(output="已拒绝执行部署操作", usage=self._usage, all_messages=lambda: [])
        return SimpleNamespace(
            output="部署已完成",
            usage=self._usage,
            all_messages=lambda: [
                ModelResponse(
                    parts=[
                        ToolReturnPart(
                            tool_name="deploy_service",
                            content="deployed:nginx",
                            tool_call_id="call-1",
                        )
                    ]
                )
            ],
        )


def _auth_headers(user_id: int = 1, username: str = "testuser") -> dict[str, str]:
    token = create_test_token(user_id=user_id, username=username, service_name="default")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fake_redis(monkeypatch):
    fr = _FakeRedis()

    async def _fake_get_redis():
        return fr

    monkeypatch.setattr("app.ai.service.get_redis", _fake_get_redis)
    return fr


@pytest.fixture
def fake_agent(monkeypatch):
    fa = _ApprovalAgent()
    monkeypatch.setattr("app.ai.service.get_ai_agent", lambda: fa)
    return fa


# ── 全链路：触发审批 → 批准执行 → 消费 taskid ──────────────

async def test_approval_full_flow(client, fake_redis, fake_agent):
    resp = await client.post(
        "/api/v1/ai/chat",
        headers=_auth_headers(),
        json={"message": "帮我部署 nginx"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["need_approval"] is True
    assert data["taskid"]
    assert data["pending_tools"][0]["name"] == "deploy_service"
    assert data["pending_tools"][0]["arguments"] == {"service_name": "nginx"}
    assert data["reply"] == ""
    taskid = data["taskid"]

    # taskid 已写入 Redis（快照含发起者 user_id）
    key = f"{_PREFIX}:approval:{taskid}"
    raw = await fake_redis.get(key)
    assert raw is not None
    snapshot = json.loads(raw)
    assert snapshot["user_id"] == 1
    assert snapshot["calls"][0]["tool_call_id"] == "call-1"

    # 批准执行
    resp2 = await client.post(
        "/api/v1/ai/approval",
        headers=_auth_headers(),
        json={"taskid": taskid, "approved": True},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["approved"] is True
    assert data2["reply"] == "部署已完成"
    assert data2["tool_results"][0]["name"] == "deploy_service"
    assert data2["tool_results"][0]["result"] == "deployed:nginx"

    # taskid 已消费（删除）
    assert await fake_redis.get(key) is None


# ── 拒绝分支：tool 不执行 ──────────────────────────────────

async def test_approval_deny(client, fake_redis, fake_agent):
    resp = await client.post(
        "/api/v1/ai/chat",
        headers=_auth_headers(),
        json={"message": "帮我部署 nginx"},
    )
    taskid = resp.json()["taskid"]

    resp2 = await client.post(
        "/api/v1/ai/approval",
        headers=_auth_headers(),
        json={"taskid": taskid, "approved": False},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["approved"] is False
    assert "拒绝" in data2["reply"]
    assert data2["tool_results"] == []


# ── 过期 / 不存在：410 ─────────────────────────────────────

async def test_approval_expired(client, fake_redis):
    resp = await client.post(
        "/api/v1/ai/approval",
        headers=_auth_headers(),
        json={"taskid": "no-such-task", "approved": True},
    )
    assert resp.status_code == 410
    assert "过期" in resp.json()["detail"]


# ── 重复提交：同一 taskid 消费后再次提交 → 410（防 tool 重复执行）──

async def test_approval_replay_rejected(client, fake_redis, fake_agent):
    resp = await client.post(
        "/api/v1/ai/chat",
        headers=_auth_headers(),
        json={"message": "帮我部署 nginx"},
    )
    taskid = resp.json()["taskid"]

    resp1 = await client.post(
        "/api/v1/ai/approval",
        headers=_auth_headers(),
        json={"taskid": taskid, "approved": True},
    )
    assert resp1.status_code == 200

    # 同一 taskid 重复提交：已被原子消费 → 410，且不再触发续跑
    resp2 = await client.post(
        "/api/v1/ai/approval",
        headers=_auth_headers(),
        json={"taskid": taskid, "approved": True},
    )
    assert resp2.status_code == 410
    assert fake_agent.calls == 2  # 1 次对话 + 1 次审批续跑，未重复执行


# ── 越权：taskid 归属他人 → 403 ────────────────────────────

async def test_approval_forbidden_other_user(client, fake_redis):
    key = f"{_PREFIX}:approval:task-owner-999"
    await fake_redis.set(
        key,
        json.dumps({"user_id": 999, "conversation_id": "c1", "message_history": [], "calls": []}),
    )
    resp = await client.post(
        "/api/v1/ai/approval",
        headers=_auth_headers(user_id=1, username="alice"),
        json={"taskid": "task-owner-999", "approved": True},
    )
    assert resp.status_code == 403
    # 越权未消费，taskid 保留
    assert await fake_redis.get(key) is not None


# ── 认证 / 白名单边界 ──────────────────────────────────────

async def test_ai_chat_requires_auth(client):
    resp = await client.post("/api/v1/ai/chat", json={"message": "你好"})
    assert resp.status_code == 401


async def test_ai_approval_requires_auth(client):
    resp = await client.post("/api/v1/ai/approval", json={"taskid": "x", "approved": True})
    assert resp.status_code == 401
