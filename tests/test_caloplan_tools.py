"""caloplan 数据域 tools 测试：meta 调用（MockTransport）、快照计算、upsert 语义、审批标记。

关键测试手法：
- 用 httpx.MockTransport 模拟 meta-service，monkeypatch `app.ai.tools.meta_client._client`
  为注入测试 transport 的 MetaClient（不发起真实网络请求）；
- 每个 tool 调用都包在 `bind_ai_context(token, user)` 内，模拟 run_chat / resolve_approval
  执行期间的请求上下文（JWT 透传 + user_id 注入）。
"""

import json
import re

import httpx
import pytest

from app.ai.context import bind_ai_context
from app.ai.tools.caloplan_body import UpsertBodyParams, get_my_body_by_date, upsert_my_body
from app.ai.tools.caloplan_food import CreateFoodParams, NutritionValues, create_food, list_my_food
from app.ai.tools.caloplan_meal import CreateMealParams, MealFoodItem, create_meal
from app.ai.tools.caloplan_nutrition import UpsertNutritionParams, get_my_nutrition_by_date, upsert_my_nutrition
from app.ai.tools.meta_client import MetaClient
from app.schemas.auth import CurrentUser

TEST_USER = CurrentUser(sub="alice", user_id=42, service_name="caloplan", role="user", type="access")


def _entry(type_name: str, entity_key: str, data: dict, **extra) -> dict:
    return {
        "id": 1,
        "type_name": type_name,
        "entity_key": entity_key,
        "data": data,
        "tags": [],
        "version": 1,
        "owner_user_id": 42,
        "service_name": "caloplan",
        "created_at": "2026-09-14T10:00:00",
        "updated_at": None,
        **extra,
    }


def make_meta_server():
    """构造内存版 meta-service：返回 (entries 存储, 请求记录列表, handler)。"""
    entries: dict[tuple[str, str], dict] = {}
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        method = request.method

        if method == "POST" and path == "/api/v1/entries":
            body = json.loads(request.content)
            entry = _entry(body["type_name"], body["entity_key"], body["data"], tags=body.get("tags", []))
            entries[(body["type_name"], body["entity_key"])] = entry
            return httpx.Response(201, json=entry)

        if method == "GET" and path == "/api/v1/entries":
            params = dict(request.url.params)
            type_name = params.pop("type_name", "")
            params.pop("page", None)
            params.pop("page_size", None)
            params.pop("sort_order", None)
            params.pop("tags", None)
            items = [
                e
                for (t, _k), e in entries.items()
                if t == type_name and all(str(e["data"].get(k)) == str(v) for k, v in params.items())
            ]
            return httpx.Response(200, json={"total": len(items), "items": items})

        if method == "POST" and path == "/api/v1/entries/batch":
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={k: entries.get((body["type_name"], k)) for k in body["keys"]},
            )

        if method == "PUT" and path.startswith("/api/v1/entries/"):
            _v, _api, _entries, type_name, entity_key = path.strip("/").split("/")
            old = entries[(type_name, entity_key)]
            merged = {**old["data"], **json.loads(request.content).get("data", {})}
            entry = {**old, "data": merged, "version": old["version"] + 1}
            entries[(type_name, entity_key)] = entry
            return httpx.Response(200, json=entry)

        return httpx.Response(404, json={"detail": "not found"})

    return entries, calls, handler


@pytest.fixture
def meta_mock(monkeypatch):
    """注入内存 meta-service：返回 (entries, calls)。"""
    entries, calls, handler = make_meta_server()
    client = MetaClient("http://meta.test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr("app.ai.tools.meta_client._client", client)
    return entries, calls


# ── create_food ─────────────────────────────────────────────

async def test_create_food_success(meta_mock):
    entries, calls = meta_mock
    with bind_ai_context("test-token", TEST_USER):
        result = await create_food(
            CreateFoodParams(
                name="苹果",
                unit="个",
                unit_value=1,
                nutrition=NutritionValues(carbon=0.2, protein=0.1, fat=0.05, salt=0.001, energy=100),
            )
        )
    assert result["ok"] is True
    assert len(result["id"]) == 8  # uuid4 短码
    entry = entries[("food", result["id"])]
    data = entry["data"]
    # user_id 是 entry 元数据（owner_user_id），不进入 data 业务字段
    assert data["user_id"] == "42"  # meta schema required
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", data["created_time"]) is not None
    assert entry["owner_user_id"] == 42  # meta 从 JWT 自动写入
    assert data["unit"] == {"unit": "个", "value": 1}
    assert data["nutrition"]["carbon"] == {"unit": "kg", "value": 0.2}
    assert data["nutrition"]["salt"] == {"unit": "g", "value": 0.001}
    assert data["nutrition"]["energy"] == {"unit": "kcal", "value": 100}
    # JWT 透传
    assert calls[0].headers["Authorization"] == "Bearer test-token"


async def test_create_food_meta_422_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={"detail": [{"loc": ["body", "data", "name"], "msg": "String should have at least 1 character"}]},
        )

    client = MetaClient("http://meta.test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr("app.ai.tools.meta_client._client", client)
    with bind_ai_context("t", TEST_USER):
        result = await create_food(CreateFoodParams(name="x", unit="g", unit_value=1, nutrition=NutritionValues()))
    assert result["ok"] is False
    assert "校验失败" in result["error"]


# ── list_my_food ────────────────────────────────────────────

async def test_list_my_food(meta_mock):
    entries, _calls = meta_mock
    entries[("food", "aaaaaaaa")] = _entry(
        "food",
        "aaaaaaaa",
        {
            "id": "aaaaaaaa",
            "user_id": "42",
            "name": "苹果",
            "image": "",
            "unit": {"unit": "个", "value": 1},
            "nutrition": {"carbon": {"unit": "kg", "value": 0.2}, "protein": {"unit": "kg", "value": 0.1},
                          "fat": {"unit": "kg", "value": 0.05}, "salt": {"unit": "g", "value": 0.001},
                          "energy": {"unit": "kcal", "value": 100}},
            "created_time": "2026-09-14T10:00:00",
        },
    )
    with bind_ai_context("t", TEST_USER):
        result = await list_my_food()
    assert result["ok"] is True
    assert result["total"] == 1
    assert result["items"][0]["id"] == "aaaaaaaa"
    assert result["items"][0]["nutrition"]["energy"] == 100  # 数值化返回


async def test_list_my_food_without_token():
    """绑定上下文但无 token → 明确报错，不发起请求。"""
    with bind_ai_context(None, TEST_USER):
        result = await list_my_food()
    assert result["ok"] is False
    assert "缺少访问令牌" in result["error"]


# ── create_meal ─────────────────────────────────────────────

def _food_entry(entity_key: str, name: str, energy: float, protein: float = 0.1) -> dict:
    return _entry(
        "food",
        entity_key,
        {
            "id": entity_key,
            "user_id": "42",
            "name": name,
            "image": "",
            "unit": {"unit": "个", "value": 1},
            "nutrition": {"carbon": {"unit": "kg", "value": 0.2}, "protein": {"unit": "kg", "value": protein},
                          "fat": {"unit": "kg", "value": 0.05}, "salt": {"unit": "g", "value": 0.001},
                          "energy": {"unit": "kcal", "value": energy}},
            "created_time": "2026-09-14T10:00:00",
        },
    )


async def test_create_meal_success(meta_mock):
    entries, _calls = meta_mock
    entries[("food", "aaaaaaaa")] = _food_entry("aaaaaaaa", "苹果", 50)
    entries[("food", "bbbbbbbb")] = _food_entry("bbbbbbbb", "面包", 100)

    with bind_ai_context("t", TEST_USER):
        result = await create_meal(
            CreateMealParams(
                type="breakfast",
                tips="元气早餐",
                foods=[MealFoodItem(food_id="aaaaaaaa", amount=2), MealFoodItem(food_id="bbbbbbbb", amount=1)],
            )
        )
    assert result["ok"] is True
    meal = entries[("meal", result["id"])]
    data = meal["data"]
    assert data["type"] == "breakfast"
    assert data["tips"] == "元气早餐"
    assert data["user_id"] == "42"  # meta schema required
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", data["created_time"]) is not None
    assert meal["owner_user_id"] == 42  # meta 从 JWT 自动写入
    # 快照：nutrition × amount
    assert data["foods"]["aaaaaaaa"]["amount"] == 2
    assert data["foods"]["aaaaaaaa"]["nutrition"]["energy"] == {"unit": "kcal", "value": 100}  # 50 × 2
    assert data["foods"]["aaaaaaaa"]["name"] == "苹果"
    # 合计营养
    assert data["nutrition"]["energy"] == {"unit": "kcal", "value": 200}  # 100 + 100
    assert data["nutrition"]["protein"] == {"unit": "kg", "value": 0.3}  # 0.1×2 + 0.1×1
    # 返回展示数值化
    assert result["nutrition"]["energy"] == 200


async def test_create_meal_missing_food_reports_error(meta_mock):
    _entries, _calls = meta_mock
    with bind_ai_context("t", TEST_USER):
        result = await create_meal(
            CreateMealParams(type="dinner", foods=[MealFoodItem(food_id="deadbeef", amount=1)])
        )
    assert result["ok"] is False
    assert "未找到食物" in result["error"]
    assert "deadbeef" in result["error"]


# ── upsert_my_body（每日一条，可无限更新）────────────────────

async def test_upsert_body_creates_when_no_record(meta_mock):
    entries, _calls = meta_mock
    with bind_ai_context("t", TEST_USER):
        result = await upsert_my_body(UpsertBodyParams(date="2026-09-14", age=25, height=180, weight=75))
    assert result["ok"] is True
    assert result["action"] == "created"
    entry = entries[("body", result["id"])]
    assert entry["data"]["date"] == "2026-09-14"
    assert entry["data"]["user_id"] == "42"  # meta schema required
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", entry["data"]["created_time"]) is not None
    assert entry["owner_user_id"] == 42
    assert entry["data"]["weight"] == 75


async def test_upsert_body_updates_when_record_exists(meta_mock):
    entries, calls = meta_mock
    entries[("body", "cccccccc")] = _entry(
        "body",
        "cccccccc",
        {"id": "cccccccc", "user_id": "42", "date": "2026-09-14", "age": 25, "height": 180, "weight": 75,
         "created_time": "2026-09-14T08:00:00"},
    )
    with bind_ai_context("t", TEST_USER):
        result = await upsert_my_body(UpsertBodyParams(date="2026-09-14", age=25, height=180, weight=74))
    assert result["ok"] is True
    assert result["action"] == "updated"
    assert result["id"] == "cccccccc"
    updated = entries[("body", "cccccccc")]
    assert updated["data"]["weight"] == 74  # 变更字段生效
    assert updated["data"]["height"] == 180  # 未变更字段保留
    assert updated["version"] == 2
    # PUT 请求 data 仅含业务变更字段（date 是记录标识，不参与更新）
    put_body = json.loads(calls[-1].content)
    assert put_body["data"] == {"age": 25, "height": 180, "weight": 74}


async def test_get_my_body_by_date(meta_mock):
    entries, _calls = meta_mock
    entries[("body", "cccccccc")] = _entry(
        "body", "cccccccc",
        {"id": "cccccccc", "user_id": "42", "date": "2026-09-14", "age": 25, "height": 180, "weight": 75,
         "created_time": "2026-09-14T08:00:00"},
    )
    with bind_ai_context("t", TEST_USER):
        found = await get_my_body_by_date("2026-09-14")
        missing = await get_my_body_by_date("2026-09-13")
    assert found["found"] is True
    assert found["weight"] == 75
    assert missing["found"] is False


# ── upsert_my_nutrition ─────────────────────────────────────

async def test_upsert_nutrition_creates(meta_mock):
    entries, _calls = meta_mock
    with bind_ai_context("t", TEST_USER):
        result = await upsert_my_nutrition(
            UpsertNutritionParams(date="2026-09-14", carbon=250, protein=150, fat=60, salt=5, calorie=2000)
        )
    assert result["ok"] is True
    assert result["action"] == "created"
    data = entries[("nutrition", result["id"])]["data"]
    assert data["calorie"] == 2000
    assert data["date"] == "2026-09-14"


async def test_upsert_nutrition_updates(meta_mock):
    entries, _calls = meta_mock
    entries[("nutrition", "dddddddd")] = _entry(
        "nutrition", "dddddddd",
        {"id": "dddddddd", "user_id": "42", "date": "2026-09-14", "carbon": 250, "protein": 150, "fat": 60,
         "salt": 5, "calorie": 2000, "created_time": "2026-09-14T08:00:00"},
    )
    with bind_ai_context("t", TEST_USER):
        result = await upsert_my_nutrition(
            UpsertNutritionParams(date="2026-09-14", carbon=250, protein=150, fat=60, salt=5, calorie=2100)
        )
    assert result["ok"] is True
    assert result["action"] == "updated"
    assert entries[("nutrition", "dddddddd")]["data"]["calorie"] == 2100


async def test_get_my_nutrition_by_date_not_found(meta_mock):
    _entries, _calls = meta_mock
    with bind_ai_context("t", TEST_USER):
        result = await get_my_nutrition_by_date("2026-09-14")
    assert result["found"] is False


# ── 上下文守卫 ──────────────────────────────────────────────

async def test_tool_outside_context_raises():
    """未绑定请求上下文时调用 tool → RuntimeError（防止越权/串号）。"""
    with pytest.raises(RuntimeError, match="未绑定"):
        await list_my_food()


# ── owner 隔离（meta 仅按 service 隔离，同 service 多用户数据互不可见）────────

async def test_upsert_body_ignores_other_users_record(meta_mock):
    """同日存在他人 body 记录（owner_user_id 不同）→ 视为无记录，走 created 而非 update。"""
    entries, _calls = meta_mock
    entries[("body", "cccccccc")] = _entry(
        "body",
        "cccccccc",
        {"date": "2026-09-14", "age": 30, "height": 170, "weight": 60},
        owner_user_id=99,  # 他人
    )
    with bind_ai_context("t", TEST_USER):
        result = await upsert_my_body(UpsertBodyParams(date="2026-09-14", age=25, height=180, weight=75))
    assert result["ok"] is True
    assert result["action"] == "created"
    # 他人记录未被修改
    assert entries[("body", "cccccccc")]["version"] == 1
    assert entries[("body", "cccccccc")]["data"]["weight"] == 60


async def test_get_my_body_by_date_ignores_other_user(meta_mock):
    """查询仅返回当前用户记录：同日他人记录 → found=false。"""
    entries, _calls = meta_mock
    entries[("body", "cccccccc")] = _entry(
        "body", "cccccccc", {"date": "2026-09-14", "age": 30, "height": 170, "weight": 60},
        owner_user_id=99,  # 他人
    )
    with bind_ai_context("t", TEST_USER):
        result = await get_my_body_by_date("2026-09-14")
    assert result["ok"] is True
    assert result["found"] is False


async def test_list_my_food_excludes_other_users(meta_mock):
    """食物列表按 owner 隔离：他人 food 不出现。"""
    entries, _calls = meta_mock
    entries[("food", "aaaaaaaa")] = _entry(
        "food", "aaaaaaaa",
        {"id": "aaaaaaaa", "name": "我的苹果", "image": "", "unit": {"unit": "个", "value": 1},
         "nutrition": {"carbon": {"unit": "kg", "value": 0.2}, "protein": {"unit": "kg", "value": 0.1},
                       "fat": {"unit": "kg", "value": 0.05}, "salt": {"unit": "g", "value": 0.001},
                       "energy": {"unit": "kcal", "value": 100}}},
        owner_user_id=42,
    )
    entries[("food", "bbbbbbbb")] = _entry(
        "food", "bbbbbbbb",
        {"id": "bbbbbbbb", "name": "他人的面包", "image": "", "unit": {"unit": "个", "value": 1},
         "nutrition": {"carbon": {"unit": "kg", "value": 0.2}, "protein": {"unit": "kg", "value": 0.1},
                       "fat": {"unit": "kg", "value": 0.05}, "salt": {"unit": "g", "value": 0.001},
                       "energy": {"unit": "kcal", "value": 100}}},
        owner_user_id=99,  # 他人
    )
    with bind_ai_context("t", TEST_USER):
        result = await list_my_food()
    assert result["ok"] is True
    assert result["total"] == 1
    assert result["items"][0]["id"] == "aaaaaaaa"
    assert "bbbbbbbb" not in [it["id"] for it in result["items"]]


async def test_create_meal_rejects_other_users_food(meta_mock):
    """create_meal 引用他人 food → 视为未找到，报错不落库。"""
    entries, _calls = meta_mock
    entries[("food", "bbbbbbbb")] = _food_entry("bbbbbbbb", "他人的面包", 100)
    # 他人 food 的 owner 改为 99
    entries[("food", "bbbbbbbb")]["owner_user_id"] = 99
    with bind_ai_context("t", TEST_USER):
        result = await create_meal(
            CreateMealParams(type="dinner", foods=[MealFoodItem(food_id="bbbbbbbb", amount=1)])
        )
    assert result["ok"] is False
    assert "未找到食物" in result["error"]
    assert "bbbbbbbb" in result["error"]
    assert not any(t == "meal" for (t, _k) in entries)
