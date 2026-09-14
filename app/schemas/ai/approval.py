"""需审批 Tool 的执行决定（taskid 审批流）。"""

from pydantic import BaseModel, Field

from app.schemas.ai.chat import ToolCallResult
from app.schemas.ai.model import UsageInfo


class ApprovalDecisionRequest(BaseModel):
    """用户对 taskid 的执行决定（批准/拒绝）。"""

    taskid: str = Field(..., min_length=1, max_length=64)
    approved: bool = True  # 拒绝传 false，tool 不执行


class ApprovalDecisionResponse(BaseModel):
    """执行/拒绝后的最终结果。"""

    conversation_id: str
    reply: str
    model: str
    usage: UsageInfo
    latency_ms: float
    approved: bool
    tool_results: list[ToolCallResult] = Field(default_factory=list)
