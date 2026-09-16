"""对话消息 / 请求 / 响应（方案 A：纯无状态，历史由客户端携带）。"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.ai.model import ModelParams, UsageInfo


class MessageRole(str, Enum):
    """对话消息角色（对齐 OpenAI/DeepSeek 会话格式）。"""

    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"


class ContentBlock(BaseModel):
    """OpenAI/DeepSeek 兼容的内容块（content 为数组时使用）。

    仅支持文本与图片：{type:"text", text:"..."} 或
    {type:"image_url", image_url:{"url":"data:image/jpeg;base64,..."}}。
    图片仅允许出现在 user 消息（DeepSeek vision：system/assistant 带图返回 400）。
    """

    type: Literal["text", "image_url"]
    text: str | None = None  # type=text 时
    image_url: str | dict[str, str] | None = None  # type=image_url：URL 字符串或 {"url": ...}


class ToolCall(BaseModel):
    """模型发起的一次工具调用。"""

    id: str
    name: str
    arguments: dict[str, object] | str  # 兼容 dict 或原始 JSON 字符串


class ChatMessage(BaseModel):
    """对话消息（客户端回传历史时使用）。

    content 兼容纯文本字符串，或 OpenAI/DeepSeek 风格的内容块数组
    （可含 image_url 图片块，用于多模态历史回传）。
    """

    role: MessageRole
    content: str | list[ContentBlock] = ""
    tool_calls: list[ToolCall] | None = None  # role=assistant 且发起工具调用时
    tool_call_id: str | None = None  # role=tool 时回填对应调用 id


class ChatRequest(BaseModel):
    """对话请求（无状态：历史由客户端携带，服务端零留存）。"""

    message: str = Field(..., min_length=1, max_length=8000)
    conversation_id: str | None = Field(None, max_length=64)  # 客户端生成，仅日志关联/幂等用
    history: list[ChatMessage] = Field(default_factory=list, max_length=40)
    params: ModelParams = Field(default_factory=ModelParams)
    stream: bool = False  # true 时以 SSE 流式返回（text 增量 + done 终态；审批分支同 JSON 语义）


class ToolCallResult(BaseModel):
    """本次请求内实际触发的工具调用记录（响应中回显）。"""

    id: str
    name: str
    arguments: dict[str, object] | None = None
    result: object | None = None


class PendingToolCall(BaseModel):
    """待用户审批的工具调用（随 need_approval 响应返回）。"""

    tool_call_id: str
    name: str
    arguments: dict[str, object]
    description: str | None = None  # 执行方式说明（给用户看）


class ChatResponse(BaseModel):
    """对话响应（正常回复 或 审批请求分支，客户端按 need_approval 区分）。"""

    conversation_id: str
    reply: str = ""  # 审批分支时为空
    model: str
    usage: UsageInfo
    latency_ms: float
    tool_calls: list[ToolCallResult] = Field(default_factory=list)

    # ── 审批分支（need_approval=true 时有效）──
    need_approval: bool = False
    taskid: str | None = None
    pending_tools: list[PendingToolCall] = Field(default_factory=list)
