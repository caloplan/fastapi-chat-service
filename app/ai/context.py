"""AI Tool 请求上下文：在 agent.run 期间把当前请求的 JWT 与用户身份绑定到 contextvar。

caloplan tools 需要以当前请求身份调用 meta-service（转发 Authorization 头 +
注入 user_id / service_name）。tool 函数本身不接收请求对象，因此在
run_chat / resolve_approval 入口用 bind_ai_context() 绑定，tool 内用
get_ai_context() 读取。审批续跑（resolve_approval）同样需要绑定，
否则批准后执行的 tool 拿不到令牌。
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from app.schemas.auth import CurrentUser


@dataclass(frozen=True)
class AIContext:
    """一次对话/审批执行期间的 AI 请求上下文。"""

    token: str | None  # 原始 JWT（Authorization: Bearer <token>），透传给 meta-service
    user: CurrentUser  # 当前请求用户（user_id / service_name 用于数据归属与隔离）


_ctx: ContextVar[AIContext | None] = ContextVar("ai_request_context", default=None)


@contextmanager
def bind_ai_context(token: str | None, user: CurrentUser | None):
    """绑定当前请求上下文；退出时自动还原，避免跨请求污染。"""
    if user is None:
        yield
        return
    token_handle = _ctx.set(AIContext(token=token, user=user))
    try:
        yield
    finally:
        _ctx.reset(token_handle)


def get_ai_context() -> AIContext | None:
    """读取当前请求上下文（tool 内使用）。"""
    return _ctx.get()


def require_ai_context() -> AIContext:
    """读取当前请求上下文；缺失时抛错（tool 仅在对话/审批执行期间被调用）。"""
    ctx = _ctx.get()
    if ctx is None:
        raise RuntimeError("AI 请求上下文未绑定：tool 只能在 run_chat / resolve_approval 执行期间调用")
    return ctx


__all__ = ["AIContext", "bind_ai_context", "get_ai_context", "require_ai_context"]
