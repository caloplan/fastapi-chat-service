"""AI 相关 Schema（PydanticAI 演示路由用）。"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """PydanticAI Agent 演示请求。"""

    message: str = Field(..., min_length=1, max_length=4000, description="用户消息")


class ChatResponse(BaseModel):
    """PydanticAI Agent 演示响应。"""

    reply: str
    model: str
