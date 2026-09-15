"""caloplan 膳食（meal）tools：查询我的膳食列表 + 添加膳食。

- list_my_meal：自由读（免审批）；
- create_meal：写操作（requires_approval=True）。模型只传 {food_id, amount}，
  服务端按 food_id 批量查 food 后构造快照（name/image/unit/nutrition×amount）并合计营养——
  营养数据全部来自真实食物库，防止模型编造；未找到的 food_id 直接报错。

data 只存业务字段：{id, tips, type, foods, nutrition}（id 为业务 meal_id）；
user_id 是 MetaSDK entry 元数据（owner_user_id），创建时由 meta 自动写入。
meta 仅按 service 隔离（同 service 多用户数据互相可见），因此查询与 batch 引用
食物均按响应 owner_user_id 过滤——他人的 food 视为未找到，防止越权引用。

落库 foods 为快照结构（对齐 caloplan-core MealFoodSnapshot）：
{foodId, name, image, unit, amount, nutrition:{carbon/protein/fat:{unit,value},...}}
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.ai.context import require_ai_context
from app.ai.tools import register_tool
from app.ai.tools._caloplan_common import (
    NUTRITION_KEYS,
    NUTRITION_UNITS,
    filter_by_owner,
    gen_entity_key,
    nutrition_from_data,
    simplify_meal_entry,
)
from app.ai.tools.meta_client import MetaApiError, get_meta_client

_MEAL_TYPE = "meal"
MEAL_TYPES = ("breakfast", "launch", "dinner", "snack")


class MealFoodItem(BaseModel):
    """膳食中的一项食物。"""

    food_id: str = Field(..., min_length=1, max_length=64, description="食物 ID（来自 list_my_food）")
    amount: float = Field(..., gt=0, description="份数（可小数）")


class CreateMealParams(BaseModel):
    """添加膳食入参。"""

    type: Literal["breakfast", "launch", "dinner", "snack"] = Field(..., description="餐次")
    tips: str = Field("", max_length=500, description="备注（可选）")
    foods: list[MealFoodItem] = Field(..., min_length=1, description="膳食包含的食物（至少 1 项）")


def _scale_nutrition(nutrition: dict, amount: float) -> dict:
    """food 单位营养 × 份数 → 快照营养（单位不变）。"""
    return {
        key: {"unit": (nutrition.get(key) or {}).get("unit", NUTRITION_UNITS[key]),
              "value": round(float((nutrition.get(key) or {}).get("value", 0)) * amount, 4)}
        for key in NUTRITION_KEYS
    }


@register_tool(
    name="create_meal",
    description="添加膳食（一餐）到我的膳食记录：指定餐次、食物（food_id + 份数）与备注。营养由服务端按食物库自动计算，执行需要用户确认。",
    requires_approval=True,
)
async def create_meal(params: CreateMealParams) -> dict:
    """创建膳食：批量查食物 → 构造快照 → 合计营养 → 落库。"""
    ctx = require_ai_context()
    client = get_meta_client()
    keys = [item.food_id for item in params.foods]

    try:
        batch = await client.batch_get_entries("food", keys)
    except MetaApiError as exc:
        return {"ok": False, "error": exc.message}

    # 按 owner_user_id 过滤：meta batch 仅按 service 隔离，他人的 food 视为未找到，
    # 防止 agent 引用同 service 其他用户的食物（用户数据隔离）。
    uid = str(ctx.user.user_id)
    batch = {
        key: entry
        for key, entry in batch.items()
        if entry is not None and str(entry.get("owner_user_id")) == uid
    }

    # 未找到的 food_id 一律报错（不静默跳过），提示先查询有效食物
    missing = [key for key in keys if batch.get(key) is None]
    if missing:
        return {
            "ok": False,
            "error": f"未找到食物: {missing}。请先用 list_my_food 查询我的食物库，使用其中有效的 id。",
        }

    snapshots: dict[str, dict] = {}
    totals: dict[str, float] = {key: 0.0 for key in NUTRITION_KEYS}
    for item in params.foods:
        data = batch[item.food_id].get("data", {})
        snapshots[item.food_id] = {
            "foodId": item.food_id,
            "name": data.get("name", ""),
            "image": data.get("image", ""),
            "unit": data.get("unit", {"unit": "", "value": 1}),
            "amount": item.amount,
            "nutrition": _scale_nutrition(data.get("nutrition", {}), item.amount),
        }
        for key in NUTRITION_KEYS:
            totals[key] += float((data.get("nutrition") or {}).get(key, {}).get("value", 0)) * item.amount

    entity_key = gen_entity_key()
    meal_data = {
        "id": entity_key,
        "tips": params.tips,
        "type": params.type,
        "foods": snapshots,
        "nutrition": {key: {"unit": NUTRITION_UNITS[key], "value": round(totals[key], 4)} for key in NUTRITION_KEYS},
    }
    try:
        await client.create_entry(_MEAL_TYPE, entity_key, meal_data)
    except MetaApiError as exc:
        return {"ok": False, "error": exc.message}

    return {
        "ok": True,
        "id": entity_key,
        "type": params.type,
        "tips": params.tips,
        "foods": {fid: snap["amount"] for fid, snap in snapshots.items()},
        "nutrition": nutrition_from_data(meal_data["nutrition"]),
    }


@register_tool(
    name="list_my_meal",
    description="查询我的膳食记录列表：返回每条膳食的 id / 餐次 / 备注 / 各食物份数 / 合计营养。",
)
async def list_my_meal() -> dict:
    """查询当前用户膳食列表（meta 按 service 隔离，返回后按 owner_user_id 过滤当前用户）。"""
    ctx = require_ai_context()
    client = get_meta_client()
    try:
        result = await client.query_entries(_MEAL_TYPE)
    except MetaApiError as exc:
        return {"ok": False, "error": exc.message}
    items = [
        simplify_meal_entry(it)
        for it in filter_by_owner(result.get("items", []), str(ctx.user.user_id))
    ]
    return {"ok": True, "total": len(items), "items": items}


__all__ = ["CreateMealParams", "MealFoodItem", "create_meal", "list_my_meal"]
