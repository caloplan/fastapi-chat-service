"""认证相关 Schema：当前用户（从 JWT payload 解析，不落库）。"""

from pydantic import BaseModel


class CurrentUser(BaseModel):
    """当前登录用户（从 user-service 签发的 JWT payload 解析）。

    字段与 mservice-fastapi-user 的 JWT 声明一一对应：
    sub（用户名）/ user_id / service_name / role / type。
    """

    sub: str
    user_id: int
    service_name: str
    role: str
    type: str


class PingResponse(BaseModel):
    """认证连通性检查响应（演示路由用）。"""

    message: str
    user: CurrentUser
