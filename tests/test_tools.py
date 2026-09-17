"""Tool 配置基础设施测试：注册发现、AI_ENABLED_TOOLS 过滤、函数可用性。

通过修改 settings.AI_ENABLED_TOOLS 验证配置开关（try/finally 恢复，避免污染其他用例）。
caloplan 数据域 tool 的行为测试见 test_caloplan_tools.py。
"""

import pytest

from app.ai.tools import build_tools, enabled_tool_names, register_tool, registered_tools
from app.core.config import settings

# 全部已注册 tool（basic + caloplan 数据域）
_ALL_TOOLS = {
    "get_current_time",
    "create_food",
    "list_my_food",
    "create_meal",
    "delete_meal",
    "list_my_meal",
    "upsert_my_body",
    "get_my_body_by_date",
    "upsert_my_nutrition",
    "get_my_nutrition_by_date",
}

# 需审批（requires_approval=True）的写 tool
_APPROVAL_TOOLS = {"create_food", "create_meal", "delete_meal", "upsert_my_body", "upsert_my_nutrition"}


def _set_enabled(value: list[str]):
    """临时修改 AI_ENABLED_TOOLS 并保证恢复。"""
    original = settings.AI_ENABLED_TOOLS
    settings.AI_ENABLED_TOOLS = value
    return original


# ── 注册与发现 ──────────────────────────────────────────────

def test_registered_tools_contains_all():
    registry = registered_tools()
    assert set(registry) == _ALL_TOOLS
    assert registry["get_current_time"].description


def test_caloplan_approval_flags():
    registry = registered_tools()
    for name in _ALL_TOOLS:
        assert registry[name].requires_approval == (name in _APPROVAL_TOOLS), name


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
    assert set(names) == _ALL_TOOLS


def test_enabled_filtered_list():
    original = _set_enabled(["create_food"])
    try:
        names = enabled_tool_names()
    finally:
        settings.AI_ENABLED_TOOLS = original
    assert names == ["create_food"]


def test_enabled_empty_disables_all():
    original = _set_enabled([])
    try:
        assert enabled_tool_names() == []
        assert build_tools() == []
    finally:
        settings.AI_ENABLED_TOOLS = original


def test_build_tools_returns_tool_objects():
    original = _set_enabled(["get_current_time", "create_food"])
    try:
        tools = build_tools()
    finally:
        settings.AI_ENABLED_TOOLS = original
    assert sorted(t.name for t in tools) == ["create_food", "get_current_time"]
    assert all(t.description for t in tools)


def test_has_approval_tools_true():
    original = _set_enabled(["*"])
    try:
        assert enabled_tool_names() and any(
            registered_tools()[n].requires_approval for n in enabled_tool_names()
        )
    finally:
        settings.AI_ENABLED_TOOLS = original


# ── 函数可用性 ──────────────────────────────────────────────

async def test_get_current_time_returns_iso_string():
    from app.ai.tools.basic import get_current_time

    value = await get_current_time()
    assert isinstance(value, str)
    assert "T" in value
