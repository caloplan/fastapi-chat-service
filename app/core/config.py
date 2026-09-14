from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用全局配置，从环境变量 / .env 加载。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用
    APP_NAME: str = "Chat Service"
    APP_VERSION: str = "1.0.0"
    APP_ENV: str = "development"
    DEBUG: bool = True

    # 服务
    HOST: str = "0.0.0.0"
    PORT: int = 9095

    # user-service 对接（JWT 消费方）
    # 本服务不签发令牌，只消费 user-service 签发的 RS256 JWT：
    # 1. 读取 JWT Header 的 kid；
    # 2. 本地 JWKS 缓存命中则用对应公钥验证；
    # 3. 未命中或缓存过期则从 USER_SERVICE_URL 强制刷新 JWKS 后再验证（兼容密钥轮换）。
    USER_SERVICE_URL: str = "http://localhost:8000"
    JWKS_CACHE_TTL_SECONDS: int = 3600
    ALGORITHM: str = "RS256"

    # 超级用户白名单（三重 AND 校验：role=superuser + username 匹配 + user_id 匹配，缺一不可）
    # 对齐 mservice-fastapi-metastorage 的权限约定；user_id 改为 user-service 中实际 superuser 的 ID
    SUPERUSER_USERNAMES: List[str] = ["superuser"]
    SUPERUSER_USER_IDS: List[int] = [1]

    # 服务名白名单：非 superuser 的请求必须携带 service_name 且命中本白名单，否则 403
    # （superuser 不受此限制，见 app/core/dependencies.py）
    ALLOWED_SERVICE_NAMES: List[str] = ["default"]

    # Redis（redis.asyncio，用于缓存 / 中间件 / 会话等）
    # 外部 Redis：直接配置提供方给出的连接 URL（云 Redis / 自建实例均可），不在 Docker 内置镜像
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_PREFIX: str = "chat_service"
    REDIS_DECODE_RESPONSES: bool = True

    # PydanticAI（AI Agent 基础框架）
    # AI_PROVIDER：deepseek=DeepSeek 官方端点（api.deepseek.com，无需 AI_BASE_URL）；
    #              openai=任意 OpenAI 兼容接口（base_url 指向自建网关/代理时用）
    # AI_API_KEY 为空时回退 OPENAI_API_KEY（deepseek 模式）或 DEEPSEEK_API_KEY 环境变量
    AI_PROVIDER: str = "deepseek"
    AI_MODEL_NAME: str = "deepseek-chat"
    AI_API_KEY: str = ""
    AI_BASE_URL: str = ""
    AI_SYSTEM_PROMPT: str = "你是 Chat Service 的智能助手，请用简洁中文回答用户问题。"
    AI_REQUEST_TIMEOUT_SECONDS: float = 60.0

    # AI Tool 数据服务对接（caloplan 数据域）
    # Agent 的 tool 直接 HTTP 调用 meta-service（mservice-fastapi-metastorage）读写 food/meal/body/nutrition，
    # 复用当前请求 JWT 透传（与 user-service 同一套 RS256 令牌）。Docker 同网络内用服务名 meta-service。
    META_SERVICE_URL: str = "http://localhost:9093"

    # Tool 配置基础设施：AI_ENABLED_TOOLS 控制注入 Agent 的 tool 名单
    # - ["*"] 启用全部已注册 tool（默认）；["name_a", "name_b"] 仅启用名单内；[] 全部禁用
    AI_ENABLED_TOOLS: List[str] = ["*"]
    # 需审批 Tool 的 taskid 暂存 TTL（秒）：审批请求在期限内有效，超时自动作废
    AI_APPROVAL_TTL_SECONDS: int = 300

    # 日志
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/chat_service.log"
    LOG_MAX_BYTES: int = 10 * 1024 * 1024
    LOG_BACKUP_COUNT: int = 5

    # CORS
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:9095"]

    # 日志操作类型（LogProxy 拦截的方法名白名单，预留聊天领域操作）
    LOG_OPERATIONS: List[str] = [
        "conversation_create",
        "conversation_update",
        "conversation_delete",
        "message_send",
        "message_update",
        "message_delete",
        "message_read",
    ]

    @field_validator(
        "ALLOWED_ORIGINS",
        "LOG_OPERATIONS",
        "SUPERUSER_USERNAMES",
        "SUPERUSER_USER_IDS",
        "ALLOWED_SERVICE_NAMES",
        "AI_ENABLED_TOOLS",
        mode="before",
    )
    @classmethod
    def _parse_list(cls, v):
        """支持从 .env 读取 JSON 数组字符串。"""
        if isinstance(v, str):
            import json

            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [item.strip() for item in v.split(",") if item.strip()]
        return v


settings = Settings()
