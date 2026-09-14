"""AI 推理参数与 Token 用量。"""

from pydantic import BaseModel, Field


class ModelParams(BaseModel):
    """模型推理参数（可缺省，缺省走服务端 AI_MODEL_NAME / 默认值）。

    注：当前实现暂不逐项透传，字段为客户端预留；后续按需映射到
    pydantic-ai 的 model_settings。
    """

    model: str | None = None
    temperature: float = Field(0.7, ge=0, le=2)
    max_tokens: int | None = Field(None, ge=1)
    top_p: float | None = Field(None, gt=0, le=1)
    stream: bool = False
    thinking: bool | None = None  # deepseek-reasoner 思维链开关（预留）


class UsageInfo(BaseModel):
    """Token 用量（模型返回，仅本次请求，不落库）。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
