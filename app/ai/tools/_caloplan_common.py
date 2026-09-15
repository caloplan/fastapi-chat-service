"""caloplan tools 共享工具（以 _ 开头，不参与 tool 自动发现）。

- entity_key 生成：uuid4 短码（本质无业务含义，防穿梭与数据唯一，不暴露给模型）；
- 营养单位约定：food.nutrition 带单位对象 {carbon/protein/fat: kg, salt: g, energy: kcal}，
  模型侧只传数值，由服务端补齐单位（对齐 caloplan-core 的类型化单位模型）；
- 本地日期：body / nutrition 按天记录，date 使用服务器本地日期（UTC+8 部署环境）。
"""

import uuid
from datetime import datetime
from typing import Any

# food.nutrition 的固定单位（对齐 caloplan-core unit.ts 类型化单位）
NUTRITION_UNITS: dict[str, str] = {
    "carbon": "kg",
    "protein": "kg",
    "fat": "kg",
    "salt": "g",
    "energy": "kcal",
}
NUTRITION_KEYS: list[str] = list(NUTRITION_UNITS.keys())


def gen_entity_key() -> str:
    """生成 entity_key（uuid4 短码，8 位十六进制，符合 meta 的 key 正则）。"""
    return uuid.uuid4().hex[:8]


def now_iso() -> str:
    """当前时间 ISO 8601（本地时间，无时区后缀，对齐 caloplan 落库格式）。"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def today_str() -> str:
    """服务器本地日期 YYYY-MM-DD（body / nutrition 按天记录的 date 默认值）。"""
    return datetime.now().strftime("%Y-%m-%d")


def nutrition_to_data(values: dict[str, float]) -> dict[str, dict[str, Any]]:
    """模型侧营养数值 → meta 落库带单位结构 {key: {unit, value}}。"""
    return {
        key: {"unit": NUTRITION_UNITS[key], "value": float(values.get(key, 0) or 0)}
        for key in NUTRITION_KEYS
    }


def nutrition_from_data(data: dict[str, Any]) -> dict[str, float]:
    """meta 落库带单位结构 → 模型侧纯数值（用于 tool 返回展示）。"""
    return {key: float((data.get(key) or {}).get("value", 0)) for key in NUTRITION_KEYS}


def simplify_food_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """meta food entry → 精简展示（id / 名称 / 每份单位 / 营养数值）。"""
    data = entry.get("data", {})
    return {
        "id": entry.get("entity_key"),
        "name": data.get("name", ""),
        "image": data.get("image", ""),
        "unit": data.get("unit", {"unit": "", "value": 0}),
        "nutrition": nutrition_from_data(data.get("nutrition", {})),
    }


def simplify_meal_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """meta meal entry → 精简展示（id / 类型 / 提示 / 食物份数 / 合计营养）。"""
    data = entry.get("data", {})
    return {
        "id": entry.get("entity_key"),
        "type": data.get("type", ""),
        "tips": data.get("tips", ""),
        "foods": {fid: snap.get("amount", 0) for fid, snap in (data.get("foods") or {}).items()},
        "nutrition": nutrition_from_data(data.get("nutrition", {})),
    }


def filter_by_owner(items: list[dict[str, Any]], user_id: str | int) -> list[dict[str, Any]]:
    """按 entry 元数据 owner_user_id 过滤（meta 仅按 service 隔离，同 service 多用户需本地再隔离）。

    owner_user_id 是 MetaSDK entry 的元数据字段（创建时由 meta 从 JWT 自动写入），
    不在 data 业务字段里，因此查询响应后需按该顶层字段过滤当前用户的数据。
    """
    uid = str(user_id)
    return [it for it in items if str(it.get("owner_user_id")) == uid]


__all__ = [
    "NUTRITION_KEYS",
    "NUTRITION_UNITS",
    "filter_by_owner",
    "gen_entity_key",
    "now_iso",
    "nutrition_from_data",
    "nutrition_to_data",
    "simplify_food_entry",
    "simplify_meal_entry",
    "today_str",
]
