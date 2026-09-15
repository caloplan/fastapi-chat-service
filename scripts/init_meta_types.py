# -*- coding: utf-8 -*-
"""初始化 meta-service 的 caloplan 类型 schema（food / meal / body / nutrition）。

为什么需要：meta-service 创建/更新 entry 时，会按 type schema 动态建模校验，并将
**未在 schema 中定义的字段丢弃**（Pydantic 默认忽略额外字段）。因此 type schema 必须
完整包含 chat-service 落库 data 的**业务字段**——尤其是 date 等查询过滤字段，
否则 upsert（先查当日 → 有则更新）永远查不到已有记录，只会走 create 分支。
注意：user_id / created_time 属于 entry 元数据（owner_user_id / created_at），
由 meta 自动管理，不在本脚本补齐范围内。

用法：
    python scripts/init_meta_types.py --token <superuser-jwt> [--base-url ...] [--service default]

- --token：meta-service 的 superuser 访问令牌（类型创建/更新仅 superuser）。
- --base-url：meta-service 地址，默认取环境变量 META_SERVICE_URL，缺省 http://localhost:9093。
- --service：类型归属的业务名，默认取环境变量 SERVICE_NAME，缺省 default（对齐 chat 的 ALLOWED_SERVICE_NAMES）。

对已存在的 type 采用「仅补充缺失字段」的向后兼容合并（meta 有实体数据时拒绝移除/改类型）；
若既有字段类型与落库约定冲突（如 date 被定义为 integer），脚本会给出告警并跳过该字段，
需要人工处理（删除重建该 type，注意有实体数据时 meta 禁止删除）。
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

# 与 chat-service app/ai/tools/ 落库 data **业务字段**逐字段对齐的外层 schema。
# user_id / created_time 是 MetaSDK entry 元数据（owner_user_id / created_at），
# 由 meta 自动管理，不进入 data，故不在 schema 范围内。
# 嵌套结构（unit / nutrition / foods）用宽松 object/dict（不声明子字段），
# 任意嵌套内容都会被保留，避免嵌套字段被丢弃。
CALOPLAN_TYPES: dict[str, dict] = {
    "food": {
        "description": "caloplan 食物库条目（create_food / list_my_food）",
        "schema_json": {
            "fields": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "image": {"type": "string"},
                "unit": {"type": "object"},
                "nutrition": {"type": "object"},
            }
        },
    },
    "meal": {
        "description": "caloplan 膳食记录（create_meal / list_my_meal）",
        "schema_json": {
            "fields": {
                "id": {"type": "string"},
                "tips": {"type": "string"},
                "type": {"type": "string"},
                "foods": {"type": "dict"},
                "nutrition": {"type": "object"},
            }
        },
    },
    "body": {
        "description": "caloplan 每日身体指标（upsert_my_body / get_my_body_by_date）",
        "schema_json": {
            "fields": {
                "date": {"type": "string"},
                "age": {"type": "number"},
                "height": {"type": "number"},
                "weight": {"type": "number"},
            }
        },
    },
    "nutrition": {
        "description": "caloplan 每日营养目标（upsert_my_nutrition / get_my_nutrition_by_date）",
        "schema_json": {
            "fields": {
                "date": {"type": "string"},
                "carbon": {"type": "number"},
                "protein": {"type": "number"},
                "fat": {"type": "number"},
                "salt": {"type": "number"},
                "calorie": {"type": "number"},
            }
        },
    },
}

# 落库约定类型（用于冲突告警，不覆盖已有定义）
_EXPECTED_TYPES = {
    "date": "string",
    "id": "string",
    "age": "number",
    "height": "number",
    "weight": "number",
    "carbon": "number",
    "protein": "number",
    "fat": "number",
    "salt": "number",
    "calorie": "number",
}


def _merge_schema(existing_schema: dict, required_schema: dict) -> tuple[dict, list[str]]:
    """合并：保留已有字段定义，仅补充缺失字段。返回 (新 schema, 冲突告警列表)。"""
    existing_fields = existing_schema.get("fields", {}) or {}
    required_fields = required_schema.get("fields", {})
    merged: dict = {}
    warnings: list[str] = []

    # 已有字段：保留；类型与落库约定不符时告警
    for name, definition in existing_fields.items():
        merged[name] = definition
        expected = _EXPECTED_TYPES.get(name)
        actual = definition.get("type") if isinstance(definition, dict) else None
        if expected and actual and actual != expected:
            warnings.append(f"字段 {name} 既有类型 {actual} 与落库约定 {expected} 不符（有实体数据时 meta 拒绝改类型，需人工处理）")

    # 缺失字段：补充
    for name, definition in required_fields.items():
        if name not in merged:
            merged[name] = definition
            warnings.append(f"补充缺失字段 {name}（{definition.get('type')}）")

    return {"fields": merged}, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化 meta-service 的 caloplan 类型 schema")
    parser.add_argument("--token", default=os.environ.get("META_ADMIN_TOKEN", ""), help="meta-service superuser JWT")
    parser.add_argument("--base-url", default=os.environ.get("META_SERVICE_URL", "http://localhost:9093"), help="meta-service 地址")
    parser.add_argument("--service", default=os.environ.get("SERVICE_NAME", "default"), help="类型归属业务名")
    args = parser.parse_args()

    if not args.token:
        print("缺少 --token（superuser JWT），无法创建/更新类型。", file=sys.stderr)
        return 1

    base = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.token}"}
    ok = True

    with httpx.Client(base_url=base, timeout=15) as client:
        for type_name, spec in CALOPLAN_TYPES.items():
            print(f"\n== {type_name} ==")
            try:
                r = client.get(f"/api/v1/types/{type_name}", headers=headers, params={"service_name": args.service})
            except httpx.HTTPError as exc:
                print(f"  连接失败: {exc}", file=sys.stderr)
                ok = False
                continue

            if r.status_code == 404:
                body = {
                    "type_name": type_name,
                    "service_name": args.service,
                    "description": spec["description"],
                    "schema_json": spec["schema_json"],
                }
                r2 = client.post("/api/v1/types", headers=headers, json=body)
                if r2.status_code in (200, 201):
                    print(f"  创建成功: {r2.status_code}")
                else:
                    print(f"  创建失败 {r2.status_code}: {r2.text[:300]}", file=sys.stderr)
                    ok = False
                continue

            if r.status_code != 200:
                print(f"  查询失败 {r.status_code}: {r.text[:300]}", file=sys.stderr)
                ok = False
                continue

            existing_schema = r.json().get("schema_json", {})
            merged, warnings = _merge_schema(existing_schema, spec["schema_json"])
            body = {"description": spec["description"], "schema_json": merged}
            r2 = client.put(f"/api/v1/types/{type_name}", headers=headers, params={"service_name": args.service}, json=body)
            if r2.status_code == 200:
                print(f"  更新成功（补齐 schema）")
                for w in warnings:
                    print(f"  - {w}")
            else:
                print(f"  更新失败 {r2.status_code}: {r2.text[:300]}", file=sys.stderr)
                ok = False

    print("\n完成。" if ok else "\n存在失败项，请按上方信息处理。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
