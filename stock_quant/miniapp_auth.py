from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .auth import _connect, authenticate, auth_enabled, refresh_user
from .settings import DATA_DIR


ACCESS_PURPOSE = "miniapp_access"
BIND_PURPOSE = "miniapp_bind"


class MiniappAuthError(RuntimeError):
    pass


class MiniappTokenError(MiniappAuthError):
    pass


class MiniappConfigurationError(MiniappAuthError):
    pass


class MiniappEntitlementError(MiniappAuthError):
    pass


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def local_dev_enabled() -> bool:
    environment = os.getenv("APP_ENV", "local").strip().lower()
    return environment in {"local", "dev", "development", "test"} and _truthy(
        "MINIAPP_ALLOW_LOCAL_DEV",
        "true",
    )


def _token_secret() -> bytes:
    configured = os.getenv("MINIAPP_TOKEN_SECRET", "").strip()
    if configured:
        if len(configured) < 32:
            raise MiniappConfigurationError("MINIAPP_TOKEN_SECRET 至少需要32个字符")
        return configured.encode("utf-8")
    if local_dev_enabled():
        return b"stock-quant-miniapp-local-development-only"
    raise MiniappConfigurationError("正式环境必须设置 MINIAPP_TOKEN_SECRET")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:
        raise MiniappTokenError("登录状态格式无效") from exc


def issue_token(payload: dict[str, Any], purpose: str, ttl_seconds: int) -> str:
    now = int(time.time())
    claims = {
        **payload,
        "purpose": purpose,
        "iat": now,
        "exp": now + max(60, int(ttl_seconds)),
        "nonce": secrets.token_hex(8),
    }
    encoded = _b64encode(
        json.dumps(claims, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = hmac.new(_token_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_b64encode(signature)}"


def verify_token(token: str, purpose: str) -> dict[str, Any]:
    try:
        encoded, signature_text = token.split(".", 1)
    except ValueError as exc:
        raise MiniappTokenError("登录状态格式无效") from exc
    expected = hmac.new(_token_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
    supplied = _b64decode(signature_text)
    if not hmac.compare_digest(expected, supplied):
        raise MiniappTokenError("登录状态签名无效")
    try:
        claims = json.loads(_b64decode(encoded).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MiniappTokenError("登录状态内容无效") from exc
    if not isinstance(claims, dict) or claims.get("purpose") != purpose:
        raise MiniappTokenError("登录状态用途无效")
    try:
        expires_at = int(claims.get("exp") or 0)
    except (TypeError, ValueError) as exc:
        raise MiniappTokenError("登录状态有效期无效") from exc
    if expires_at <= int(time.time()):
        raise MiniappTokenError("登录状态已过期，请重新登录")
    return claims


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    def iso(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat(timespec="seconds")
        return str(value)

    mobile = str(user.get("mobile") or "")
    identity_type = str(user.get("identity_type") or "").strip().lower()
    is_wechat_user = identity_type in {"wechat", "cloudbase_personal"} or (
        mobile.startswith("wx") and len(mobile) == 20
    )
    return {
        "id": int(user.get("id") or 0),
        "login_name": user.get("login_name"),
        "mobile": None if is_wechat_user else (mobile or None),
        "real_name": user.get("real_name") or "本机用户",
        "roles": list(user.get("roles") or []),
        "is_admin": bool(user.get("is_admin")),
        "service_status": user.get("service_status") or "active",
        "service_valid": bool(user.get("service_valid", True)),
        "service_started_at": iso(user.get("service_started_at")),
        "service_expires_at": iso(user.get("service_expires_at")),
        "is_wechat_user": is_wechat_user,
    }


def issue_access_token(user: dict[str, Any]) -> dict[str, Any]:
    ttl_days = max(1, min(30, int(os.getenv("MINIAPP_TOKEN_DAYS", "7"))))
    token = issue_token({"sub": int(user.get("id") or 0)}, ACCESS_PURPOSE, ttl_days * 86400)
    return {
        "token": token,
        "expires_in": ttl_days * 86400,
        "user": public_user(user),
    }


def local_dev_user() -> dict[str, Any]:
    return {
        "id": 0,
        "login_name": "local",
        "mobile": "",
        "real_name": "本机调试用户",
        "roles": ["ADMIN"],
        "is_admin": True,
        "service_status": "active",
        "service_valid": True,
        "service_started_at": None,
        "service_expires_at": None,
    }


def login_with_account(identifier: str, password: str, ip_address: str | None = None) -> dict[str, Any]:
    if auth_enabled():
        user = authenticate(identifier, password, ip_address=ip_address)
    elif local_dev_enabled():
        user = local_dev_user()
    else:
        raise MiniappConfigurationError("小程序登录服务尚未启用")
    return issue_access_token(user)


def user_from_access_token(token: str) -> dict[str, Any]:
    claims = verify_token(token, ACCESS_PURPOSE)
    user_id = int(claims.get("sub") or 0)
    if claims.get("identity_type") == "cloudbase_personal":
        if not cloudbase_personal_mode_enabled():
            raise MiniappTokenError("云托管个人登录模式已关闭")
        return {
            "id": user_id,
            "login_name": f"wx_{user_id}",
            "mobile": "",
            "real_name": "微信用户",
            "roles": ["ADMIN"],
            "is_admin": True,
            "service_status": "active",
            "service_valid": True,
            "service_started_at": None,
            "service_expires_at": None,
            "identity_type": "cloudbase_personal",
        }
    if user_id == 0:
        if not local_dev_enabled():
            raise MiniappTokenError("本机调试登录已关闭")
        return local_dev_user()
    user = refresh_user(user_id)
    if not user:
        raise MiniappTokenError("账号不存在或已停用")
    return user


def require_active_service(user: dict[str, Any]) -> None:
    if user.get("is_admin") or user.get("service_valid"):
        return
    status = str(user.get("service_status") or "not_opened")
    messages = {
        "expired": "会员服务已到期，请先续费",
        "not_started": "会员服务尚未开始",
        "disabled": "账号已停用，请联系管理员",
        "deleted": "账号不存在或已停用",
    }
    raise MiniappEntitlementError(messages.get(status, "会员服务尚未开通，请先开通"))


def owner_key_for_user(user: dict[str, Any]) -> str:
    user_id = int(user.get("id") or 0)
    return f"user:{user_id}" if user_id else "local"


def cloudbase_identity_enabled() -> bool:
    return _truthy("MINIAPP_TRUST_CLOUDBASE_IDENTITY", "false")


def cloudbase_personal_mode_enabled() -> bool:
    """Use CloudBase's verified WeChat identity without the PC membership database.

    This mode is intentionally limited to a personal mini program deployment.  The
    CloudBase gateway supplies the OpenID headers; callers cannot opt into it by
    posting an arbitrary OpenID to the public API.
    """

    return cloudbase_identity_enabled() and _truthy(
        "MINIAPP_CLOUDBASE_PERSONAL_MODE",
        "false",
    )


def _cloudbase_personal_user(app_id: str, open_id: str) -> dict[str, Any]:
    digest = hashlib.sha256(f"{app_id}:{open_id}".encode("utf-8")).digest()
    user_id = int.from_bytes(digest[:8], "big") % 2_000_000_000 + 1
    return {
        "id": user_id,
        "login_name": f"wx_{hashlib.sha256(open_id.encode('utf-8')).hexdigest()[:12]}",
        "mobile": "",
        "real_name": "微信用户",
        "roles": ["ADMIN"],
        "is_admin": True,
        "service_status": "active",
        "service_valid": True,
        "service_started_at": None,
        "service_expires_at": None,
        "identity_type": "cloudbase_personal",
    }


def wechat_configured() -> bool:
    app_id = os.getenv("WECHAT_MINIAPP_APP_ID", "").strip()
    app_secret = os.getenv("WECHAT_MINIAPP_APP_SECRET", "").strip()
    return bool(app_id and (app_secret or cloudbase_identity_enabled()))


def wechat_auto_register_enabled() -> bool:
    default = "true" if local_dev_enabled() else "false"
    return _truthy("MINIAPP_WECHAT_AUTO_REGISTER", default)


def wechat_trial_days() -> int:
    try:
        value = int(os.getenv("MINIAPP_WECHAT_TRIAL_DAYS", "7"))
    except ValueError:
        value = 7
    return max(0, min(30, value))


def exchange_wechat_code(code: str) -> dict[str, str]:
    app_id = os.getenv("WECHAT_MINIAPP_APP_ID", "").strip()
    app_secret = os.getenv("WECHAT_MINIAPP_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise MiniappConfigurationError("微信 AppID 或 AppSecret 尚未配置")
    if not code.strip():
        raise ValueError("缺少微信登录凭证")
    try:
        response = requests.get(
            "https://api.weixin.qq.com/sns/jscode2session",
            params={
                "appid": app_id,
                "secret": app_secret,
                "js_code": code.strip(),
                "grant_type": "authorization_code",
            },
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MiniappAuthError("微信登录服务暂时不可用") from exc
    if payload.get("errcode"):
        raise MiniappAuthError(f"微信登录失败：{payload.get('errmsg') or payload['errcode']}")
    open_id = str(payload.get("openid") or "").strip()
    if not open_id:
        raise MiniappAuthError("微信登录未返回用户标识")
    return {
        "app_id": app_id,
        "open_id": open_id,
        "union_id": str(payload.get("unionid") or "").strip(),
    }


def _find_bound_user(app_id: str, open_id: str) -> dict[str, Any] | None:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select user_id
                from miniapp_user_bindings
                where provider = 'wechat' and app_id = %s and open_id = %s
                limit 1
                """,
                (app_id, open_id),
            )
            row = cursor.fetchone()
            if row:
                cursor.execute(
                    """
                    update miniapp_user_bindings
                    set last_login_at = UTC_TIMESTAMP()
                    where provider = 'wechat' and app_id = %s and open_id = %s
                    """,
                    (app_id, open_id),
                )
                connection.commit()
        return refresh_user(int(row["user_id"])) if row else None
    finally:
        connection.close()


def _create_wechat_user(identity: dict[str, str]) -> dict[str, Any]:
    try:
        import bcrypt
        import pymysql
    except ImportError as exc:
        raise MiniappConfigurationError("服务器会员依赖未安装") from exc

    app_id = identity["app_id"]
    open_id = identity["open_id"]
    union_id = identity.get("union_id") or None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    days = wechat_trial_days()
    expires_at = now + timedelta(days=days) if days else None
    internal_mobile = "wx" + hashlib.sha256(f"{app_id}:{open_id}".encode("utf-8")).hexdigest()[:18]
    random_password = secrets.token_urlsafe(32).encode("utf-8")
    password_hash = bcrypt.hashpw(random_password, bcrypt.gensalt(rounds=12)).decode("ascii")

    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select user_id
                from miniapp_user_bindings
                where provider = 'wechat' and app_id = %s and open_id = %s
                limit 1 for update
                """,
                (app_id, open_id),
            )
            existing = cursor.fetchone()
            if existing:
                connection.commit()
                user = refresh_user(int(existing["user_id"]))
                if not user:
                    raise MiniappAuthError("微信账号绑定的会员不存在")
                return user

            cursor.execute(
                """
                insert into sys_users (
                    mobile, real_name, password_hash, status,
                    service_started_at, service_expires_at, password_changed_at,
                    last_login_at, remark
                ) values (%s, '微信用户', %s, 'active', %s, %s, %s, %s, %s)
                """,
                (
                    internal_mobile,
                    password_hash,
                    now if days else None,
                    expires_at,
                    now,
                    now,
                    f"微信小程序快捷登录自动创建；首次体验{days}天" if days else "微信小程序快捷登录自动创建",
                ),
            )
            user_id = int(cursor.lastrowid)
            cursor.execute(
                """
                insert into sys_user_roles (user_id, role_id, granted_by)
                select %s, id, null from sys_roles where role_code = 'MEMBER'
                """,
                (user_id,),
            )
            if days and expires_at:
                cursor.execute(
                    """
                    insert into subscription_periods (
                        user_id, start_at, end_at, source_type, remark
                    ) values (%s, %s, %s, 'gift', %s)
                    """,
                    (user_id, now, expires_at, "微信小程序首次登录体验"),
                )
            cursor.execute(
                """
                insert into miniapp_user_bindings (
                    provider, app_id, open_id, union_id, user_id, last_login_at
                ) values ('wechat', %s, %s, %s, %s, %s)
                """,
                (app_id, open_id, union_id, user_id, now),
            )
        connection.commit()
    except pymysql.err.IntegrityError:
        connection.rollback()
        user = _find_bound_user(app_id, open_id)
        if user:
            return user
        raise
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    user = refresh_user(user_id)
    if not user:
        raise MiniappAuthError("微信用户创建失败，请稍后重试")
    return user


def login_with_wechat_identity(identity: dict[str, str]) -> dict[str, Any]:
    if not auth_enabled():
        if local_dev_enabled():
            return {"status": "authenticated", **issue_access_token(local_dev_user())}
        raise MiniappConfigurationError("微信登录必须启用会员数据库")

    app_id = str(identity.get("app_id") or "").strip()
    open_id = str(identity.get("open_id") or "").strip()
    if not app_id or not open_id:
        raise MiniappAuthError("微信登录未返回完整用户标识")
    normalized_identity = {
        "app_id": app_id,
        "open_id": open_id,
        "union_id": str(identity.get("union_id") or "").strip(),
    }

    user = _find_bound_user(app_id, open_id)
    if user:
        return {"status": "authenticated", **issue_access_token(user)}
    if wechat_auto_register_enabled():
        user = _create_wechat_user(normalized_identity)
        return {"status": "authenticated", "is_new_user": True, **issue_access_token(user)}
    bind_token = issue_token(normalized_identity, BIND_PURPOSE, 10 * 60)
    return {"status": "binding_required", "bind_token": bind_token, "expires_in": 600}


def login_with_wechat(code: str) -> dict[str, Any]:
    return login_with_wechat_identity(exchange_wechat_code(code))


def login_with_cloudbase_wechat(
    app_id: str,
    open_id: str,
    union_id: str = "",
) -> dict[str, Any]:
    if not cloudbase_identity_enabled():
        raise MiniappConfigurationError("云托管微信身份登录尚未启用")

    configured_app_id = os.getenv("WECHAT_MINIAPP_APP_ID", "").strip()
    supplied_app_id = app_id.strip()
    supplied_open_id = open_id.strip()
    if not configured_app_id:
        raise MiniappConfigurationError("云托管模式必须设置 WECHAT_MINIAPP_APP_ID")
    if not supplied_app_id or not supplied_open_id:
        raise MiniappAuthError("云托管未注入完整微信身份")
    if not secrets.compare_digest(configured_app_id, supplied_app_id):
        raise MiniappAuthError("云托管微信 AppID 与当前小程序不一致")

    if cloudbase_personal_mode_enabled():
        user = _cloudbase_personal_user(supplied_app_id, supplied_open_id)
        ttl_days = max(1, min(30, int(os.getenv("MINIAPP_TOKEN_DAYS", "7"))))
        token_payload = {
            "token": issue_token(
                {
                    "sub": int(user["id"]),
                    "identity_type": "cloudbase_personal",
                },
                ACCESS_PURPOSE,
                ttl_days * 86400,
            ),
            "expires_in": ttl_days * 86400,
            "user": public_user(user),
        }
        return {"status": "authenticated", **token_payload}

    return login_with_wechat_identity(
        {
            "app_id": supplied_app_id,
            "open_id": supplied_open_id,
            "union_id": union_id.strip(),
        }
    )


def _bind_identity(user_id: int, claims: dict[str, Any]) -> None:
    app_id = str(claims.get("app_id") or "")
    open_id = str(claims.get("open_id") or "")
    union_id = str(claims.get("union_id") or "") or None
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select user_id
                from miniapp_user_bindings
                where provider = 'wechat' and app_id = %s and open_id = %s
                limit 1
                """,
                (app_id, open_id),
            )
            existing = cursor.fetchone()
            if existing and int(existing["user_id"]) != user_id:
                raise MiniappAuthError("该微信账号已绑定其他会员")
            cursor.execute(
                """
                delete from miniapp_user_bindings
                where provider = 'wechat' and app_id = %s and user_id = %s and open_id <> %s
                """,
                (app_id, user_id, open_id),
            )
            cursor.execute(
                """
                insert into miniapp_user_bindings (
                    provider, app_id, open_id, union_id, user_id, last_login_at
                ) values ('wechat', %s, %s, %s, %s, UTC_TIMESTAMP())
                on duplicate key update
                    union_id = values(union_id),
                    last_login_at = UTC_TIMESTAMP()
                """,
                (app_id, open_id, union_id, user_id),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def bind_wechat_account(
    bind_token: str,
    identifier: str,
    password: str,
    ip_address: str | None = None,
) -> dict[str, Any]:
    claims = verify_token(bind_token, BIND_PURPOSE)
    user = authenticate(identifier, password, ip_address=ip_address)
    _bind_identity(int(user["id"]), claims)
    return issue_access_token(user)


def validate_api_startup(host: str) -> None:
    _token_secret()
    production = os.getenv("APP_ENV", "local").strip().lower() in {"production", "prod"}
    personal_cloudbase = cloudbase_personal_mode_enabled()
    if production and not auth_enabled() and not personal_cloudbase:
        raise MiniappConfigurationError("正式环境必须设置 AUTH_ENABLED=true")
    if cloudbase_identity_enabled() and not os.getenv("WECHAT_MINIAPP_APP_ID", "").strip():
        raise MiniappConfigurationError("云托管身份模式必须设置 WECHAT_MINIAPP_APP_ID")
    if _truthy("MINIAPP_REQUIRE_PERSISTENT_STORAGE", "false"):
        if not DATA_DIR.exists() or not os.path.ismount(DATA_DIR):
            raise MiniappConfigurationError(f"云端持久化目录尚未挂载：{DATA_DIR}")
        if not os.access(DATA_DIR, os.W_OK):
            raise MiniappConfigurationError(f"云端持久化目录不可写：{DATA_DIR}")
    if (
        not auth_enabled()
        and not personal_cloudbase
        and host not in {"127.0.0.1", "localhost", "::1"}
    ):
        raise MiniappConfigurationError("免登录调试模式只能绑定本机地址")


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
