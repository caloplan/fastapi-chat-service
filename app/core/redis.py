"""Redis 接入：连接生命周期 + FastAPI 依赖。

- lifespan 启动时调用 init_redis()，关闭时调用 close_redis()；
- 业务层通过 get_redis 依赖获取 redis.asyncio.Redis 客户端；
- 启动时 Redis 不可用仅告警不阻断启动，依赖方首次使用时自动重连。

用法::

    from app.core.redis import get_redis

    @router.get("/demo")
    async def demo(redis: Annotated[Redis, Depends(get_redis)]):
        await redis.set("k", "v")
        return await redis.get("k")
"""

import redis.asyncio as aioredis

from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger("redis")

_redis_client: aioredis.Redis | None = None


async def init_redis() -> aioredis.Redis:
    """初始化 Redis 客户端并做连通性探测（失败仅告警，不阻断启动）。"""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=settings.REDIS_DECODE_RESPONSES,
        )
    try:
        await _redis_client.ping()
    except Exception as exc:
        logger.warning("Redis 连通性探测失败（服务继续启动，依赖方使用时将重试）: %s", exc)
    else:
        logger.info("Redis 连接成功: %s", settings.REDIS_URL)
    return _redis_client


async def close_redis() -> None:
    """关闭 Redis 连接池。"""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis 连接已关闭")


async def get_redis() -> aioredis.Redis:
    """FastAPI 依赖：返回 Redis 客户端（未初始化时惰性初始化）。"""
    if _redis_client is None:
        await init_redis()
    return _redis_client


__all__ = ["init_redis", "close_redis", "get_redis"]
