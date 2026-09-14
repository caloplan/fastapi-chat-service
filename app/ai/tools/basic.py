"""基础工具：时间查询。

新增 Tool 的方式：在本包新建模块，用 register_tool 注册即可，
build_tools() 会自动发现并依据 AI_ENABLED_TOOLS 配置注入 Agent。

caloplan 数据域 tools 见同包 caloplan_food / caloplan_meal /
caloplan_body / caloplan_nutrition（读写 meta-service）。
"""

from datetime import datetime, timezone

from app.ai.tools import register_tool


@register_tool(name="get_current_time", description="获取当前日期时间（ISO 8601，UTC）")
async def get_current_time() -> str:
    """获取当前日期时间字符串（ISO 8601，UTC）。"""
    return datetime.now(timezone.utc).isoformat()
