"""Meta 微服务 HTTP 客户端（mservice-fastapi-metastorage）。

供 caloplan tools 调用，读写 food / meal / body / nutrition 类型：
- 复用当前请求 JWT（Authorization: Bearer <token>），身份与数据隔离由 meta 侧 Scope 保证；
- 错误映射为可读消息（401/403/404/409/422）；
- get_entry 对 404 返回 None，其余方法抛 MetaApiError。

数据格式约定（对齐 caloplan-core / caloplan-user 落库约定）：
- meta entry.data 使用 snake_case（user_id / created_time 等）；
- user_id 由服务端从当前请求注入（str 形式），不暴露给模型；
- entity_key 由服务端生成（uuid4 短码），不暴露给模型。
"""

from typing import Any

import httpx

from app.ai.context import require_ai_context
from app.core.config import settings


class MetaApiError(Exception):
    """Meta 服务调用失败（已映射为可读消息）。"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _extract_detail(payload: dict[str, Any]) -> str:
    """从 FastAPI 错误响应中提取可读 detail（兼容 str / 校验错误列表）。"""
    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                loc = ".".join(str(x) for x in item.get("loc", []))
                msg = item.get("msg", "")
                parts.append(f"{loc}: {msg}" if loc else msg)
            else:
                parts.append(str(item))
        return "; ".join(parts) if parts else "请求校验失败"
    return str(detail) if detail else "请求失败"


class MetaClient:
    """meta-service 客户端（单实例复用 httpx 连接池）。"""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> Any:
        """统一请求入口：注入 Authorization、映射错误。

        allow_404=True 时 404 返回 None（用于 get 单实体）；其余状态码一律抛 MetaApiError。
        """
        ctx = require_ai_context()
        if not ctx.token:
            raise MetaApiError("当前请求缺少访问令牌，无法调用 meta-service")
        headers = {"Authorization": f"Bearer {ctx.token}"}
        try:
            resp = await self._http.request(
                method,
                path,
                headers=headers,
                json=json_body,
                params=params,
            )
        except httpx.HTTPError as exc:
            raise MetaApiError(f"meta-service 连接失败: {exc}") from exc

        if resp.status_code == 404 and allow_404:
            return None

        if resp.status_code >= 400:
            try:
                payload = resp.json()
            except Exception:
                payload = {}
            detail = _extract_detail(payload) if isinstance(payload, dict) else str(payload)
            message = {
                401: f"meta-service 认证失败: {detail}",
                403: f"meta-service 无权限: {detail}",
                404: f"meta-service 资源不存在: {detail}",
                409: f"meta-service 冲突: {detail}",
                422: f"meta-service 校验失败: {detail}",
            }.get(resp.status_code, f"meta-service 错误({resp.status_code}): {detail}")
            raise MetaApiError(message, status_code=resp.status_code)

        if resp.status_code == 204:
            return None
        try:
            return resp.json()
        except Exception as exc:
            raise MetaApiError(f"meta-service 响应解析失败: {exc}") from exc

    # ── 实体 CRUD（/api/v1/entries）──────────────────────────

    async def create_entry(
        self,
        type_name: str,
        entity_key: str,
        data: dict[str, Any],
        *,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """创建实体元数据（201 返回完整 entry）。"""
        body: dict[str, Any] = {"type_name": type_name, "entity_key": entity_key, "data": data}
        if tags:
            body["tags"] = tags
        return await self._request("POST", "/api/v1/entries", json_body=body)

    async def get_entry(self, type_name: str, entity_key: str) -> dict[str, Any] | None:
        """获取实体（404 返回 None）。"""
        return await self._request(
            "GET",
            f"/api/v1/entries/{type_name}/{entity_key}",
            allow_404=True,
        )

    async def update_entry(
        self,
        type_name: str,
        entity_key: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """部分更新实体（data deep merge，版本自增）。"""
        return await self._request(
            "PUT",
            f"/api/v1/entries/{type_name}/{entity_key}",
            json_body={"data": data},
        )

    async def query_entries(
        self,
        type_name: str,
        *,
        filters: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        page: int = 1,
        page_size: int = 100,
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        """复杂查询：字段精确过滤（filters）+ tags 交集 + 分页，返回 {total, items}。"""
        params: dict[str, Any] = {"type_name": type_name, "page": page, "page_size": page_size}
        if tags:
            params["tags"] = ",".join(tags)
        params.update(filters or {})
        # sort_order 非默认时才传，保持服务端默认
        if sort_order != "desc":
            params["sort_order"] = sort_order
        return await self._request("GET", "/api/v1/entries", params=params)

    async def batch_get_entries(
        self,
        type_name: str,
        keys: list[str],
    ) -> dict[str, Any]:
        """批量查询：{key: entry | None}，未找到的 key 为 null。"""
        return await self._request(
            "POST",
            "/api/v1/entries/batch",
            json_body={"type_name": type_name, "keys": keys},
        )


_client: MetaClient | None = None


def get_meta_client() -> MetaClient:
    """进程级单例 meta 客户端（lazy 创建，连接池复用）。"""
    global _client
    if _client is None:
        _client = MetaClient(settings.META_SERVICE_URL)
    return _client


__all__ = ["MetaApiError", "MetaClient", "get_meta_client"]
