"""caloplan 身体指标（body）tools：按天记录，每日一条、可无限更新（upsert）。

- get_my_body_by_date：自由读（免审批）；
- upsert_my_body：写操作（requires_approval=True）。查当日记录：
  无 → 创建；有 → 按 id 更新（仅变更字段，deep merge + 版本自增由 meta 保证）。

落库字段 snake_case：{id, user_id, date, age, height, weight, created_time}。
"""

from pydantic import BaseModel, Field

from app.ai.context import require_ai_context
from app.ai.tools import register_tool
from app.ai.tools._caloplan_common import gen_entity_key, now_iso, today_str
from app.ai.tools.meta_client import MetaApiError, get_meta_client

_BODY_TYPE = "body"

_BODY_FIELDS = ("age", "height", "weight")


class UpsertBodyParams(BaseModel):
    """当日身体指标入参（date 不传默认当天）。"""

    date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="日期 YYYY-MM-DD（不传默认今天）")
    age: float = Field(..., ge=0, le=150, description="年龄")
    height: float = Field(..., ge=0, le=300, description="身高（cm）")
    weight: float = Field(..., ge=0, le=500, description="体重（kg）")


async def _query_today(client, user_id: str, date: str) -> dict | None:
    """查询当日记录（内部工具函数，抛 MetaApiError）。"""
    result = await client.query_entries(_BODY_TYPE, filters={"user_id": user_id, "date": date})
    items = result.get("items", [])
    return items[0] if items else None


@register_tool(
    name="upsert_my_body",
    description="记录当天身体指标（年龄/身高/体重）：当天已有记录则更新，没有则创建。执行需要用户确认。",
    requires_approval=True,
)
async def upsert_my_body(params: UpsertBodyParams) -> dict:
    """按天 upsert 身体指标。"""
    ctx = require_ai_context()
    client = get_meta_client()
    date = params.date or today_str()
    values = {field: getattr(params, field) for field in _BODY_FIELDS}

    try:
        existing = await _query_today(client, str(ctx.user.user_id), date)
        if existing is not None:
            entry = await client.update_entry(_BODY_TYPE, existing["entity_key"], values)
            return {"ok": True, "action": "updated", "id": entry.get("entity_key"), "date": date, **values}
        entity_key = gen_entity_key()
        data = {
            "id": entity_key,
            "user_id": str(ctx.user.user_id),
            "date": date,
            **values,
            "created_time": now_iso(),
        }
        await client.create_entry(_BODY_TYPE, entity_key, data)
        return {"ok": True, "action": "created", "id": entity_key, "date": date, **values}
    except MetaApiError as exc:
        return {"ok": False, "error": exc.message}


@register_tool(
    name="get_my_body_by_date",
    description="查询指定日期的身体指标记录（年龄/身高/体重）；当天没有记录时返回 found=false。",
)
async def get_my_body_by_date(date: str) -> dict:
    """按日期查询当前用户身体指标。"""
    ctx = require_ai_context()
    client = get_meta_client()
    try:
        result = await client.query_entries(_BODY_TYPE, filters={"user_id": str(ctx.user.user_id), "date": date})
    except MetaApiError as exc:
        return {"ok": False, "error": exc.message}
    items = result.get("items", [])
    if not items:
        return {"ok": True, "found": False, "date": date}
    data = items[0].get("data", {})
    return {
        "ok": True,
        "found": True,
        "id": items[0].get("entity_key"),
        "date": data.get("date"),
        **{field: data.get(field) for field in _BODY_FIELDS},
    }


__all__ = ["UpsertBodyParams", "get_my_body_by_date", "upsert_my_body"]
