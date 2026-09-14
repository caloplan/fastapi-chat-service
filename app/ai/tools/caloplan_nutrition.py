"""caloplan 营养目标（nutrition）tools：按天记录，每日一条、可无限更新（upsert）。

- get_my_nutrition_by_date：自由读（免审批）；
- upsert_my_nutrition：写操作（requires_approval=True）。查当日记录：
  无 → 创建；有 → 按 id 更新（仅变更字段）。

落库字段 snake_case：{id, user_id, date, carbon, protein, fat, salt, calorie, created_time}。
注意：营养目标为纯数值（碳/蛋白/脂肪 kg、盐 g、热量 kcal），与 food.nutrition 的带单位对象不同。
"""

from pydantic import BaseModel, Field

from app.ai.context import require_ai_context
from app.ai.tools import register_tool
from app.ai.tools._caloplan_common import gen_entity_key, now_iso, today_str
from app.ai.tools.meta_client import MetaApiError, get_meta_client

_NUTRITION_TYPE = "nutrition"

_NUTRITION_FIELDS = ("carbon", "protein", "fat", "salt", "calorie")


class UpsertNutritionParams(BaseModel):
    """当日营养目标入参（date 不传默认当天）。"""

    date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="日期 YYYY-MM-DD（不传默认今天）")
    carbon: float = Field(..., ge=0, description="碳水化合物目标（kg）")
    protein: float = Field(..., ge=0, description="蛋白质目标（kg）")
    fat: float = Field(..., ge=0, description="脂肪目标（kg）")
    salt: float = Field(..., ge=0, description="盐目标（g）")
    calorie: float = Field(..., ge=0, description="热量目标（kcal）")


@register_tool(
    name="upsert_my_nutrition",
    description="设置当天营养目标（碳水/蛋白/脂肪/盐/热量）：当天已有目标则更新，没有则创建。执行需要用户确认。",
    requires_approval=True,
)
async def upsert_my_nutrition(params: UpsertNutritionParams) -> dict:
    """按天 upsert 营养目标。"""
    ctx = require_ai_context()
    client = get_meta_client()
    date = params.date or today_str()
    values = {field: getattr(params, field) for field in _NUTRITION_FIELDS}

    try:
        result = await client.query_entries(_NUTRITION_TYPE, filters={"user_id": str(ctx.user.user_id), "date": date})
        existing = (result.get("items") or [None])[0]
        if existing is not None:
            entry = await client.update_entry(_NUTRITION_TYPE, existing["entity_key"], values)
            return {"ok": True, "action": "updated", "id": entry.get("entity_key"), "date": date, **values}
        entity_key = gen_entity_key()
        data = {
            "id": entity_key,
            "user_id": str(ctx.user.user_id),
            "date": date,
            **values,
            "created_time": now_iso(),
        }
        await client.create_entry(_NUTRITION_TYPE, entity_key, data)
        return {"ok": True, "action": "created", "id": entity_key, "date": date, **values}
    except MetaApiError as exc:
        return {"ok": False, "error": exc.message}


@register_tool(
    name="get_my_nutrition_by_date",
    description="查询指定日期的营养目标（碳水/蛋白/脂肪/盐/热量）；当天没有记录时返回 found=false。",
)
async def get_my_nutrition_by_date(date: str) -> dict:
    """按日期查询当前用户营养目标。"""
    ctx = require_ai_context()
    client = get_meta_client()
    try:
        result = await client.query_entries(_NUTRITION_TYPE, filters={"user_id": str(ctx.user.user_id), "date": date})
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
        **{field: data.get(field) for field in _NUTRITION_FIELDS},
    }


__all__ = ["UpsertNutritionParams", "get_my_nutrition_by_date", "upsert_my_nutrition"]
