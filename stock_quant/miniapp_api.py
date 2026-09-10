from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .auth import auth_enabled
from .cloud_state import cloud_sqlite_sync_enabled, prepare_cloud_state, sync_cloud_state
from .logging_config import configure_application_logging
from .miniapp_feed import cloud_feed_status, refresh_market_feed
from .miniapp_auth import (
    MiniappAuthError,
    MiniappConfigurationError,
    MiniappEntitlementError,
    MiniappTokenError,
    bind_wechat_account,
    cloudbase_identity_enabled,
    login_with_account,
    login_with_cloudbase_wechat,
    login_with_wechat,
    owner_key_for_user,
    public_user,
    require_active_service,
    user_from_access_token,
    validate_api_startup,
    wechat_configured,
)
from .miniapp_membership import subscription_summary
from .miniapp_product import miniapp_product_mode, personal_records_mode_enabled
from .miniapp_service import (
    MiniappServiceError,
    create_alert,
    create_quick_alerts,
    evaluate_alerts,
    generate_paper_trades,
    generate_position_plan,
    get_data_status,
    get_home,
    get_market,
    get_paper_account,
    get_review,
    get_stock_detail,
    json_safe,
    list_alerts,
    list_positions,
    list_recommendations,
    remove_position,
    save_position,
    screen_recommendations,
    search_stocks,
    toggle_alert,
)


LOGGER = logging.getLogger(__name__)
MAX_BODY_BYTES = 128 * 1024
LOGIN_WINDOW_SECONDS = 60
LOGIN_MAX_ATTEMPTS = 10


class MiniappApiServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class LoginLimiter:
    def __init__(self) -> None:
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client_ip: str) -> bool:
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts[client_ip]
            while attempts and now - attempts[0] > LOGIN_WINDOW_SECONDS:
                attempts.popleft()
            if len(attempts) >= LOGIN_MAX_ATTEMPTS:
                return False
            attempts.append(now)
            return True


LOGIN_LIMITER = LoginLimiter()


class MiniappApiHandler(BaseHTTPRequestHandler):
    server_version = "MiniappAPI/1.0"

    def _client_ip(self) -> str:
        trust_proxy = os.getenv("MINIAPP_TRUST_PROXY", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if trust_proxy or cloudbase_identity_enabled():
            forwarded = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            if forwarded:
                return forwarded
        return self.client_address[0]

    def _headers(self, status: int, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        origin = os.getenv("MINIAPP_CORS_ORIGIN", "").strip()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(json_safe(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._headers(status, len(body))
        self.wfile.write(body)

    def _ok(self, data: Any = None, status: int = HTTPStatus.OK) -> None:
        self._json(int(status), {"ok": True, "data": data if data is not None else {}})

    def _error(self, status: int, code: str, message: str) -> None:
        self._json(int(status), {"ok": False, "error": {"code": code, "message": message}})

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise MiniappServiceError("请求长度无效", "invalid_content_length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise MiniappServiceError("请求内容过大", "body_too_large", 413)
        if length == 0:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MiniappServiceError("请求内容不是有效JSON", "invalid_json") from exc
        if not isinstance(payload, dict):
            raise MiniappServiceError("请求内容必须是JSON对象", "invalid_json")
        return payload

    def _bearer_token(self) -> str:
        authorization = self.headers.get("Authorization", "").strip()
        if not authorization.lower().startswith("bearer "):
            raise MiniappTokenError("请先登录")
        return authorization[7:].strip()

    def _query_value(self, query: dict[str, list[str]], key: str, default: str = "") -> str:
        values = query.get(key)
        return str(values[0]) if values else default

    def _int_query(
        self,
        query: dict[str, list[str]],
        key: str,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        try:
            value = int(self._query_value(query, key, str(default)))
        except ValueError:
            value = default
        return max(minimum, min(maximum, value))

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        origin = os.getenv("MINIAPP_CORS_ORIGIN", "").strip()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        should_sync = cloud_sqlite_sync_enabled() and method in {"POST", "PATCH", "DELETE"}
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)
            if method == "GET" and path in {"/health", "/api/v1/health"}:
                self._ok(
                    {
                        "status": "ok",
                        "auth_enabled": auth_enabled(),
                        "wechat_configured": wechat_configured(),
                        "product_mode": miniapp_product_mode(),
                        "market_feed": cloud_feed_status(),
                    }
                )
                return
            if personal_records_mode_enabled() and not _personal_records_route_allowed(
                method, path
            ):
                self._error(HTTPStatus.NOT_FOUND, "not_found", "接口不存在")
                return
            if method == "POST" and path == "/api/v1/auth/login":
                self._login()
                return
            if method == "POST" and path == "/api/v1/auth/wechat":
                self._wechat_login()
                return
            if method == "POST" and path == "/api/v1/auth/bind":
                self._wechat_bind()
                return

            user = user_from_access_token(self._bearer_token())
            owner_key = owner_key_for_user(user)

            if method == "GET" and path == "/api/v1/me":
                self._ok({"user": public_user(user), "owner_key": owner_key})
                return
            if method == "GET" and path == "/api/v1/subscription":
                self._ok(subscription_summary(user))
                return

            require_active_service(user)

            if method == "GET":
                refresh_market_feed()

            if method == "GET" and path == "/api/v1/home":
                self._ok(get_home(user, self._int_query(query, "limit", 12, 1, 50)))
                return
            if method == "GET" and path == "/api/v1/recommendations":
                self._ok(
                    list_recommendations(
                        user,
                        horizon=self._query_value(query, "horizon"),
                        keyword=self._query_value(query, "keyword"),
                        page=self._int_query(query, "page", 1, 1, 10000),
                        page_size=self._int_query(query, "page_size", 20, 1, 50),
                    )
                )
                return
            if method == "POST" and path == "/api/v1/screener":
                self._ok(screen_recommendations(user, self._body()))
                return
            if method == "GET" and path == "/api/v1/stocks/search":
                self._ok(
                    search_stocks(
                        user,
                        owner_key,
                        self._query_value(query, "keyword"),
                        self._int_query(query, "limit", 20, 1, 50),
                    )
                )
                return
            if method == "GET" and path.startswith("/api/v1/stocks/"):
                stock_query = unquote(path.removeprefix("/api/v1/stocks/"))
                self._ok(
                    get_stock_detail(
                        user,
                        owner_key,
                        stock_query,
                        self._int_query(query, "days", 120, 30, 240),
                    )
                )
                return
            if method == "GET" and path == "/api/v1/market":
                self._ok(get_market(user))
                return
            if method == "GET" and path == "/api/v1/data-status":
                self._ok(get_data_status(user))
                return
            if method == "GET" and path == "/api/v1/review":
                self._ok(get_review(user, owner_key))
                return
            if method == "GET" and path == "/api/v1/paper":
                self._ok(get_paper_account(user, owner_key))
                return
            if method == "POST" and path == "/api/v1/paper/generate":
                self._ok(generate_paper_trades(user, owner_key, self._body()))
                return
            if method == "GET" and path == "/api/v1/positions":
                self._ok(list_positions(user, owner_key))
                return
            if method == "POST" and path == "/api/v1/positions":
                self._ok(save_position(user, owner_key, self._body()), HTTPStatus.CREATED)
                return
            if path.startswith("/api/v1/positions/"):
                self._position_action(method, path, user, owner_key)
                return
            if method == "GET" and path == "/api/v1/alerts":
                self._ok(list_alerts(user, owner_key))
                return
            if method == "POST" and path == "/api/v1/alerts":
                self._ok(create_alert(user, owner_key, self._body()), HTTPStatus.CREATED)
                return
            if method == "POST" and path == "/api/v1/alerts/evaluate":
                self._ok(evaluate_alerts(user, owner_key))
                return
            if method == "POST" and path == "/api/v1/alerts/quick":
                self._ok(create_quick_alerts(user, owner_key, self._body()))
                return
            if path.startswith("/api/v1/alerts/"):
                self._alert_action(method, path, user, owner_key)
                return
            self._error(HTTPStatus.NOT_FOUND, "not_found", "接口不存在")
        except MiniappTokenError as exc:
            self._error(HTTPStatus.UNAUTHORIZED, "unauthorized", str(exc))
        except MiniappConfigurationError as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "service_not_configured", str(exc))
        except MiniappServiceError as exc:
            self._error(exc.status, exc.code, str(exc))
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_request", str(exc))
        except MiniappEntitlementError as exc:
            self._error(HTTPStatus.FORBIDDEN, "subscription_required", str(exc))
        except MiniappAuthError as exc:
            self._error(HTTPStatus.UNAUTHORIZED, "authentication_failed", str(exc))
        except Exception:
            LOGGER.exception("小程序接口发生未处理异常：%s %s", method, self.path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "服务暂时不可用，请稍后重试")
        finally:
            if should_sync:
                try:
                    sync_cloud_state()
                except Exception:
                    LOGGER.exception("小程序数据持久化同步失败")

    def _login(self) -> None:
        client_ip = self._client_ip()
        if not LOGIN_LIMITER.allow(client_ip):
            self._error(HTTPStatus.TOO_MANY_REQUESTS, "too_many_attempts", "登录尝试过于频繁，请稍后再试")
            return
        payload = self._body()
        result = login_with_account(
            str(payload.get("identifier") or ""),
            str(payload.get("password") or ""),
            ip_address=client_ip,
        )
        self._ok(result)

    def _wechat_login(self) -> None:
        client_ip = self._client_ip()
        if not LOGIN_LIMITER.allow(client_ip):
            self._error(HTTPStatus.TOO_MANY_REQUESTS, "too_many_attempts", "登录尝试过于频繁，请稍后再试")
            return
        if cloudbase_identity_enabled():
            self._ok(
                login_with_cloudbase_wechat(
                    self.headers.get("X-WX-APPID", ""),
                    self.headers.get("X-WX-OPENID", ""),
                    self.headers.get("X-WX-UNIONID", ""),
                )
            )
            return
        payload = self._body()
        self._ok(login_with_wechat(str(payload.get("code") or "")))

    def _wechat_bind(self) -> None:
        client_ip = self._client_ip()
        if not LOGIN_LIMITER.allow(client_ip):
            self._error(HTTPStatus.TOO_MANY_REQUESTS, "too_many_attempts", "登录尝试过于频繁，请稍后再试")
            return
        payload = self._body()
        account = bind_wechat_account(
            str(payload.get("bind_token") or ""),
            str(payload.get("identifier") or ""),
            str(payload.get("password") or ""),
            ip_address=client_ip,
        )
        self._ok(account)

    def _position_action(
        self,
        method: str,
        path: str,
        user: dict[str, Any],
        owner_key: str,
    ) -> None:
        parts = path.split("/")
        try:
            position_id = int(parts[4])
        except (IndexError, ValueError) as exc:
            raise MiniappServiceError("持仓编号无效", "invalid_position_id") from exc
        if method == "POST" and len(parts) == 6 and parts[5] == "plan":
            self._ok(generate_position_plan(user, owner_key, position_id))
            return
        if method == "DELETE" and len(parts) == 5:
            self._ok(remove_position(user, owner_key, position_id))
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found", "持仓接口不存在")

    def _alert_action(
        self,
        method: str,
        path: str,
        user: dict[str, Any],
        owner_key: str,
    ) -> None:
        parts = path.split("/")
        try:
            rule_id = int(parts[4])
        except (IndexError, ValueError) as exc:
            raise MiniappServiceError("预警编号无效", "invalid_alert_id") from exc
        if method == "PATCH" and len(parts) == 5:
            payload = self._body()
            if "enabled" not in payload:
                raise MiniappServiceError("缺少启用状态", "invalid_alert")
            self._ok(toggle_alert(user, owner_key, rule_id, bool(payload["enabled"])))
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found", "预警接口不存在")

    def log_message(self, message_format: str, *args: object) -> None:
        LOGGER.info("%s - %s", self._client_ip(), message_format % args)


def validate_startup(host: str) -> None:
    validate_api_startup(host)


def _personal_records_route_allowed(method: str, path: str) -> bool:
    return (method, path) in {
        ("GET", "/health"),
        ("GET", "/api/v1/health"),
        ("POST", "/api/v1/auth/wechat"),
        ("GET", "/api/v1/me"),
        ("GET", "/api/v1/subscription"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="微信小程序 API")
    parser.add_argument("--host", default=os.getenv("MINIAPP_API_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT") or os.getenv("MINIAPP_API_PORT", "8512")),
    )
    args = parser.parse_args()
    configure_application_logging()
    prepare_cloud_state()
    refresh_market_feed(force=True)
    validate_startup(args.host)
    server = MiniappApiServer((args.host, args.port), MiniappApiHandler)
    LOGGER.info("小程序 API 已启动：http://%s:%s", args.host, args.port)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        LOGGER.info("小程序 API 正在停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
