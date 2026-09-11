import functools
import inspect
import json
import time
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.config import settings
from app.utils.logger import get_logger

logger = get_logger("log_proxy")

# 需要脱敏的敏感字段名（不区分大小写）
SENSITIVE_FIELDS = {
    "password",
    "hashed_password",
    "old_password",
    "new_password",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "secret_key",
}

# 邮箱等部分脱敏字段
PARTIAL_MASK_FIELDS = {"email", "phone"}


def _mask_value(key: str, value: Any) -> Any:
    """对敏感字段值进行脱敏。"""
    if key.lower() in SENSITIVE_FIELDS:
        return "******"
    if key.lower() in PARTIAL_MASK_FIELDS and isinstance(value, str):
        if "@" in value:
            local, _, domain = value.partition("@")
            if len(local) > 2:
                return f"{local[0]}***{local[-1]}@{domain}"
            return f"***@{domain}"
        if len(value) > 4:
            return f"{value[:3]}***{value[-2:]}"
        return "***"
    return value


def _sanitize(obj: Any) -> Any:
    """递归脱敏任意结构中的敏感字段。"""
    if isinstance(obj, dict):
        return {k: _mask_value(k, _sanitize(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(item) for item in obj]
    # pydantic / ORM 对象尝试转 dict
    if hasattr(obj, "model_dump"):
        try:
            return _sanitize(obj.model_dump())
        except Exception:
            pass
    if hasattr(obj, "__dict__") and not isinstance(obj, type):
        try:
            return _sanitize({k: v for k, v in vars(obj).items() if not k.startswith("_")})
        except Exception:
            pass
    return obj


def _to_serializable(obj: Any) -> Any:
    """将对象转为可 JSON 序列化的形式。"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "value"):  # Enum
        try:
            return obj.value
        except Exception:
            pass
    return obj


def _json_dumps(data: Any) -> str:
    """安全 JSON 序列化。"""
    try:
        return json.dumps(data, default=_to_serializable, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(data)


class LogProxy:
    """代理类：拦截目标对象的方法调用，自动记录操作日志。

    用法::

        repo = LogProxy(UserRepository(db))
        await repo.create(...)
    """

    def __init__(self, target: Any, operations: list[str] | None = None) -> None:
        self._target = target
        self._operations = set(operations or settings.LOG_OPERATIONS)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            return getattr(self._target, name)
        attr = getattr(self._target, name)
        if callable(attr):
            return self._wrap(attr, name)
        return attr

    def _wrap(self, func: Callable, method_name: str) -> Callable:
        """包装同步/异步方法。"""
        is_coroutine = inspect.iscoroutinefunction(func)

        if is_coroutine:

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                return await self._execute(func, method_name, args, kwargs)

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return self._execute_sync(func, method_name, args, kwargs)

        return sync_wrapper

    async def _execute(self, func: Callable, method_name: str, args: tuple, kwargs: dict) -> Any:
        start = time.perf_counter()
        timestamp = datetime.now(timezone.utc).isoformat()
        status = "success"
        result: Any = None
        error: str | None = None
        try:
            result = await func(*args, **kwargs)
            return result
        except Exception as exc:
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            self._emit(method_name, args, kwargs, result, status, elapsed_ms, timestamp, error)

    def _execute_sync(self, func: Callable, method_name: str, args: tuple, kwargs: dict) -> Any:
        start = time.perf_counter()
        timestamp = datetime.now(timezone.utc).isoformat()
        status = "success"
        result: Any = None
        error: str | None = None
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as exc:
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            self._emit(method_name, args, kwargs, result, status, elapsed_ms, timestamp, error)

    def _emit(
        self,
        method_name: str,
        args: tuple,
        kwargs: dict,
        result: Any,
        status: str,
        elapsed_ms: float,
        timestamp: str,
        error: str | None,
    ) -> None:
        """输出结构化日志。"""
        target_cls = type(self._target).__name__
        log_entry = {
            "operation": f"{target_cls}.{method_name}",
            "method": method_name,
            "args": _json_dumps(_sanitize(args)),
            "kwargs": _json_dumps(_sanitize(kwargs)),
            "elapsed_ms": elapsed_ms,
            "timestamp": timestamp,
            "status": status,
        }
        if status == "success":
            log_entry["result"] = _json_dumps(_sanitize(result))
            logger.info(_json_dumps(log_entry))
        else:
            log_entry["error"] = error
            logger.error(_json_dumps(log_entry))
