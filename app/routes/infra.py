"""基础设施演示路由：Redis 连通性、PydanticAI 对话与审批流。

所有端点均要求认证（get_current_user 会同步执行 service_name
白名单校验，superuser 除外）。
"""

import time
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.ai.service import resolve_approval, run_chat, run_chat_stream
from app.core.dependencies import get_current_user
from app.core.redis import get_redis
from app.schemas.ai import ApprovalDecisionRequest, ApprovalDecisionResponse, ChatRequest, ChatResponse
from app.schemas.auth import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["基础设施"])


@router.get("/redis/ping", summary="Redis 连通性检查", description="需要认证；验证 Redis 基础框架接入。")
async def redis_ping(
    _: Annotated[CurrentUser, Depends(get_current_user)],
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> dict[str, str | float]:
    try:
        start = time.perf_counter()
        await redis.ping()
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Redis 不可用: {exc}",
        )
    return {"status": "ok", "latency_ms": elapsed_ms}


@router.post("/ai/chat", response_model=ChatResponse, summary="AI 对话（可触发需审批 Tool / 可选 SSE 流式）", description="需要认证；stream=false 返回正常回复或 need_approval=true + taskid 审批请求；stream=true 以 SSE 流式返回（text 增量 + done 终态，审批分支发 approval 事件）。")
async def ai_chat(
    request: Request,
    body: ChatRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
):
    token = _extract_bearer(request)
    if body.stream:
        return StreamingResponse(
            run_chat_stream(current_user, body, token),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return await run_chat(current_user, body, token)


@router.post("/ai/approval", response_model=ApprovalDecisionResponse, summary="审批决定：执行/拒绝需审批 Tool", description="需要认证；携带 taskid 提交批准/拒绝，服务端执行后消费 taskid。")
async def ai_approval(
    request: Request,
    body: ApprovalDecisionRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> ApprovalDecisionResponse:
    token = _extract_bearer(request)
    return await resolve_approval(current_user, body, token)


def _extract_bearer(request: Request) -> str | None:
    """从 Authorization 头提取原始 JWT（供 tool 透传调用 meta-service）。"""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None
