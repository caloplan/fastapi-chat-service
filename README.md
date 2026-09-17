# Chat Service - 聊天微服务（caloplan 专属 AI 对话）

基于 **FastAPI + PydanticAI** 的聊天微服务，**无数据库存储、无状态对话**（历史由客户端全量回传）。
认证消费 **user-service** 签发的 RS256 JWT（兄弟微服务，自身不签发令牌、不存用户表）；
AI 模型接入 **DeepSeek**（官方端点），内置 **Tool 注册/配置基础设施** 与 **需审批 Tool 的 taskid 审批流**；
数据读写对接 **meta-service**（caloplan 数据域：food / meal / body / nutrition）。

> 本服务定位为 **caloplan 专属**，不做通用微服务框架。扩展业务前先读本 README 的「约定与坑」章节。

## 快速开始

```bash
# 1. 虚拟环境 + 依赖
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements-dev.txt

# 2. 配置（复制后按需改）
copy .env.example .env

# 3. 启动（端口 9095，demo 页面用 3000）
uvicorn app.main:app --reload --host 0.0.0.0 --port 9095
```

- 健康检查：`http://localhost:9095/health` ｜ Swagger：`http://localhost:9095/docs`
- Demo 页面（含登录/审批/自动续期）：`demo/index.html`，**推荐从 3000 端口打开防跨域**：
  `python -m http.server 3000 --directory demo` → http://localhost:3000

## 配置（.env 关键项）

| 变量 | 默认 | 说明 |
|---|---|---|
| `USER_SERVICE_URL` | `http://localhost:8000` | user-service 地址（JWKS 公钥来源） |
| `ALLOWED_SERVICE_NAMES` | `["default"]` | 服务名白名单，非 superuser 请求必须命中（否则 403） |
| `REDIS_URL` | `redis://localhost:6379/0` | 外部 Redis 直连（不内置镜像） |
| `META_SERVICE_URL` | `http://localhost:9093` | meta-service 地址（caloplan 数据域） |
| `AI_PROVIDER` | `deepseek` | `deepseek`=官方端点 / `openai`=OpenAI 兼容网关 |
| `AI_MODEL_NAME` | `deepseek-chat` | 对话模型；**图片输入需 `deepseek-flash`**（vision） |
| `AI_API_KEY` | 空 | 优先；为空回退 `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` |
| `AI_APPROVAL_TTL_SECONDS` | `300` | 审批 taskid 有效期（5min） |
| `AI_ENABLED_TOOLS` | `["*"]` | Tool 启用名单，`[]` 全部禁用 |

## API 端点

| 方法 | 端点 | 功能 | 认证 |
|---|---|---|---|
| GET | `/health` | 健康检查 | 否 |
| GET | `/` | 服务信息 | 否 |
| GET | `/api/v1/redis/ping` | Redis 连通性 | Bearer JWT |
| POST | `/api/v1/ai/chat` | AI 对话（可触发审批；`stream=true` 时 SSE 流式） | Bearer JWT |
| POST | `/api/v1/ai/approval` | 审批决定（批准/拒绝 taskid） | Bearer JWT |

### POST /api/v1/ai/chat

**请求**（无状态：历史由客户端全量回传，服务端零留存）：

```json
{
  "message": "帮我记录今天的身体指标",
  "history": [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好，有什么可以帮你？"}
  ]
}
```

**响应**（正常回复）：

```json
{
  "conversation_id": "…",
  "reply": "已记录…",
  "model": "deepseek-chat",
  "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
  "latency_ms": 850.0,
  "tool_calls": [],
  "need_approval": false
}
```

**命中需审批 Tool 时**（`need_approval=true`，`reply=""`，返回 `taskid` + `pending_tools`）：

```json
{
  "conversation_id": "…",
  "reply": "",
  "need_approval": true,
  "taskid": "9f2c…",
  "pending_tools": [
    {"tool_call_id": "call-1", "name": "upsert_my_body",
     "arguments": {"date": "2026-09-15", "age": 25, "height": 180, "weight": 74},
     "description": "执行需要用户确认"}
  ]
}
```

客户端展示 pending_tools 让用户判断 → 调 `/api/v1/ai/approval`：

```json
{"taskid": "9f2c…", "approved": true}
```

批准则执行 tool 并回传 `tool_results` 明细；拒绝则不执行、模型照常给最终回复。
**审批约束**：taskid 与发起者 user_id 绑定（越权 403）；TTL 5min 过期作废（410）；Lua 原子消费防并发重复执行。

### SSE 流式（stream=true）

请求加 `"stream": true`（默认 `false`）→ 响应切换为 `text/event-stream`，逐帧推送
`data: <json>\n\n` 事件，客户端按 `type` 区分：

```text
data: {"type":"text","content":"这是第一段增量"}
data: {"type":"text","content":"这是第二段增量"}
data: {"type":"done","conversation_id":"…","reply":"这是第一段增量这是第二段增量",
       "model":"deepseek-chat","usage":{"prompt_tokens":100,"completion_tokens":20,"total_tokens":120},
       "latency_ms":850.0,"tool_calls":[],"need_approval":false}
```

- **`text`**：文本增量（可多次，客户端累积展示即打字机效果）；`done`：终态事件，结构与
  JSON 分支的 `ChatResponse` 完全一致，`reply` 为完整文本；
- **命中需审批 Tool 时**：因审批 Tool 为 deferred 模式，流式过程**不产出任何 text 事件**，
  直接推 `approval` 事件 + `done`（`need_approval=true`，含 `taskid`/`pending_tools`，语义与 JSON 分支相同），
  客户端据 `done.need_approval` 显示审批卡即可；
- **出错时**：推 `{"type":"error","detail":"…"}` 后结束流；
- 多模态（当前轮 message / history 带 image_url）与 `stream=true` 可同时使用（前提同为 `deepseek-flash`）。

curl 示例：

```bash
curl -N -X POST http://localhost:9095/api/v1/ai/chat \
  -H "Authorization: Bearer <JWT>" -H "Content-Type: application/json" \
  -d '{"message":"讲个冷笑话","stream":true}'
```

### 多模态：当前轮 message 与 history 均可携带图片

**当前轮 `message`** 支持纯文本，或 OpenAI/DeepSeek 风格内容块数组（文本 + 图片），
图片以 `image_url` 块传入，**url 与 base64 data URL 两种形式均可**：

```json
{
  "message": [
    {"type": "text", "text": "这张图里的食物营养怎么样？"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQSkZJRg..."}}
  ]
}
```

纯图片提问（无文本）也可直接传 `[{"type":"image_url","image_url":{"url":"https://…/a.jpg"}}]`。
`image_url` 兼容 `{"url": "…"}` 包裹或纯字符串两种写法。

**`history` 回传**同样支持：**user 消息**的 content 可为内容块数组（文本 + 图片）：

```json
{
  "message": "这张图里的食物营养怎么样？",
  "history": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "看看这张图"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQSkZJRg..."}}
      ]
    },
    {"role": "assistant", "content": "我看到了"}
  ]
}
```

服务端自动把数组解析为 pydantic-ai 的 `TextContent`/`ImageUrl` parts（`_message_to_prompt` /
`_content_blocks_to_parts`），序列化后与 DeepSeek vision 的 content 数组格式完全一致（已实测验证）。

**前提与限制**：
- 模型必须是 **`deepseek-flash`**（`AI_MODEL_NAME=deepseek-flash`；`deepseek-chat` 不支持图片）；
- 图片只允许出现在 **user** 消息（system/assistant 带图 → DeepSeek 返回 400）；
- 格式 JPEG/PNG/GIF/WebP；base64 内联受 48MiB 请求体限制、外部 URL ≤8192 字符、单图 token 上限 1024；
- 内容块数组为空/全无效 → 422；与 `stream=true` 可同时使用。

## Tool 体系（caloplan 数据域）

`app/ai/tools/`：`@register_tool(name, description, requires_approval)` 注册 → `build_tools()` 自动发现 →
按 `AI_ENABLED_TOOLS` 过滤 → 注入 Agent。tool 内用 `require_ai_context()` 读取当前请求 JWT 与用户身份
（`app/ai/context.py` contextvar 绑定；审批续跑同样绑定，批准执行的 tool 可透传令牌调 meta-service）。

| Tool | 类型 | 说明 |
|---|---|---|
| `get_current_time` | 读（免审批） | 当前日期时间 |
| `list_my_food` | 读（免审批） | 我的食物库（供 create_meal 引用 food_id） |
| `list_my_meal` | 读（免审批） | 我的膳食列表（可按 `created_time` 按日过滤，YYYY-MM-DD，不传返回全部） |
| `get_my_body_by_date` | 读（免审批） | 指定日期身体指标 |
| `get_my_nutrition_by_date` | 读（免审批） | 指定日期营养目标 |
| `create_food` | 写（需审批） | 添加食物（entity_key=uuid4 短码；owner_user_id 由 meta 从 JWT 自动注入） |
| `create_meal` | 写（需审批） | 添加膳食（模型只传 food_id+份数，服务端批量查食物构造快照并合计营养；**未找到的 food_id 报错**） |
| `upsert_my_body` | 写（需审批） | 当日身体指标：每日一条、无则创建、有则仅更新变更字段 |
| `upsert_my_nutrition` | 写（需审批） | 当日营养目标：同上 |

**边界（硬约束）**：用户资料（user-service 的用户数据）AI 永远不允许涉及；`service_name` 仅作数据访问限制（由 token 决定，不暴露给模型、不可跨服务）。

## 与 meta-service 对接（caloplan 数据域）

- caloplan tools 直接 HTTP 调用 meta-service（地址 `META_SERVICE_URL`），`Authorization` 头复用当前请求 Bearer token；
- **JWT 透传**：与 user-service 同一套 RS256 令牌，meta 侧 Scope 按 token 的 `service_name` 隔离；
- **审批续跑**：批准执行的 tool 同样绑定当前请求 JWT，透传 meta 无感知；
- **不暴露给模型**：`service_name` / entity_key 由服务端生成，模型不可见、不可改。

### 数据模型：data 存业务字段（含 user_id / created_time），归属与版本在 entry 元数据

meta 的 entry 分两层：

- **data（业务字段，snake_case，对齐 caloplan-core 落库契约）**：`food={id,user_id,name,image,unit,nutrition,created_time}`、`meal={id,user_id,tips,type,foods,nutrition,created_time}`、`body={date,user_id,age,height,weight,created_time}`、`nutrition={date,user_id,carbon,protein,fat,salt,calorie,created_time}`；`user_id` 为当前用户（冗余兜底，meta 已按 owner 隔离），`created_time` 统一存 `yyyy-mm-dd`（供按日过滤查询）；
- **entry 元数据（MetaSDK，由 meta 管理、不进 data）**：`entity_key` / `owner_user_id`（= 创建时 JWT 的 user_id，meta 自动写入）/ `service_name` / `created_at` / `updated_at` / `version`。

**用户隔离**：meta 在查询/批量查询时按 `service_name` + `owner_user_id` **双重隔离**（MetaScope：普通身份强制自身 service 且自身 owner，越权数据返回 `null`/不可见），
因此 chat 侧查询响应后按 `entry.owner_user_id` **本地过滤**当前用户（`_caloplan_common.filter_by_owner`）属防御性冗余；
`create_meal` 批量引用 food 同样按 owner 过滤，他人的 food 视为未找到并报错。

### 坑：data 业务字段必须在 type schema 中（否则字段被丢弃）

meta 创建/更新 entry 时按 type schema 动态校验，**未在 schema 中定义的字段会被丢弃**（Pydantic 默认忽略额外字段）。
若类型的 schema 缺少 data 业务字段（尤其 `date` / `created_time` / `user_id`），会导致：创建时该字段被丢 → 按该字段过滤查询永远查不到
（list 返回空、upsert 只 create 不 update）——**即使 Agent 调用查询 tool 也查不到**。

类型 schema 由 meta 管理后台（server-meta-admin）维护（类型管理仅 superuser）：

- 四个类型（food / meal / body / nutrition）的 schema 必须包含 chat 落库 data 的**全部业务字段**（外层字段精确，嵌套 unit / nutrition / foods 用宽松 object / dict 防嵌套字段被丢）；
- 新增 data 业务字段时需同步在后台补充到对应类型 schema（已有实体数据时 meta 仅允许新增字段，拒绝移除/改类型）；
- **已有脏数据**：补齐 schema 前创建且业务字段已被丢弃的记录无法恢复（查询同样查不到），如为测试数据可忽略或清理。

## 需审批 Tool 的 taskid 审批流

```
客户端                    服务端                        Redis
   │  POST /ai/chat          │                            │
   │────────────────────────>│ agent.run(prompt)          │
   │                         │ 命中需审批 tool            │
   │                         │ → 生成 taskid              │
   │                         │───────────────────────────>│ SET approval:{taskid}
   │                         │                            │   = {消息快照, 待审批调用, user_id}
   │                         │                            │   EXPIRE AI_APPROVAL_TTL_SECONDS
   │<─ need_approval=true ───│                            │
   │  taskid + pending_tools │                            │
   │                         │                            │
   │  POST /ai/approval      │                            │
   │  {taskid, approved}     │                            │
   │────────────────────────>│ 校验归属 → 取快照 → 续跑    │
   │                         │───────────────────────────>│ DEL approval:{taskid}（原子消费）
   │<──── 最终回复 / 拒绝 ────│                            │
```

- **taskid 存 Redis**：`{REDIS_PREFIX}:approval:{taskid}`，TTL=`AI_APPROVAL_TTL_SECONDS`（默认 300s），含发起者 `user_id` 与消息快照；
- **消费**：Redis Lua 脚本原子完成"存在性检查 + 创建者 user_id 校验 + 删除"；重复提交 → 410；超时 → TTL 作废；越权 → 403；
- 方案 A 语义：常态下服务端零内容留存（客户端持有全量历史）；仅待审批瞬间暂存快照于 Redis，TTL 即焚。

## 认证机制（对齐 mservice-fastapi-user）

- user-service 签发 RS256 JWT，Header 携带 `kid`（RFC 7638 thumbprint），payload：`sub` / `user_id` / `service_name` / `role` / `type`；
- 校验（`app/core/security.py`）：取 `kid` → 本地 JWKS 缓存命中则验，否则从 `USER_SERVICE_URL` 刷新再验；`type != access` / 过期 / 伪造 / 未知 kid → 401；
- 权限（`app/core/dependencies.py`）：`get_current_user` 解析 JWT 不落库；**服务名白名单**（非 superuser 的 `service_name` 必须命中 `ALLOWED_SERVICE_NAMES`，否则 403）；`require_superuser` **三重 AND**（role=superuser + 用户名白名单 + user_id 白名单）。

## Demo 页面

- 位置 `demo/index.html`；**推荐从 3000 端口打开**（Origin 已在 user-service / chat-service CORS 白名单）：
  ```bash
  python -m http.server 3000 --directory demo    # → http://localhost:3000
  ```
- 也可从 chat-service 同源访问 **http://localhost:9095/demo/**（main.py 自动挂载 `/demo`）；
- 功能：粘贴 JWT 或页面内登录（**OAuth2 表单**，非 JSON，页面内有正确 curl 指引）；**令牌自动续期**（登录保存 refresh_token，401 自动调 user-service `/api/v1/auth/refresh` 滚动续期并重试原请求）；审批卡片展示/批准/拒绝；tool 结果格式化展示；
- 设置面板可改 Chat Service / User Service 地址（默认 chat `http://localhost:3000`、user 为远程实际地址，localStorage 持久化）。

## 运行测试

```bash
pytest tests -v    # 当前 70 passed
```

覆盖：认证（令牌解析/白名单/权限三重 AND）；Redis/AI 端点（mock）；Tool 注册与配置过滤；
审批流全链路（触发→暂存→批准/拒绝→消费，过期 410/越权 403，续跑 JWT 透传）；caloplan tools
（meta 内存 mock：读写、meal 快照与合计营养、upsert 语义、**owner 隔离**、entity_key 注入）；
**多模态**（history content 数组 → TextContent/ImageUrl parts；**当前轮 message 数组**（base64/url/纯字符串 URL），
空块 422，端到端 422→200）；**SSE 流式**（stream=true：text 增量 + done 终态；审批分支 approval 事件 +
done(need_approval=true)、快照入库；stream 缺省仍为 JSON）。

## 项目结构

```
fastapi-chat-service/
├── app/
│   ├── main.py                          # 应用入口（lifespan 日志+Redis；挂载路由与 /demo）
│   ├── core/
│   │   ├── config.py                    # 全局配置（user-service/JWKS/白名单/Redis/AI/meta）
│   │   ├── security.py                  # JWKS 获取/缓存 + JWT 校验
│   │   ├── dependencies.py              # 认证依赖（get_current_user / require_superuser / 白名单）
│   │   └── redis.py                     # Redis 生命周期 + get_redis 依赖
│   ├── ai/
│   │   ├── agent.py                     # PydanticAI Agent 构建（DeepSeek/OpenAI 兼容 + 审批输出类型切换）
│   │   ├── context.py                   # AI 请求上下文（JWT + 用户身份 contextvar 绑定）
│   │   ├── service.py                   # AI 编排：run_chat / run_chat_stream（SSE 流式）/ resolve_approval（taskid 审批流；多模态历史解析）
│   │   └── tools/
│   │       ├── __init__.py              # register_tool / build_tools / AI_ENABLED_TOOLS 过滤
│   │       ├── meta_client.py           # meta-service HTTP 客户端（JWT 透传 + 错误映射）
│   │       ├── _caloplan_common.py      # 共享：entity_key / 营养单位 / filter_by_owner
│   │       ├── basic.py                 # get_current_time
│   │       ├── caloplan_food.py         # create_food / list_my_food
│   │       ├── caloplan_meal.py         # create_meal / list_my_meal（快照+合计营养+owner 过滤）
│   │       ├── caloplan_body.py         # upsert_my_body / get_my_body_by_date
│   │       └── caloplan_nutrition.py    # upsert_my_nutrition / get_my_nutrition_by_date
│   ├── schemas/
│   │   ├── auth.py                      # CurrentUser
│   │   └── ai/                          # chat / model / approval / error（含 ContentBlock 多模态块）
│   ├── routes/
│   │   └── infra.py                     # 基础设施路由（Redis ping / AI chat / AI approval）
│   ├── proxy/log_proxy.py               # 日志代理（后续 Repository 层使用）
│   └── utils/logger.py                  # 轮转日志
├── demo/index.html                      # Demo 页面（登录/审批/自动续期）
├── tests/                               # conftest（RSA/JWKS mock）+ auth/infra/tools/caloplan_tools/approval
├── .env.example
├── requirements.txt / requirements-dev.txt
├── pytest.ini
├── Dockerfile / docker-compose.yml      # 无内置 Redis，外部 URL 直连
└── README.md
```

> 无数据库存储：不含 SQLAlchemy / models / repositories。

## Docker 部署

```bash
docker-compose up -d --build
```

- **无内置 Redis**：容器通过 `REDIS_URL` 直连外部实例，请确保网络可达；
- 端口 `9095:9095`；日志卷 `./logs:/app/logs`（无数据库卷）；
- DeepSeek：`AI_PROVIDER=deepseek` + `AI_API_KEY`（官方端点，无需 base_url）；
- healthcheck 探测 `/health`；镜像内 pip 用中科大源（pydantic-ai 安装失败可改用官方 PyPI）。

## 扩展指引（给后续 Agent）

1. **新增 Tool**：`app/ai/tools/` 下新建模块，`@register_tool(name, description, requires_approval)` 装饰 async 函数；
   需要用户确认的写操作加 `requires_approval=True`；读操作免审批；`AI_ENABLED_TOOLS` 控制启用；
2. **写 caloplan 数据 tool**：data 按 caloplan-core 落库契约放业务字段（snake_case，含 `user_id` / `created_time`，`created_time` 存 `yyyy-mm-dd`）；
   查询返回后用 `filter_by_owner(items, user_id)` 过滤当前用户（meta 已按 owner 隔离，此为防御性冗余）；
   新增 data 字段需同步到 meta 类型 schema（管理后台维护），否则字段会被静默丢弃；
3. **新业务路由**：`app/routes/` 下新建并挂载到 `main.py`，写入时从 `CurrentUser` 提取 `user_id` / `service_name` 做归属判定（403）；
4. **Redis 使用**：注入 `get_redis`，Key 统一加 `REDIS_PREFIX` 前缀；
5. **pydantic-ai 2.43 注意**：模型类为 `OpenAIChatModel`（旧 `OpenAIModel` 已移除）；`result.usage` 是属性不是方法；
   `ToolCallPart.args` 是 JSON 字符串（用 `_normalize_arguments`）；图片用 `TextContent`/`ImageUrl`（`TextPart` 仅用于消息历史）。
