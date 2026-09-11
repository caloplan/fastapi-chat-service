# Chat Service - 聊天微服务（基础框架）

基于 FastAPI 的聊天微服务**基础框架**，认证部分对齐 `mservice-fastapi-user`（user-service）的认证协议。**无数据库存储**，当前仅预留 `health` 等基础端点；认证能力（JWT 校验依赖）已就绪，后续聊天接口直接注入即可。

本服务**不签发令牌、不存储用户表**，作为 user-service 的**兄弟微服务**，通过 JWKS 消费其签发的 RS256 JWT 完成认证与权限判定。

## 技术栈

- **FastAPI** - 异步 Web 框架
- **Pydantic v2 + pydantic-settings** - 数据验证 + 全局配置（`.env`）
- **python-jose + cryptography** - JWT 校验（消费 user-service 令牌，RS256）
- **httpx** - 拉取 user-service JWKS（带 TTL 缓存）
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
- `is_superuser` / `require_superuser`：**三重 AND 校验**——`role == "superuser"` + username 在 `SUPERUSER_USERNAMES` 白名单 + user_id 在 `SUPERUSER_USER_IDS` 白名单，缺一不可，管理接口返回 403。

## 项目结构

```
fastapi-chat-service/
├── app/
│   ├── main.py                          # 应用入口（lifespan 仅初始化日志；health 等基础端点）
│   ├── core/
│   │   ├── config.py                    # 配置管理（user-service 对接 / JWKS 缓存 / superuser 白名单）
│   │   ├── security.py                  # JWKS 获取/缓存 + JWT 校验（消费 user-service 令牌）
│   │   └── dependencies.py              # 认证依赖（get_current_user / require_superuser）
│   ├── schemas/
│   │   └── auth.py                      # CurrentUser（JWT payload 解析）
│   ├── proxy/
│   │   └── log_proxy.py                 # Log Proxy 日志代理（复用参照项目实现，后续 Repository 层使用）
│   └── utils/
│       └── logger.py                    # 轮转日志（复用参照项目实现）
├── tests/
│   ├── conftest.py                      # 测试 RSA 密钥 + JWKS mock + JWT 签发
│   └── test_auth.py                     # 认证层单元测试 + health 端点测试
├── .env.example
├── requirements.txt / requirements-dev.txt
├── pytest.ini
├── Dockerfile / docker-compose.yml
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

## 与 user-service 对接

- 本地联调：user-service 运行在 `http://localhost:8000`（默认），本服务自动从 `/.well-known/jwks.json` 拉取公钥；
- Docker 部署：`docker-compose.yml` 中 `USER_SERVICE_URL=http://user-service:8000`（同网络服务名）；
- **密钥轮换兼容**：JWKS 缓存 TTL 内直接验证；`kid` 未命中时强制刷新一次，无需其他协调；
- **superuser 白名单**：部署时把 `SUPERUSER_USER_IDS` 改为 user-service 中实际 superuser 的 `user_id`（可在 user-service 的 `/api/v1/users` 列表查询）。

## 运行测试

```bash
pytest tests -v
```

测试覆盖：health / 服务信息端点；`get_current_user` 有效令牌解析、无 token / 伪造 / 过期 / refresh token / 未知 kid / 缺 user_id → 401；superuser 三重 AND 权限判定。

## Docker 部署

```bash
docker-compose up -d --build
```

- 端口 `9095:9095`；仅日志卷 `./logs:/app/logs`（无数据库卷）；
- healthcheck 探测 `/health`；镜像内 pip 使用中科大源（对齐参照项目）。

## 后续开发指引（在此框架上扩展聊天业务）

1. **认证**：受保护接口直接注入 `Depends(get_current_user)` 拿当前用户（`CurrentUser`），管理接口注入 `Depends(require_superuser)`；
2. **业务路由**：在 `app/core` 之外新建路由模块（如 `app/routes/chat.py`）并挂载到 `main.py`，写入时从 `CurrentUser` 提取 `user_id` / `service_name` 做归属与越权判定（403）；
3. **持久化（按需引入）**：若后续需要存储会话/消息，再引入 SQLAlchemy 异步 + SQLite（参照 `mservice-fastapi-user` 的 `core/database.py` 与分层结构）；
4. **操作日志**：Repository 层用 `LogProxy(Repository(db))` 包裹自动脱敏记录。
