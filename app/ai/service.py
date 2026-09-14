"""AI 对话编排：普通对话 + 需审批 Tool 的 taskid 审批流。

方案 A（纯无状态）：对话历史由客户端全量回传，服务端零留存。
仅"需审批 Tool"这一瞬间，为支持后续批准/拒绝续跑，把消息快照与
待审批调用暂存 Redis（TTL=AI_APPROVAL_TTL_SECONDS，默认 300s）：
- 用户确认后提交 taskid → 服务端取快照续跑 → 删除 taskid（消费）；
- 期限内未处理 → TTL 自动过期作废；
- taskid 与发起者 user_id 绑定，防止越权执行。
"""

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from pydantic_ai import DeferredToolRequests, DeferredToolResults, ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart

from app.ai.agent import get_ai_agent
from app.ai.context import bind_ai_context
from app.core.config import settings
from app.core.redis import get_redis
from app.schemas.ai import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    MessageRole,
    PendingToolCall,
    ToolCallResult,
    UsageInfo,
)
from app.schemas.auth import CurrentUser
from app.utils.logger import get_logger

logger = get_logger("ai_service")


def build_message_history(history: list[ChatMessage]) -> list[ModelMessage]:
    """客户端回传历史 → pydantic-ai 消息。

    简化约定：仅转换 user / assistant 文本消息；system 提示由服务端
    AI_SYSTEM_PROMPT 管理；tool 消息历史由审批快照管理，无需客户端回传。
    """
    messages: list[ModelMessage] = []
    for msg in history:
        if msg.role == MessageRole.user:
            messages.append(ModelRequest(parts=[UserPromptPart(content=msg.content)]))
        elif msg.role == MessageRole.assistant:
            messages.append(ModelResponse(parts=[TextPart(content=msg.content)]))
    return messages


def _usage(usage: Any) -> UsageInfo:
    return UsageInfo(
        prompt_tokens=getattr(usage, "input_tokens", 0),
        completion_tokens=getattr(usage, "output_tokens", 0),
        total_tokens=getattr(usage, "total_tokens", 0),
    )


def _approval_key(taskid: str) -> str:
    return f"{settings.REDIS_PREFIX}:approval:{taskid}"


def _extract_tool_results(messages: Any) -> list[ToolCallResult]:
    """从运行消息中提取实际执行的工具调用。

    ToolCallPart 配对 ToolReturnPart 补全参数；审批续跑消息中可能只含
    ToolReturnPart（ToolCallPart 在首次 run 的消息里），此时单独生成记录。
    """
    calls: dict[str, ToolCallResult] = {}
    for msg in messages:
        for part in getattr(msg, "parts", []):
            if isinstance(part, ToolCallPart):
                calls[part.tool_call_id] = ToolCallResult(
                    id=part.tool_call_id,
                    name=part.tool_name,
                    arguments=part.args,
                    result=None,
                )
            elif isinstance(part, ToolReturnPart):
                existing = calls.get(part.tool_call_id)
                if existing is not None:
                    existing.result = part.content
                else:
                    calls[part.tool_call_id] = ToolCallResult(
                        id=part.tool_call_id,
                        name=part.tool_name,
                        arguments=None,
                        result=part.content,
                    )
    return [call for call in calls.values() if call.result is not None]


async def run_chat(user: CurrentUser, body: ChatRequest, token: str | None = None) -> ChatResponse:
    """执行一次对话：正常回复，或命中需审批 Tool 时返回 taskid 审批请求。

    token：当前请求的原始 JWT（Authorization 头），绑定到 AI 上下文供 tool
    透传调用 meta-service；审批快照不保存 token（方案 A 零留存）。
    """
    with bind_ai_context(token, user):
        agent = get_ai_agent()
        history = build_message_history(body.history)
        conversation_id = body.conversation_id or uuid.uuid4().hex

        start = time.perf_counter()
        try:
            result = await agent.run(body.message, message_history=history)
        except Exception as exc:
            logger.error("AI 对话失败: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"AI 调用失败: {exc}",
            )
        latency_ms = round((time.perf_counter() - start) * 1000, 2)

        output = result.output
        if isinstance(output, DeferredToolRequests):
            pending = [
                PendingToolCall(tool_call_id=p.tool_call_id, name=p.tool_name, arguments=p.args)
                for p in output.approvals
            ]
            taskid = uuid.uuid4().hex
            snapshot = {
                "user_id": user.user_id,  # 创建者：审批请求必须由同一用户提交（越权 403）
                "username": user.sub,  # 审计字段
                "conversation_id": conversation_id,
                "message_history": json.loads(ModelMessagesTypeAdapter.dump_json(result.all_messages())),
                "calls": [p.model_dump() for p in pending],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            redis = await get_redis()
            await redis.set(
                _approval_key(taskid),
                json.dumps(snapshot, ensure_ascii=False),
                ex=settings.AI_APPROVAL_TTL_SECONDS,
            )
            logger.info("需审批 Tool 已暂存: taskid=%s tools=%s ttl=%ss", taskid, [p.name for p in pending], settings.AI_APPROVAL_TTL_SECONDS)
            return ChatResponse(
                conversation_id=conversation_id,
                model=settings.AI_MODEL_NAME,
                usage=_usage(result.usage()),
                latency_ms=latency_ms,
                need_approval=True,
                taskid=taskid,
                pending_tools=pending,
            )

        return ChatResponse(
            conversation_id=conversation_id,
            reply=str(output or ""),
            model=settings.AI_MODEL_NAME,
            usage=_usage(result.usage()),
            latency_ms=latency_ms,
            tool_calls=_extract_tool_results(result.all_messages()),
        )


# Lua 原子消费审批快照：存在性检查 + 创建者(user_id)校验 + 删除一次完成。
# 返回值：0=不存在/已被消费；-1=创建者不匹配（key 保留，创建者可重试）；其他=快照 JSON。
# 相比 get→校验→delete 的非原子流程，可防止同一 taskid 并发提交导致 tool 重复执行。
_CONSUME_APPROVAL_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw then return 0 end
local snap = cjson.decode(raw)
if tostring(snap['user_id']) ~= ARGV[1] then return -1 end
redis.call('DEL', KEYS[1])
return raw
"""


async def resolve_approval(user: CurrentUser, body: ApprovalDecisionRequest, token: str | None = None) -> ApprovalDecisionResponse:
    """处理用户对 taskid 的执行决定：批准则执行 tool，拒绝则不执行，随后消费 taskid。

    token：当前请求的原始 JWT，续跑期间绑定到 AI 上下文（批准执行的 tool 需要
    用它调用 meta-service）。审批快照本身不保存 token。
    """
    with bind_ai_context(token, user):
        redis = await get_redis()
        key = _approval_key(body.taskid)
        raw = await redis.eval(_CONSUME_APPROVAL_SCRIPT, 1, key, str(user.user_id))
        if raw == 0:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="审批任务不存在或已过期",
            )
        if raw == -1:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权执行该审批任务",
            )
        snapshot = json.loads(raw)

        agent = get_ai_agent()
        messages = ModelMessagesTypeAdapter.validate_json(json.dumps(snapshot["message_history"]))
        decisions = {call["tool_call_id"]: body.approved for call in snapshot["calls"]}

        start = time.perf_counter()
        try:
            result = await agent.run(
                None,
                message_history=messages,
                deferred_tool_results=DeferredToolResults(approvals=decisions),
            )
        except Exception as exc:
            logger.error("审批续跑失败: taskid=%s err=%s", body.taskid, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"AI 调用失败: {exc}",
            )
        latency_ms = round((time.perf_counter() - start) * 1000, 2)

        logger.info("审批任务已消费: taskid=%s approved=%s", body.taskid, body.approved)

        return ApprovalDecisionResponse(
            conversation_id=snapshot.get("conversation_id") or body.taskid,
            reply=str(result.output or ""),
            model=settings.AI_MODEL_NAME,
            usage=_usage(result.usage()),
            latency_ms=latency_ms,
            approved=body.approved,
            tool_results=_extract_tool_results(result.all_messages()),
        )


__all__ = ["build_message_history", "resolve_approval", "run_chat"]
