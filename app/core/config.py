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

    @field_validator("ALLOWED_ORIGINS", "LOG_OPERATIONS", "SUPERUSER_USERNAMES", "SUPERUSER_USER_IDS", mode="before")
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
