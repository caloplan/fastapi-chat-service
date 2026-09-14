"""caloplan 食物（food）tools：查询我的食物库 + 添加食物。

- list_my_food：自由读（免审批），返回 food_id / 名称 / 每份单位与营养，
  供 create_meal 引用 food_id；
- create_food：写操作（requires_approval=True），entity_key 与 user_id 由服务端注入。

落库字段 snake_case，data.user_id = 当前 JWT user_id（对齐 caloplan-core FoodData 约定）。
"""

from pydantic import BaseModel, Field

from app.ai.context import require_ai_context
from app.ai.tools import register_tool
from app.ai.tools._caloplan_common import (
    gen_entity_key,
    now_iso,
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
    """创建食物条目（entity_key=uuid4 短码；user_id 从当前请求注入）。"""
    ctx = require_ai_context()
    client = get_meta_client()
    entity_key = gen_entity_key()
    data = {
        "id": entity_key,
        "user_id": str(ctx.user.user_id),
        "name": params.name,
        "image": "",
        "unit": {"unit": params.unit, "value": params.unit_value},
        "nutrition": nutrition_to_data(params.nutrition.model_dump()),
        "created_time": now_iso(),
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
    """查询当前用户食物列表（filters user_id，数据隔离由 token 身份保证）。"""
    ctx = require_ai_context()
    client = get_meta_client()
    try:
        result = await client.query_entries(_FOOD_TYPE, filters={"user_id": str(ctx.user.user_id)})
    except MetaApiError as exc:
        return {"ok": False, "error": exc.message}
    items = [simplify_food_entry(it) for it in result.get("items", [])]
    return {"ok": True, "total": result.get("total", 0), "items": items}


__all__ = ["CreateFoodParams", "NutritionValues", "create_food", "list_my_food"]
