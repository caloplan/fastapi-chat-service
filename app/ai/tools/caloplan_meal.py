"""caloplan 膳食（meal）tools：查询我的膳食列表 + 添加膳食 + 删除膳食。

- list_my_meal：自由读（免审批）；
- create_meal：写操作（requires_approval=True）。模型只传 {food_id, amount}，
  服务端按 food_id 批量查 food 后构造快照（name/image/unit/nutrition×amount）并合计营养——
  营养数据全部来自真实食物库，防止模型编造；未找到的 food_id 直接报错。
- delete_meal：写操作（requires_approval=True）。按 meal_id 软删除，先查确认存在且属主再删。

data 只存业务字段：{id, tips, type, foods, nutrition}（id 为业务 meal_id）；
user_id 是 MetaSDK entry 元数据（owner_user_id），创建时由 meta 自动写入。
meta 仅按 service 隔离（同 service 多用户数据互相可见），因此查询与 batch 引用
食物均按响应 owner_user_id 过滤——他人的 food 视为未找到，防止越权引用。

落库 foods 为快照结构（对齐 caloplan-core MealFoodSnapshot，字段用 snake_case：
food_id/name/image/unit/amount/nutrition:{carbon/protein/fat:{unit,value},...}）
"""

import re
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
    today_str,
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
    # P2：nutrition 显式 null 时兜底为空 dict，保证后续 .get 不抛 AttributeError
    nutrition = nutrition or {}
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

    # P2：keys 先去重（保序），避免把重复 key 传给 batch 查询；查询与快照均基于去重后的 keys
    keys = list(dict.fromkeys(item.food_id for item in params.foods))

    try:
        batch = await client.batch_get_entries("food", keys)
    except MetaApiError as exc:
        return {"ok": False, "error": exc.message}

    # 按 owner_user_id 过滤：meta batch 仅按 service 隔离，他人的 food 视为未找到，
    # 防止 agent 引用同 service 其他用户的食物（用户数据隔离）。
    # P1：owner_user_id 字段缺失/为 None 时用 "" 兜底，避免 str(None)=="None" 误判
    uid = str(ctx.user.user_id)
    batch = {
        key: entry
        for key, entry in batch.items()
        if entry is not None and str(entry.get("owner_user_id") or "") == uid
    }

    # 未找到的 food_id 一律报错（不静默跳过），提示先查询有效食物。
    # P1：entry 为 {} / {"data": None} 时视为未找到，不再构造 name/image 全空、营养全 0 的空食物
    missing = [key for key in keys if not batch.get(key) or not (batch[key].get("data") or {})]
    if missing:
        return {
            "ok": False,
            "error": f"未找到食物: {missing}。请先用 list_my_food 查询我的食物库，使用其中有效的 id。",
        }

    # P0：先按 food_id 合并 amount（累加），顺序按 food_id 首次出现位置保持稳定；
    # 后续快照与合计营养统一基于合并结果，避免重复 food_id 导致快照 amount 与 totals 不一致
    merged: dict[str, float] = {}
    for item in params.foods:
        merged[item.food_id] = merged.get(item.food_id, 0.0) + item.amount

    snapshots: dict[str, dict] = {}
    totals: dict[str, float] = {key: 0.0 for key in NUTRITION_KEYS}
    for food_id, amount in merged.items():
        # P2：data 显式 null 时兜底为空 dict，保证后续 .get 不抛 AttributeError
        data = batch[food_id].get("data") or {}
        snapshots[food_id] = {
            "food_id": food_id,  # meta schema snake_case（对齐前端 SDK camelToSnake 落库）
            "name": data.get("name", ""),
            "image": data.get("image", ""),
            "unit": data.get("unit", {"unit": "", "value": 1}),
            "amount": amount,
            "nutrition": _scale_nutrition(data.get("nutrition"), amount),
        }
        for key in NUTRITION_KEYS:
            totals[key] += float((data.get("nutrition") or {}).get(key, {}).get("value", 0)) * amount

    entity_key = gen_entity_key()
    meal_data = {
        "id": entity_key,
        "user_id": str(ctx.user.user_id),
        "created_time": today_str(),
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


class DeleteMealParams(BaseModel):
    """删除膳食入参。"""

    meal_id: str = Field(..., min_length=1, max_length=64, description="要删除的膳食 ID（来自 list_my_meal）")


@register_tool(
    name="delete_meal",
    description="删除我的一条膳食记录（一餐）：按 meal_id 软删除，删除后不再出现在膳食列表中。执行需要用户确认。",
    requires_approval=True,
)
async def delete_meal(params: DeleteMealParams) -> dict:
    """删除膳食：先查确认存在且属主，再软删除。"""
    ctx = require_ai_context()
    client = get_meta_client()

    try:
        entry = await client.get_entry(_MEAL_TYPE, params.meal_id)
    except MetaApiError as exc:
        return {"ok": False, "error": exc.message}

    if entry is None:
        return {"ok": False, "error": f"未找到膳食: {params.meal_id}"}

    # owner 校验：meta 按 service 隔离，同 service 多用户数据互相可见，显式校验防止越权删除
    if str(entry.get("owner_user_id") or "") != str(ctx.user.user_id):
        return {"ok": False, "error": "无权删除他人的膳食记录"}

    try:
        await client.delete_entry(_MEAL_TYPE, params.meal_id)
    except MetaApiError as exc:
        return {"ok": False, "error": exc.message}

    return {"ok": True, "id": params.meal_id, "message": "膳食已删除"}


@register_tool(
    name="list_my_meal",
    description="查询我的膳食记录列表：支持按创建日期过滤（created_time，YYYY-MM-DD，不传则返回全部）；返回每条膳食的 id / 创建日期 / 餐次 / 备注 / 各食物份数 / 合计营养。",
)
async def list_my_meal(created_time: str = "") -> dict:
    """查询当前用户膳食列表（meta 按 service 隔离，返回后按 owner_user_id 过滤当前用户）。

    created_time 为空返回全部；非空时仅返回该日创建的膳食（data.created_time，YYYY-MM-DD）。
    对齐 caloplan-core MealRespository.listMine：按日期用 meta 服务端 filters 精确过滤（json_extract 等值匹配）。
    """
    ctx = require_ai_context()
    client = get_meta_client()

    # 入参校验：created_time 可选，非空时必须是 YYYY-MM-DD，格式错误直接报错（不发起查询）
    if created_time and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", created_time):
        return {"ok": False, "error": f"created_time 格式应为 YYYY-MM-DD，收到: {created_time}"}

    # 服务端过滤（对齐 caloplan-core listMine 的 filters 写法）：
    # user_id / created_time 是 data 业务字段（落库 snake_case），走 json_extract 精确匹配；
    # owner 隔离本身由 meta Scope 强制保证，user_id 过滤是显式冗余（与业务层口径一致）
    filters: dict[str, str] = {"user_id": str(ctx.user.user_id)}
    if created_time:
        filters["created_time"] = created_time

    try:
        result = await client.query_entries(_MEAL_TYPE, filters=filters)
    except MetaApiError as exc:
        return {"ok": False, "error": exc.message}

    owned = filter_by_owner(result.get("items", []), str(ctx.user.user_id))
    items = []
    for it in owned:
        item = simplify_meal_entry(it)
        # 附带创建日期，便于模型核对按日过滤结果
        item["created_time"] = (it.get("data") or {}).get("created_time", "")
        items.append(item)
    return {"ok": True, "total": len(items), "items": items}


__all__ = ["CreateMealParams", "DeleteMealParams", "MealFoodItem", "create_meal", "delete_meal", "list_my_meal"]
