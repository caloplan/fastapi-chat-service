"""PydanticAI 基础框架集成。

提供：
- build_agent(): 依据全局配置构建 PydanticAI Agent；
- get_ai_agent(): FastAPI 依赖，返回进程级缓存的 Agent 实例。

模型接入（AI_PROVIDER）：
- deepseek：DeepSeek 官方端点（api.deepseek.com），无需 AI_BASE_URL；
- openai：任意 OpenAI 兼容接口，AI_BASE_URL 指向自建网关/代理时使用。

Tool 配置：通过 app/ai/tools 注册，AI_ENABLED_TOOLS 控制启用名单，
build_agent() 构造时自动注入。
"""

import os
from functools import lru_cache
from typing import Any

from fastapi import HTTPException, status
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.openai import OpenAIProvider

from app.ai.tools import build_tools, has_approval_tools
from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger("ai")


def _resolve_api_key() -> str:
    """解析 API Key：AI_API_KEY 优先，其次按 provider 回退环境变量，均缺失抛 503。"""
    if settings.AI_API_KEY:
        return settings.AI_API_KEY
    env_names = ["DEEPSEEK_API_KEY"] if settings.AI_PROVIDER == "deepseek" else ["OPENAI_API_KEY"]
    for name in env_names:
        value = os.environ.get(name)
        if value:
            return value
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"AI 未配置：请设置 AI_API_KEY（{settings.AI_PROVIDER} 模式也可用 {env_names[0]}）",
    )


def _build_provider():
    """按 AI_PROVIDER 构建模型 Provider。"""
    api_key = _resolve_api_key()
    if settings.AI_PROVIDER == "deepseek":
        return DeepSeekProvider(api_key=api_key)
    if settings.AI_PROVIDER == "openai":
        return OpenAIProvider(
            base_url=settings.AI_BASE_URL or None,
            api_key=api_key,
        )
    raise ValueError(f"不支持的 AI_PROVIDER: {settings.AI_PROVIDER}（可选 deepseek / openai）")


def build_agent() -> Agent:
    """根据 settings 构建 PydanticAI Agent（含按配置启用的 tools）。

    启用工具中存在 requires_approval=True 时，输出类型切换为
    str | DeferredToolRequests，使需审批的工具调用以审批请求返回而非直接执行。
    """
    provider = _build_provider()
    model = OpenAIChatModel(model_name=settings.AI_MODEL_NAME, provider=provider)
    tools = build_tools()
    output_type: Any = str
    if has_approval_tools():
        output_type = str | DeferredToolRequests
    agent = Agent(
        model=model,
        system_prompt=settings.AI_SYSTEM_PROMPT,
        tools=tools,
        output_type=output_type,
    )
    logger.info(
        "PydanticAI Agent 已构建: provider=%s model=%s tools=%s approval=%s",
        settings.AI_PROVIDER,
        settings.AI_MODEL_NAME,
        [t.name for t in tools],
        has_approval_tools(),
    )
    return agent


@lru_cache(maxsize=1)
def _cached_agent() -> Agent:
    return build_agent()


def get_ai_agent() -> Agent:
    """FastAPI 依赖：返回进程级缓存的 Agent 实例（未配置 API Key 时抛 503）。"""
    return _cached_agent()


__all__ = ["build_agent", "get_ai_agent"]
