"""AI Schema 包：统一导出对外模型。"""

from app.schemas.ai.approval import ApprovalDecisionRequest, ApprovalDecisionResponse
from app.schemas.ai.chat import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    MessageRole,
    PendingToolCall,
    ToolCall,
    ToolCallResult,
)
from app.schemas.ai.error import AIErrorDetail
from app.schemas.ai.model import ModelParams, UsageInfo

__all__ = [
    "AIErrorDetail",
    "ApprovalDecisionRequest",
    "ApprovalDecisionResponse",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "MessageRole",
    "ModelParams",
    "PendingToolCall",
    "ToolCall",
    "ToolCallResult",
    "UsageInfo",
]
