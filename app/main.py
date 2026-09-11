from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.utils.logger import setup_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：无数据库依赖，仅初始化日志。"""
    logger = setup_logger()
    logger.info("正在启动 %s v%s (env=%s)", settings.APP_NAME, settings.APP_VERSION, settings.APP_ENV)
    logger.info("user-service 对接地址: %s", settings.USER_SERVICE_URL)
    try:
        yield
    finally:
        logger.info("应用已关闭")


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="基于 FastAPI 的聊天微服务基础框架，认证对齐 mservice-fastapi-user："
        "消费其 RS256 JWT（JWKS 多 kid 校验）。当前无数据库存储，仅预留 health 等基础端点。",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 健康检查
    @app.get("/health", tags=["系统"], summary="健康检查")
    async def health_check() -> dict[str, str]:
        return {"status": "healthy", "service": settings.APP_NAME, "version": settings.APP_VERSION}

    @app.get("/", tags=["系统"], summary="服务信息")
    async def root() -> dict[str, str]:
        return {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "docs": "/docs",
            "health": "/health",
            "user_service": settings.USER_SERVICE_URL,
        }

    return app


app = create_app()
