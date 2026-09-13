# Chat Service - 聊天微服务（基础框架）

基于 FastAPI 的聊天微服务**基础框架**，认证部分对齐 `mservice-fastapi-user`（user-service）的认证协议。**无数据库存储**，当前已接入 **Redis**（外部实例，URL 直连）与 **PydanticAI**（AI Agent 基础框架，默认 DeepSeek 模型 + Tool 注册基础设施），并预留 `health` 等基础端点；认证能力（JWT 校验依赖）已就绪，后续聊天接口直接注入即可。

本服务**不签发令牌、不存储用户表**，作为 user-service 的**兄弟微服务**，通过 JWKS 消费其签发的 RS256 JWT 完成认证与权限判定。

## 技术栈

- **FastAPI** - 异步 Web 框架
- **Pydantic v2 + pydantic-settings** - 数据验证 + 全局配置（`.env`）
- **python-jose + cryptography** - JWT 校验（消费 user-service 令牌，RS256）
- **httpx** - 拉取 user-service JWKS（带 TTL 缓存）
- **Redis（redis.asyncio）** - 缓存 / 中间件 / 会话存储，**外部实例 URL 直连**，生命周期内自动连接与释放
- **PydanticAI** - AI Agent 基础框架，默认接入 **DeepSeek**（官方端点），支持 OpenAI 兼容网关；内置 **Tool 注册/配置基础设施**
- **RotatingFileHandler 轮转日志 + Log Proxy** - 日志与操作脱敏（复用参照项目实现）
- **pytest + pytest-asyncio + httpx** - 测试

## 认证机制（对齐 mservice-fastapi-user）

user-service 签发 JWT 的协议：

- **RS256 非对称签名**，JWT Header 携带 `kid`（RFC 7638 thumbprint），payload 声明：`sub`（用户名）/ `user_id` / `service_name` / `role` / `type`（access / refresh）；
- **密钥自动轮换**：user-service 后台定时轮换签名密钥，`/.well-known/jwks.json` 同时暴露当前与保留期内的旧公钥（多 kid）；
- **超级用户**：user-service 部署时按 `.env` 自动创建的 superuser，登录后 token 携带 `role=superuser`。

本服务的校验流程（`app/core/security.py`）：

1. 读取 JWT Header 的 `kid`；
2. 本地 JWKS 缓存命中 → 用对应公钥验证；
3. 未命中或缓存过期 → 从 `USER_SERVICE_URL` 强制刷新 JWKS 后再验证（兼容轮换）；
4. `type != access`、过期、伪造、未知 kid → 401。

权限判定（`app/core/dependencies.py`）：

- `get_current_user`：解析 JWT → `CurrentUser`（sub / user_id / service_name / role / type），不落库；
- **服务名白名单**：非 superuser 的请求必须携带 `service_name` 且命中 `ALLOWED_SERVICE_NAMES` 白名单，否则 403（superuser 三重 AND 判定通过后不受限）；
- `is_superuser` / `require_superuser`：**三重 AND 校验**——`role == "superuser"` + username 在 `SUPERUSER_USERNAMES` 白名单 + user_id 在 `SUPERUSER_USER_IDS` 白名单，缺一不可，管理接口返回 403。

## Redis 接入

- `app/core/redis.py`：连接生命周期（`init_redis` / `close_redis`）+ FastAPI 依赖（`get_redis`）；
- **外部实例接入**：不内置 Redis 镜像，直接配置 `REDIS_URL`（云 Redis / 自建实例的连接 URL 均可），Docker 部署同样走 URL 直连；
- lifespan 启动时自动连接，关闭时释放；启动时 Redis 不可用仅告警不阻断，依赖方首次使用时自动重连；
- 业务用法：路由注入 `redis: Annotated[Redis, Depends(get_redis)]` 即可使用 `redis.asyncio` API；
- 配置：`REDIS_URL` / `REDIS_PREFIX`（Key 统一前缀）/ `REDIS_DECODE_RESPONSES`。

## PydanticAI 接入

- `app/ai/agent.py`：`build_agent()` 依据配置构建 Agent，`get_ai_agent()` 为进程级缓存依赖；
- **DeepSeek（默认）**：`AI_PROVIDER=deepseek` 走 DeepSeek 官方端点（api.deepseek.com），模型 `AI_MODEL_NAME=deepseek-chat`（可选 deepseek-reasoner）；
- **OpenAI 兼容**：`AI_PROVIDER=openai` 时 `AI_BASE_URL` 指向自建网关/代理；
- API Key：`AI_API_KEY` 优先，为空时回退 `DEEPSEEK_API_KEY`（deepseek 模式）或 `OPENAI_API_KEY`（openai 模式）；均缺失时 AI 端点返回 503（明确报错，不静默降级）；
- 业务用法：路由注入 `agent: Annotated[Agent, Depends(get_ai_agent)]`，`await agent.run(prompt)`。

## Tool 配置基础设施

- `app/ai/tools/`：注册 → 发现 → 按配置过滤 → 注入 Agent 的完整链路；
- **新增 Tool**：在 `app/ai/tools/` 新建模块，用 `@register_tool(name=..., description=...)` 装饰 async 函数即可，`build_tools()` 自动发现；
- **配置开关**：`AI_ENABLED_TOOLS=["*"]` 启用全部（默认）；`["name_a","name_b"]` 仅启用名单内；`[]` 全部禁用；
- 内置演示 Tool：`get_current_time`（当前时间）、`add_numbers`（整数加法）。

## 项目结构

```
fastapi-chat-service/
├── app/
│   ├── main.py                          # 应用入口（lifespan 初始化日志+Redis；挂载路由）
│   ├── core/
│   │   ├── config.py                    # 配置管理（user-service 对接 / JWKS 缓存 / superuser 白名单 / Redis / AI）
│   │   ├── security.py                  # JWKS 获取/缓存 + JWT 校验（消费 user-service 令牌）
│   │   ├── dependencies.py              # 认证依赖（get_current_user / require_superuser / 服务名白名单）
│   │   └── redis.py                     # Redis 连接生命周期 + get_redis 依赖
│   ├── ai/
│   │   ├── agent.py                     # PydanticAI Agent 构建 + get_ai_agent 依赖
│   │   └── tools/                       # Tool 注册/配置基础设施
│   │       ├── __init__.py              # register_tool / build_tools / AI_ENABLED_TOOLS 过滤
│   │       └── basic.py                 # 演示 Tool（get_current_time / add_numbers）
│   ├── schemas/
│   │   ├── auth.py                      # CurrentUser（JWT payload 解析）
│   │   └── ai.py                        # AI 演示请求/响应 Schema
│   ├── routes/
│   │   └── infra.py                     # 基础设施演示路由（Redis ping / AI chat，需认证）
│   ├── proxy/
│   │   └── log_proxy.py                 # Log Proxy 日志代理（复用参照项目实现，后续 Repository 层使用）
│   └── utils/
│       └── logger.py                    # 轮转日志（复用参照项目实现）
├── tests/
│   ├── conftest.py                      # 测试 RSA 密钥 + JWKS mock + JWT 签发
│   ├── test_auth.py                     # 认证层单元测试（含服务名白名单）+ health 端点测试
│   ├── test_infra.py                    # Redis / PydanticAI 演示端点测试
│   └── test_tools.py                    # Tool 注册/配置过滤测试
├── .env.example
├── requirements.txt / requirements-dev.txt
├── pytest.ini
├── Dockerfile / docker-compose.yml（无内置 Redis，外部 URL 直连）
└── README.md
```

> 无数据库存储：不含 SQLAlchemy / aiosqlite / alembic / models / repositories。

## 快速开始

### 1. 创建虚拟环境并安装依赖

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements-dev.txt
```

### 2. 配置环境变量

```bash
copy .env.example .env
# 编辑 .env：USER_SERVICE_URL 指向 user-service 地址；SUPERUSER_USER_IDS 改为实际 superuser ID
```

### 3. 启动服务

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 9095
```

### 4. 访问

- 健康检查: http://localhost:9095/health
- 服务信息: http://localhost:9095/
- Swagger UI: http://localhost:9095/docs

## API 端点

| 方法 | 端点 | 功能 | 认证 |
|------|------|------|------|
| GET | `/health` | 健康检查 | 否 |
| GET | `/` | 服务信息 | 否 |
| GET | `/api/v1/redis/ping` | Redis 连通性检查（基础框架演示） | 是（Bearer JWT） |
| POST | `/api/v1/ai/chat` | PydanticAI Agent 演示（body: `{"message": "..."}`） | 是（Bearer JWT） |

> 受保护端点均要求 Bearer Token：user-service 签发的 access token，且非 superuser 的 `service_name` 必须命中 `ALLOWED_SERVICE_NAMES`。

## 与 user-service 对接

- 本地联调：user-service 运行在 `http://localhost:8000`（默认），本服务自动从 `/.well-known/jwks.json` 拉取公钥；
- Docker 部署：`docker-compose.yml` 中 `USER_SERVICE_URL=http://user-service:8000`（同网络服务名）；
- **密钥轮换兼容**：JWKS 缓存 TTL 内直接验证；`kid` 未命中时强制刷新一次，无需其他协调；
- **superuser 白名单**：部署时把 `SUPERUSER_USER_IDS` 改为 user-service 中实际 superuser 的 `user_id`（可在 user-service 的 `/api/v1/users` 列表查询）；
- **服务名白名单**：部署时把 `ALLOWED_SERVICE_NAMES` 配成允许访问本服务的所有服务名（逗号分隔或 JSON 数组），未命中即 403（superuser 除外）。

## 运行测试

```bash
pytest tests -v
```

测试覆盖：health / 服务信息端点；`get_current_user` 有效令牌解析、无 token / 伪造 / 过期 / refresh token / 未知 kid / 缺 user_id → 401；服务名白名单（非 superuser 未命中 → 403，superuser 豁免）；superuser 三重 AND 权限判定；Redis / PydanticAI 演示端点（依赖注入 mock）；Tool 注册与 `AI_ENABLED_TOOLS` 配置过滤。

## Docker 部署

```bash
docker-compose up -d --build
```

- **无内置 Redis**：Redis 为外部接入，容器通过 `REDIS_URL` 直连外部实例（云 Redis / 自建均可），请确保网络可达；
- 端口 `9095:9095`；日志卷 `./logs:/app/logs`（无数据库卷）；
- DeepSeek：`AI_PROVIDER=deepseek` + `AI_API_KEY`（官方端点，无需 base_url）；
- healthcheck 探测 `/health`；镜像内 pip 使用中科大源（对齐参照项目，pydantic-ai 安装失败时可改用官方 PyPI 源）。

## 后续开发指引（在此框架上扩展聊天业务）

1. **认证**：受保护接口直接注入 `Depends(get_current_user)` 拿当前用户（`CurrentUser`），管理接口注入 `Depends(require_superuser)`；服务名白名单已在 `get_current_user` 内统一校验；
2. **业务路由**：在 `app/routes/` 下新建路由模块并挂载到 `main.py`，写入时从 `CurrentUser` 提取 `user_id` / `service_name` 做归属与越权判定（403）；
3. **Redis 使用**：注入 `get_redis` 依赖做缓存 / 限流 / 会话，Key 统一加 `REDIS_PREFIX` 前缀；
4. **AI 能力**：注入 `get_ai_agent` 依赖做对话；需要模型工具时在 `app/ai/tools/` 下新增模块并用 `@register_tool` 注册，`AI_ENABLED_TOOLS` 控制启用名单；
5. **持久化（按需引入）**：若后续需要存储会话/消息，再引入 SQLAlchemy 异步 + SQLite（参照 `mservice-fastapi-user` 的 `core/database.py` 与分层结构）；
6. **操作日志**：Repository 层用 `LogProxy(Repository(db))` 包裹自动脱敏记录。
