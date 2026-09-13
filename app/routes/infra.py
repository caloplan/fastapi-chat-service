"""基础设施演示路由：Redis 连通性、PydanticAI Agent 演示。

这些端点用于验证基础框架接入，均要求认证（get_current_user 会同步执行
service_name 白名单校验，superuser 除外）。
"""

import time
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic_ai import Agent

from app.ai.agent import get_ai_agent
from app.core.config import settings
from app.core.dependencies import get_current_user
from app.core.redis import get_redis
from app.schemas.ai import ChatRequest, ChatResponse
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


@router.post("/ai/chat", response_model=ChatResponse, summary="PydanticAI Agent 演示", description="需要认证；验证 PydanticAI 基础框架接入。")
async def ai_chat(
    body: ChatRequest,
    _: Annotated[CurrentUser, Depends(get_current_user)],
    agent: Annotated[Agent, Depends(get_ai_agent)],
) -> ChatResponse:
    try:
        result = await agent.run(body.message)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI 调用失败: {exc}",
        )
    return ChatResponse(reply=str(result.output or ""), model=settings.AI_MODEL_NAME)
