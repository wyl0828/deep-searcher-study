from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Request, Response
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from frontend.product.db import get_session
from frontend.product.errors import ProductError
from frontend.product.models import (
    LEGACY_OWNER_ID,
    Conversation,
    KnowledgeBase,
    User,
    UserSession,
    utcnow,
)

SESSION_COOKIE_NAME = "deepsearcher_session"
USERNAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{2,31}")
PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 128
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_FAKE_PASSWORD_HASH = (
    "scrypt$16384$8$1$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)


def _session_ttl() -> timedelta:
    try:
        days = int(os.environ.get("DEEPSEARCHER_SESSION_DAYS", "7"))
    except ValueError:
        days = 7
    return timedelta(days=min(max(days, 1), 30))


def _secure_cookie() -> bool:
    return os.environ.get("DEEPSEARCHER_SECURE_COOKIES", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def normalize_username(value: str) -> str:
    username = str(value or "").strip().casefold()
    if USERNAME_PATTERN.fullmatch(username) is None:
        raise ProductError(
            "USERNAME_INVALID",
            "用户名需为 3–32 位小写字母、数字、点、下划线或短横线。",
        )
    return username


def validate_password(value: str) -> str:
    password = str(value or "")
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise ProductError(
            "PASSWORD_INVALID",
            f"密码长度需为 {PASSWORD_MIN_LENGTH}–{PASSWORD_MAX_LENGTH} 个字符。",
        )
    return password


def hash_password(password: str) -> str:
    password = validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )
    import base64

    return "$".join(
        (
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    import base64

    try:
        algorithm, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        expected = base64.b64decode(raw_digest, validate=True)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(raw_salt, validate=True),
            n=int(raw_n),
            r=int(raw_r),
            p=int(raw_p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def ensure_legacy_owner(session: Session) -> User:
    user = session.get(User, LEGACY_OWNER_ID)
    if user is None:
        user = User(
            id=LEGACY_OWNER_ID,
            username="__legacy__",
            display_name="待接管的旧数据",
            password_hash=_FAKE_PASSWORD_HASH,
            role="system_pending",
            is_active=False,
        )
        session.add(user)
        session.flush()
    return user


def setup_required(session: Session) -> bool:
    legacy_owner = ensure_legacy_owner(session)
    count = session.scalar(select(func.count()).select_from(User).where(User.id != LEGACY_OWNER_ID))
    return not bool(count) and legacy_owner.role == "system_pending"


def acquire_setup_lock(session: Session) -> None:
    result = session.execute(
        update(User)
        .where(
            User.id == LEGACY_OWNER_ID,
            User.role == "system_pending",
        )
        .values(role="system_claimed")
    )
    if result.rowcount != 1:
        raise ProductError(
            "SETUP_ALREADY_COMPLETED",
            "工作台已经完成初始化，请直接登录。",
            status_code=409,
        )


def create_user(
    session: Session,
    *,
    username: str,
    password: str,
    display_name: str,
    role: str = "member",
) -> User:
    normalized = normalize_username(username)
    display = str(display_name or "").strip()
    if not 1 <= len(display) <= 50:
        raise ProductError("DISPLAY_NAME_INVALID", "显示名称需为 1–50 个字符。")
    if role not in {"admin", "member"}:
        raise ProductError("ROLE_INVALID", "用户角色无效。")
    if session.scalar(select(User.id).where(User.username == normalized)) is not None:
        raise ProductError("USERNAME_EXISTS", "这个用户名已经存在。", status_code=409)
    user = User(
        username=normalized,
        display_name=display,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def authenticate(session: Session, username: str, password: str) -> User | None:
    normalized = str(username or "").strip().casefold()
    user = session.scalar(select(User).where(User.username == normalized))
    encoded = user.password_hash if user is not None else _FAKE_PASSWORD_HASH
    valid = verify_password(str(password or ""), encoded)
    return user if valid and user is not None and user.is_active else None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_login_session(session: Session, user: User, response: Response) -> None:
    token = secrets.token_urlsafe(32)
    ttl = _session_ttl()
    session.execute(delete(UserSession).where(UserSession.expires_at <= utcnow()))
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=_token_hash(token),
            expires_at=utcnow() + ttl,
        )
    )
    session.commit()
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=_secure_cookie(),
        samesite="lax",
        path="/",
    )


def delete_login_session(session: Session, request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        session.execute(delete(UserSession).where(UserSession.token_hash == _token_hash(token)))
        session.commit()
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", secure=_secure_cookie(), samesite="lax")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def optional_user(request: Request, session: Session = Depends(get_session)) -> User | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    auth_session = session.scalar(
        select(UserSession)
        .where(UserSession.token_hash == _token_hash(token))
        .join(UserSession.user)
    )
    if auth_session is None or _aware(auth_session.expires_at) <= utcnow():
        if auth_session is not None:
            session.delete(auth_session)
            session.commit()
        return None
    user = auth_session.user
    if not user.is_active:
        return None
    now = utcnow()
    if _aware(auth_session.last_seen_at) <= now - timedelta(minutes=5):
        auth_session.last_seen_at = now
        session.commit()
    request.state.product_user = user
    return user


def require_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise ProductError("AUTH_REQUIRED", "请先登录。", status_code=401)
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise ProductError("ADMIN_REQUIRED", "需要管理员权限。", status_code=403)
    return user


def claim_legacy_data(session: Session, owner_id: str) -> None:
    session.execute(
        update(KnowledgeBase)
        .where(KnowledgeBase.owner_id == LEGACY_OWNER_ID)
        .values(owner_id=owner_id)
    )
    session.execute(
        update(Conversation)
        .where(Conversation.owner_id == LEGACY_OWNER_ID)
        .values(owner_id=owner_id)
    )


def user_response(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at,
    }
