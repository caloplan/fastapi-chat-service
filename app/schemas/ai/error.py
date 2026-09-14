"""AI 统一错误结构。"""

from pydantic import BaseModel


class AIErrorDetail(BaseModel):
    """AI 错误体（随 HTTP 状态码返回）。"""

    code: str  # ai_not_configured / model_error / approval_expired / forbidden ...
    message: str
    retryable: bool = False
