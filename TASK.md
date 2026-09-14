# TASK: caloplan AI Tools 落地（fastapi-chat-service）

> 状态：**已确认 v1**（2026-09-14 多轮讨论敲定，待 implement）
> 前置：PydanticAI + DeepSeek 接入、taskid 审批流（Redis TTL=300s）、Lua 原子消费均已就绪（40 tests passed）

## 1. 背景与定位

- chat-service 为 **caloplan 专属** AI 会话服务，不是通用微服务框架。
- 基于现有 `app/ai/tools/` 注册机制与审批流，为 Agent 接入 caloplan 数据域的 tool：**food / meal / body / nutrition**。
- 所有 tool 直接 HTTP 调用 meta-service（`mservice-fastapi-metastorage` :9093），**复用当前请求 JWT**，不经 TS 业务包（caloplan-core / caloplan-user）。

## 2. 边界（已确认，硬约束）

| 数据域 | 读 | 写 | 审批 |
|---|---|---|---|
| food | ✅ 自由 | ✅ | 写需审批 |
| meal | ✅ 自由 | ✅ | 写需审批 |
| body（身体指标） | ✅ 自由 | ✅ 每日一条，可无限更新（upsert） | 写需审批 |
| nutrition（营养目标） | ✅ 自由 | ✅ 每日一条，可无限更新（upsert） | 写需审批 |
| 用户资料（user-service） | ❌ | ❌ | **AI 永远不允许涉及**（含 updateProfile / 任何用户数据读写） |
| service_name | 数据访问限制维度；由 token 决定，**不暴露给模型**，不可跨服务 | | |

- **演示 tool 移除**：`add_numbers` / `deploy_service` 删除；保留 `get_current_time`（meal/body/nutrition 按天记录需要日期）。
- **entityKey**：由 chat-service 服务端生成 **uuid4 短码**（如 `uuid4().hex[:8]`），不暴露给模型；本质无业务含义，仅防穿梭与数据唯一。
- **userId 注入**：meta data 中的 `user_id` 字段由服务端从当前 JWT 注入，模型不可见、不可改。

## 3. Tool 清单

### 3.1 读（免审批）

| tool | 说明 | 入参（模型可见） | 备注 |
|---|---|---|---|
| `get_current_time` | 当前日期/时间 | — | 保留的通用 tool |
| `list_my_food` | 当前用户食物列表 | `name?`（模糊过滤可选） | meta `food` 类型 query，filters `user_id` |
| `list_my_meal` | 当前用户膳食列表 | `type?`（breakfast/launch/dinner/snack 过滤可选） | meta `meal` 类型 query |
| `get_my_body_by_date` | 指定日期身体指标 | `date`（YYYY-MM-DD） | meta `body` 类型，无记录返回空 |
| `get_my_nutrition_by_date` | 指定日期营养目标 | `date`（YYYY-MM-DD） | meta `nutrition` 类型 |

### 3.2 写（`requires_approval=True`，走 taskid 审批流）

| tool | 说明 | 入参（模型可见） | 服务端职责 |
|---|---|---|---|
| `create_food` | 添加食物 | `name, unit{unit,value}, nutrition{carbon,protein,fat,salt,energy}` | 生成 entityKey、注入 user_id，POST meta `/entries`（type=food） |
| `create_meal` | 添加膳食 | `type, tips?, foods:[{food_id, amount}]` | **按 food_id 批量查 food → 构造快照（name/image/unit/nutrition×amount）→ 合计 nutrition → 生成 entityKey**；**未找到的 food_id 报错**（不静默跳过）；POST meta `/entries`（type=meal） |
| `upsert_my_body` | 当日身体指标 | `date?, age, height, weight` | 查当日记录：无 → create；有 → merge 更新（仅变更字段） |
| `upsert_my_nutrition` | 当日营养目标 | `date?, carbon, protein, fat, salt, calorie` | 同上（type=nutrition） |

## 4. 服务端职责（chat-service 内）

1. **meta 客户端**：httpx AsyncClient 封装，BaseURL = `META_SERVICE_URL`，`Authorization: Bearer <当前请求 JWT>`（从请求上下文取，不落库）。
2. **entityKey 生成**：`uuid4().hex[:8]`，写 tool 内生成。
3. **userId 注入**：写 tool 的 data 中 `user_id = 当前 JWT user_id`（与 caloplan-core 落库约定一致：data 存 userId，查询用 filters `user_id`）。
4. **meal 快照计算**：`create_meal` 由服务端批量 POST `/entries/batch`（type=food, keys=food_id 列表）→ 每个 food 快照 `{food_id, name, image, unit, amount, nutrition: food.nutrition × amount}` → 合计 nutrition（同单位累加）；**未找到的 food_id 报错**（映射为可读错误，不静默跳过）。
5. **upsert 语义**：`get_my_body_by_date`/`get_my_nutrition_by_date` 查当日 → 无则 POST create（date 默认当天）；有则 PUT update（data 仅含变更字段，deep merge + 版本自增由 meta 保证）。
6. **数据隔离**：不传 `service_name` 给 meta（默认当前 token 身份）；meta 侧 scope 自动限定；超界（403）原样透传错误。

## 5. 配置变更

- `app/core/config.py`：新增 `META_SERVICE_URL`（默认 `http://localhost:9093`）。
- `AI_ENABLED_TOOLS` 默认值更新为 caloplan 工具集（移除 add_numbers / deploy_service）。
- `.env.example` / `docker-compose.yml` / `README.md` 同步。

## 6. 实现步骤

1. `app/ai/tools/meta_client.py`：httpx 客户端 + token 注入 + 错误映射（401/403/404/409/422 → 可读消息）。
2. `app/ai/tools/caloplan_food.py` / `caloplan_meal.py` / `caloplan_body.py` / `caloplan_nutrition.py`：注册各 tool。
3. `app/ai/tools/__init__.py`：注册表更新（移除演示 tool）。
4. `app/core/config.py` + `.env.example` + `README.md` 更新。
5. `app/ai/agent.py`：无需改动（自动检测 has_approval_tools）。

## 7. 测试计划

- **注册**：caloplan tools 注册成功、requires_approval 标记正确；演示 tool 不存在。
- **meta 调用**：httpx MockTransport 模拟 meta 响应；验证 URL/方法/headers（JWT 透传）/body（userId 注入、entityKey 格式）。
- **meal 快照**：batch 查 food → 快照 + 合计 nutrition 计算正确；未知 food_id 行为。
- **upsert**：当日无记录 → create；有记录 → update（仅变更字段）。
- **审批流**：写 tool 触发 taskid；批准后执行；拒绝后不执行（沿用现有 test_approval 框架）。
- 全量 `pytest tests -q`（现有 test_tools 适配演示 tool 移除）。

## 8. 已确认决策（2026-09-14 用户拍板）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | `create_meal` 未找到的 `food_id` | **报错**，不静默跳过（映射为可读错误返回） |
| 2 | body / nutrition tool 形态 | **upsert 单 tool**（`upsert_my_body` / `upsert_my_nutrition`），贴近"每日一条、可无限更新"语义 |
| 3 | 第一版写范围 | food/meal 仅 `create`；body/nutrition `upsert`；food/meal 的 **update/delete 留待后续迭代** |
