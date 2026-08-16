"""System-level operation audit (P1-4.1, aligned with ragent BizChangeLog).

Python equivalent of the ragent audit stack:
- BizChangeLogDO: the OperationAuditLog model (models.py)
- BizChangeLogRecordService.record: record_operation() persists one log row
- BizChangeLogContext: AuditContext (contextvars) carries operator/request data
  and builds before/after snapshots plus a JSON-pointer-style change diff
- BizChangeLogServiceImpl.page: page_audit_logs() paginated + filtered query
- @LogRecord + SpEL: the audit_operation() decorator wraps service functions
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import json
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from frontend.product.models import OperationAuditLog, make_id, utcnow

# Field length caps mirror ragent BizChangeLogRecordService.limit().
_LIMITS = {
    "biz_type": 64,
    "biz_id": 64,
    "operation_type": 32,
    "action_desc": 512,
    "operator_id": 64,
    "operator_name": 128,
    "operator_role": 64,
    "error_message": 300,
    "request_id": 128,
    "ip": 64,
    "user_agent": 512,
}

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@dataclass(frozen=True)
class AuditContext:
    """Request-scoped operator/request identity (analog of UserContext + request)."""

    operator_id: str | None = None
    operator_name: str | None = None
    operator_role: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None


_AUDIT_CONTEXT: contextvars.ContextVar[AuditContext | None] = contextvars.ContextVar(
    "operation_audit_context", default=None
)


def get_audit_context() -> AuditContext | None:
    return _AUDIT_CONTEXT.get()


def bind_audit_context(context: AuditContext | None) -> None:
    """Bind the request-scoped audit identity for the current task/thread."""
    _AUDIT_CONTEXT.set(context)


def _limit(value: str | None, max_length: int) -> str | None:
    if value is None or len(value) <= max_length:
        return value
    return value[:max_length]


def _json_fallback(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _to_jsonable(value: Any) -> Any:
    """Normalize ORM rows / datetimes / containers to JSON-safe plain data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    table = getattr(value, "__table__", None)
    if table is not None:
        return {column.name: _to_jsonable(getattr(value, column.name)) for column in table.columns}
    try:
        return json.loads(json.dumps(value, default=_json_fallback))
    except (TypeError, ValueError):
        return str(value)


def _escape_json_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _collect_diff(
    path: str,
    before: Any,
    after: Any,
    result: list[dict[str, Any]],
) -> None:
    """Path-based diff equivalent of ragent BizChangeLogContext.collectDiff."""
    if before == after:
        return
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            _collect_diff(
                f"{path}/{_escape_json_pointer(str(key))}",
                before.get(key),
                after.get(key),
                result,
            )
        return
    if isinstance(before, list) and isinstance(after, list):
        for index in range(max(len(before), len(after))):
            _collect_diff(
                f"{path}/{index}",
                before[index] if index < len(before) else None,
                after[index] if index < len(after) else None,
                result,
            )
        return
    result.append({"field": path or "/", "before": before, "after": after})


def change_diff(before: Any, after: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    _collect_diff("", before, after, result)
    return result


def record_operation(
    session: Session,
    *,
    biz_type: str,
    biz_id: str,
    operation_type: str,
    action_desc: str,
    before: Any = None,
    after: Any = None,
    success: bool = True,
    error_message: str | None = None,
    operator_id: str | None = None,
    operator_name: str | None = None,
    operator_role: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    request_id: str | None = None,
    commit: bool = True,
) -> OperationAuditLog:
    """Persist one audit row (analog of BizChangeLogRecordService.record)."""
    context = get_audit_context()
    before_json = _to_jsonable(before)
    after_json = _to_jsonable(after)
    record = OperationAuditLog(
        id=make_id("aud"),
        biz_type=_limit(biz_type, _LIMITS["biz_type"]) or "UNKNOWN",
        biz_id=_limit(biz_id or "UNKNOWN", _LIMITS["biz_id"]) or "UNKNOWN",
        operation_type=_limit(operation_type, _LIMITS["operation_type"]) or "",
        action_desc=_limit(action_desc, _LIMITS["action_desc"]) or "",
        before_snapshot=before_json,
        after_snapshot=after_json,
        change_diff=change_diff(before_json, after_json),
        operator_id=_limit(
            operator_id or (context.operator_id if context else None) or "SYSTEM",
            _LIMITS["operator_id"],
        )
        or "SYSTEM",
        operator_name=_limit(
            operator_name or (context.operator_name if context else None),
            _LIMITS["operator_name"],
        ),
        operator_role=_limit(
            operator_role or (context.operator_role if context else None),
            _LIMITS["operator_role"],
        ),
        success=success,
        error_message=_limit(error_message, _LIMITS["error_message"]),
        request_id=_limit(
            request_id or (context.request_id if context else None),
            _LIMITS["request_id"],
        ),
        ip=_limit(ip or (context.ip if context else None), _LIMITS["ip"]),
        user_agent=_limit(
            user_agent or (context.user_agent if context else None),
            _LIMITS["user_agent"],
        ),
        created_at=utcnow(),
    )
    session.add(record)
    if commit:
        session.commit()
    else:
        session.flush()
    return record


def page_audit_logs(
    session: Session,
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    biz_type: str | None = None,
    biz_id: str | None = None,
    operation_type: str | None = None,
    operator_id: str | None = None,
    operator_name: str | None = None,
    success: bool | None = None,
    begin_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict[str, Any]:
    """Paginated + filtered audit query (analog of BizChangeLogServiceImpl.page)."""
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or DEFAULT_PAGE_SIZE), 1), MAX_PAGE_SIZE)
    conditions = []
    if biz_type:
        conditions.append(OperationAuditLog.biz_type == biz_type)
    if biz_id:
        conditions.append(OperationAuditLog.biz_id.like(f"%{biz_id}%"))
    if operation_type:
        conditions.append(OperationAuditLog.operation_type == operation_type)
    if operator_id:
        conditions.append(OperationAuditLog.operator_id == operator_id)
    if operator_name:
        conditions.append(OperationAuditLog.operator_name.like(f"%{operator_name}%"))
    if success is not None:
        conditions.append(OperationAuditLog.success == success)
    if begin_time is not None:
        conditions.append(OperationAuditLog.created_at >= begin_time)
    if end_time is not None:
        conditions.append(OperationAuditLog.created_at <= end_time)

    total = session.scalar(select(func.count(OperationAuditLog.id)).where(*conditions)) or 0
    rows = session.scalars(
        select(OperationAuditLog)
        .where(*conditions)
        .order_by(OperationAuditLog.created_at.desc(), OperationAuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_audit_log_response(record) for record in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _audit_log_response(record: OperationAuditLog) -> dict[str, Any]:
    return {
        "id": record.id,
        "biz_type": record.biz_type,
        "biz_id": record.biz_id,
        "operation_type": record.operation_type,
        "action_desc": record.action_desc,
        "before_snapshot": record.before_snapshot,
        "after_snapshot": record.after_snapshot,
        "change_diff": record.change_diff,
        "operator_id": record.operator_id,
        "operator_name": record.operator_name,
        "operator_role": record.operator_role,
        "success": record.success,
        "error_message": record.error_message,
        "request_id": record.request_id,
        "ip": record.ip,
        "user_agent": record.user_agent,
        "created_at": record.created_at,
    }


class AuditHook:
    """Snapshot hook input passed to before/after callables (analog of SpEL)."""

    def __init__(
        self,
        func: Callable[..., Any],
        args: tuple,
        kwargs: dict,
        result: Any = None,
    ):
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.result = result
        self.arguments = inspect.signature(func).bind_partial(*args, **kwargs).arguments

    def arg(self, name: str, default: Any = None) -> Any:
        return self.arguments.get(name, default)


def audit_operation(
    *,
    biz_type: str,
    operation_type: str,
    action_desc: str | Callable[[AuditHook], str] | None = None,
    biz_id: str | Callable[[AuditHook], str] | None = None,
    before: str | Callable[[AuditHook], Any] | None = None,
    after: str | Callable[[AuditHook], Any] | None = None,
    commit: bool = True,
) -> Callable[..., Any]:
    """Decorator that records an operation audit row around a service function.

    before/after accept either a kwargs key name or a callable receiving an
    AuditHook. ``after=None`` snapshots the function return value.
    """

    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            hook = AuditHook(func, args, kwargs)
            before_value = _resolve_before(before, hook)
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                session = _resolve_session(args, kwargs)
                if session is not None:
                    hook.result = None
                    after_value = _resolve_after(after, hook, result=None)
                    try:
                        record_operation(
                            session,
                            biz_type=biz_type,
                            biz_id=_resolve_biz_id(biz_id, hook, result=None),
                            operation_type=operation_type,
                            action_desc=_resolve_text(action_desc, hook, result=None),
                            before=before_value,
                            after=after_value,
                            success=False,
                            error_message=str(exc),
                            commit=commit,
                        )
                    except Exception:
                        # Audit must never mask the business failure.
                        try:
                            session.rollback()
                        except Exception:
                            pass
                raise
            hook.result = result
            after_value = _resolve_after(after, hook, result=result)
            session = _resolve_session(args, kwargs)
            if session is not None:
                record_operation(
                    session,
                    biz_type=biz_type,
                    biz_id=_resolve_biz_id(biz_id, hook, result=result),
                    operation_type=operation_type,
                    action_desc=_resolve_text(action_desc, hook, result=result),
                    before=before_value,
                    after=after_value,
                    success=True,
                    commit=commit,
                )
            return result

        return wrapper

    return decorate


def _resolve_session(args: tuple, kwargs: dict) -> Session | None:
    for value in kwargs.values():
        if isinstance(value, Session):
            return value
    for value in args:
        if isinstance(value, Session):
            return value
    return None


def _resolve_before(before, hook: AuditHook) -> Any:
    if before is None:
        return None
    if isinstance(before, str):
        return hook.arg(before)
    return before(hook)


def _resolve_after(after, hook: AuditHook, *, result: Any) -> Any:
    if after is None:
        return result
    if isinstance(after, str):
        return hook.arg(after)
    return after(hook)


def _resolve_biz_id(biz_id, hook: AuditHook, *, result: Any) -> str:
    value: Any = None
    if biz_id is None:
        return "UNKNOWN"
    if isinstance(biz_id, str):
        value = hook.arg(biz_id)
        if value is not None:
            value = getattr(value, "id", value)
    else:
        value = biz_id(hook)
    return str(value or "UNKNOWN")


def _resolve_text(text, hook: AuditHook, *, result: Any) -> str:
    if text is None:
        return ""
    if isinstance(text, str):
        return text
    return str(text(hook) or "")
