"""基础演示 Tools：时间查询与数值计算。

新增 Tool 的方式：在本包新建模块，用 register_tool 注册即可，
build_tools() 会自动发现并依据 AI_ENABLED_TOOLS 配置注入 Agent。
"""

from datetime import datetime, timezone

from app.ai.tools import register_tool


@register_tool(name="get_current_time", description="获取当前日期时间（ISO 8601，UTC）")
async def get_current_time() -> str:
    """获取当前日期时间字符串（ISO 8601，UTC）。"""
    return datetime.now(timezone.utc).isoformat()


@register_tool(name="add_numbers", description="计算两个整数的和")
async def add_numbers(a: int, b: int) -> int:
    """计算两个整数之和。"""
    return a + b


@register_tool(name="deploy_service", description="部署指定服务到生产环境（需要用户确认后执行）", requires_approval=True)
async def deploy_service(service_name: str) -> str:
    """部署指定服务到生产环境（示例：需审批 Tool，模型调用后由用户确认才执行）。"""
    return f"deployed:{service_name}"
