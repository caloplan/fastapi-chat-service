"""Tool 配置基础设施测试：注册发现、AI_ENABLED_TOOLS 过滤、函数可用性。

通过修改 settings.AI_ENABLED_TOOLS 验证配置开关（try/finally 恢复，避免污染其他用例）。
"""

import pytest

from app.ai.tools import build_tools, enabled_tool_names, register_tool, registered_tools
from app.core.config import settings


def _set_enabled(value: list[str]):
    """临时修改 AI_ENABLED_TOOLS 并保证恢复。"""
    original = settings.AI_ENABLED_TOOLS
    settings.AI_ENABLED_TOOLS = value
    return original


# ── 注册与发现 ──────────────────────────────────────────────

def test_registered_tools_contains_basic():
    registry = registered_tools()
    assert "get_current_time" in registry
    assert "add_numbers" in registry
    assert registry["get_current_time"].description


def test_duplicate_registration_raises():
    async def _dummy() -> str:
        return "x"

    with pytest.raises(ValueError, match="重复注册"):
        register_tool(name="get_current_time", description="重复")(
            _dummy
        )
    # 注册失败不污染注册表
    assert "get_current_time" in registered_tools()


# ── 配置过滤 ────────────────────────────────────────────────

def test_enabled_all_by_default():
    original = _set_enabled(["*"])
    try:
        names = enabled_tool_names()
    finally:
        settings.AI_ENABLED_TOOLS = original
    assert set(names) == {"get_current_time", "add_numbers", "deploy_service"}


def test_enabled_filtered_list():
    original = _set_enabled(["add_numbers"])
    try:
        names = enabled_tool_names()
    finally:
        settings.AI_ENABLED_TOOLS = original
    assert names == ["add_numbers"]


def test_enabled_empty_disables_all():
    original = _set_enabled([])
    try:
        assert enabled_tool_names() == []
        assert build_tools() == []
    finally:
        settings.AI_ENABLED_TOOLS = original


def test_build_tools_returns_tool_objects():
    original = _set_enabled(["get_current_time", "add_numbers"])
    try:
        tools = build_tools()
    finally:
        settings.AI_ENABLED_TOOLS = original
    assert sorted(t.name for t in tools) == ["add_numbers", "get_current_time"]
    assert all(t.description for t in tools)


# ── 函数可用性 ──────────────────────────────────────────────

async def test_get_current_time_returns_iso_string():
    from app.ai.tools.basic import get_current_time

    value = await get_current_time()
    assert isinstance(value, str)
    assert "T" in value


async def test_add_numbers_works():
    from app.ai.tools.basic import add_numbers

    assert await add_numbers(1, 2) == 3
    assert await add_numbers(-5, 5) == 0
