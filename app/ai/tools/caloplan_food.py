"""caloplan 食物（food）tools：查询我的食物库 + 添加食物。

- list_my_food：自由读（免审批），返回 food_id / 名称 / 每份单位与营养，
  供 create_meal 引用 food_id；
- create_food：写操作（requires_approval=True），entity_key 由服务端注入。

data 只存业务字段：{id, name, image, unit, nutrition}（id 为业务 food_id）；
user_id 是 MetaSDK entry 元数据（owner_user_id），创建时由 meta 自动写入，
查询后按响应 owner_user_id 过滤当前用户（meta 仅按 service 隔离）。
"""

from pydantic import BaseModel, Field

from app.ai.context import require_ai_context
from app.ai.tools import register_tool
from app.ai.tools._caloplan_common import (
    filter_by_owner,
    gen_entity_key,
    nutrition_from_data,
    nutrition_to_data,
    simplify_food_entry,
)
from app.ai.tools.meta_client import MetaApiError, get_meta_client

_FOOD_TYPE = "food"


class NutritionValues(BaseModel):
    """食物每份营养（模型只传数值，单位由服务端补齐：碳/蛋白/脂肪 kg，盐 g，能量 kcal）。"""

    carbon: float = Field(0, ge=0, description="碳水化合物（每份，kg）")
    protein: float = Field(0, ge=0, description="蛋白质（每份，kg）")
    fat: float = Field(0, ge=0, description="脂肪（每份，kg）")
    salt: float = Field(0, ge=0, description="盐（每份，g）")
    energy: float = Field(0, ge=0, description="能量（每份，kcal）")


class CreateFoodParams(BaseModel):
    """添加食物入参。"""

    name: str = Field(..., min_length=1, max_length=100, description="食物名称")
    unit: str = Field(..., min_length=1, max_length=20, description="每份单位名，如 g / 个 / 杯 / 碗")
    unit_value: float = Field(..., gt=0, description="每份数量（对应单位的数值），如 100（g）")
    nutrition: NutritionValues = Field(..., description="每份营养（数值）")


@register_tool(
    name="create_food",
    description="添加食物到我的食物库：提供名称、每份单位与每份营养（碳/蛋白/脂肪/盐/能量）。执行需要用户确认。",
    requires_approval=True,
)
async def create_food(params: CreateFoodParams) -> dict:
    """创建食物条目（entity_key=uuid4 短码；owner_user_id 由 meta 从 JWT 自动注入）。"""
    ctx = require_ai_context()
    client = get_meta_client()
    entity_key = gen_entity_key()
    data = {
        "id": entity_key,
        "name": params.name,
        "image": "",
        "unit": {"unit": params.unit, "value": params.unit_value},
        "nutrition": nutrition_to_data(params.nutrition.model_dump()),
    }
    try:
        await client.create_entry(_FOOD_TYPE, entity_key, data)
    except MetaApiError as exc:
        return {"ok": False, "error": exc.message}
    return {
        "ok": True,
        "id": entity_key,
        "name": params.name,
        "unit": {"unit": params.unit, "value": params.unit_value},
        "nutrition": nutrition_from_data(data["nutrition"]),
    }


@register_tool(
    name="list_my_food",
    description="查询我的食物库列表：返回每条食物的 id / 名称 / 每份单位 / 每份营养。添加膳食（create_meal）时用其中的 id 作为 food_id。",
)
async def list_my_food() -> dict:
    """查询当前用户食物列表（meta 按 service 隔离，返回后按 owner_user_id 过滤当前用户）。"""
    ctx = require_ai_context()
    client = get_meta_client()
    try:
        result = await client.query_entries(_FOOD_TYPE)
    except MetaApiError as exc:
        return {"ok": False, "error": exc.message}
    items = [
        simplify_food_entry(it)
        for it in filter_by_owner(result.get("items", []), str(ctx.user.user_id))
    ]
    return {"ok": True, "total": len(items), "items": items}


__all__ = ["CreateFoodParams", "NutritionValues", "create_food", "list_my_food"]
