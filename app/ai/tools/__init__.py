"""Tool 配置基础设施：注册 → 发现 → 按配置过滤 → 注入 Agent。

注册机制::

    from app.ai.tools import register_tool

    @register_tool(name="get_current_time", description="获取当前日期时间")
    async def get_current_time() -> str:
        ...

配置开关（AI_ENABLED_TOOLS，见 app/core/config.py）:
    - ["*"]             启用全部已注册 tool（默认）；
    - ["name_a", ...]   仅启用名单内的 tool；
    - []                全部禁用（Agent 不带任何 tool）。

使用::

    from app.ai.tools import build_tools
    agent = Agent(model=..., tools=build_tools())
"""

import importlib
import pkgutil
from dataclasses import dataclass
from typing import Callable

from pydantic_ai import Tool

from app.core.config import settings

# name -> ToolDef
_TOOL_REGISTRY: dict[str, "ToolDef"] = {}
_discovered = False


@dataclass(frozen=True)
class ToolDef:
    """Tool 注册元信息。"""

    name: str
    description: str
    func: Callable


def register_tool(name: str, description: str) -> Callable[[Callable], Callable]:
    """注册装饰器：把（async）函数注册为可用 Tool，返回原函数。"""

    def decorator(func: Callable) -> Callable:
        if name in _TOOL_REGISTRY:
            raise ValueError(f"Tool 重复注册: {name}")
        _TOOL_REGISTRY[name] = ToolDef(name=name, description=description, func=func)
        return func

    return decorator


def _discover_tools() -> None:
    """自动发现并导入 tools 子模块，触发其中的 register_tool。"""
    global _discovered
    if _discovered:
        return
    pkg = importlib.import_module(__name__)
    for mod in pkgutil.iter_modules(pkg.__path__):
        if mod.name.startswith("_"):
            continue
        importlib.import_module(f"{pkg.__name__}.{mod.name}")
    _discovered = True


def registered_tools() -> dict[str, ToolDef]:
    """返回全部已注册 tool（触发一次模块发现）。"""
    _discover_tools()
    return dict(_TOOL_REGISTRY)


def enabled_tool_names() -> list[str]:
    """按 AI_ENABLED_TOOLS 配置解析启用名单（默认 "*" 全部启用）。"""
    conf = settings.AI_ENABLED_TOOLS or []
    if "*" in conf:
        return list(registered_tools().keys())
    return [name for name in conf if name in registered_tools()]


def build_tools() -> list[Tool]:
    """根据配置构建 PydanticAI Tool 列表（供 Agent 构造注入）。"""
    registry = registered_tools()
    return [
        Tool(registry[name].func, name=name, description=registry[name].description)
        for name in enabled_tool_names()
    ]


__all__ = ["ToolDef", "build_tools", "enabled_tool_names", "register_tool", "registered_tools"]
