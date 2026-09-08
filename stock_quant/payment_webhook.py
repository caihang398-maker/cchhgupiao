from __future__ import annotations

import argparse
import json
import logging
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from stock_quant.logging_config import configure_application_logging
from stock_quant.payments import (
    PaymentConfigurationError,
    PaymentError,
    PaymentSignatureError,
    PaymentValidationError,
    load_creem_config,
    payment_enabled,
    payment_tables_available,
    process_creem_webhook,
)


LOGGER = logging.getLogger(__name__)
MAX_BODY_BYTES = 1024 * 1024


class PaymentWebhookServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class PaymentWebhookHandler(BaseHTTPRequestHandler):
    server_version = "StockQuantPaymentWebhook/1.0"

    def _json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        if path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        self._json(HTTPStatus.NOT_FOUND, {"status": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        if path != "/webhooks/creem":
            self._json(HTTPStatus.NOT_FOUND, {"status": "not_found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"status": "invalid_content_length"})
            return
        if content_length <= 0 or content_length > MAX_BODY_BYTES:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"status": "invalid_body_size"})
            return

        raw_body = self.rfile.read(content_length)
        signature = self.headers.get("creem-signature", "")
        try:
            result = process_creem_webhook(raw_body, signature)
            self._json(
                HTTPStatus.OK,
                {
                    "status": result.status,
                    "event_id": result.event_id,
                    "event_type": result.event_type,
                    "message": result.message,
                },
            )
        except PaymentSignatureError:
            LOGGER.warning("拒绝 Creem 无效签名回调，来源：%s", self.client_address[0])
            self._json(HTTPStatus.UNAUTHORIZED, {"status": "invalid_signature"})
        except PaymentValidationError as exc:
            LOGGER.warning("Creem 回调校验失败：%s", exc)
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"status": "invalid_event"})
        except PaymentConfigurationError:
            LOGGER.exception("Creem 回调服务配置不完整")
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"status": "service_not_ready"})
        except PaymentError:
            LOGGER.exception("Creem 回调业务处理失败")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"status": "processing_failed"})
        except Exception:
            LOGGER.exception("Creem 回调发生未处理异常")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"status": "internal_error"})

    def log_message(self, message_format: str, *args: object) -> None:
        LOGGER.info(
            "%s - %s",
            self.client_address[0],
            message_format % args,
        )


def validate_startup() -> None:
    if not payment_enabled():
        raise PaymentConfigurationError("PAYMENT_ENABLED 未开启，支付回调服务不会启动")
    load_creem_config(require_webhook_secret=True)
    if not payment_tables_available():
        raise PaymentConfigurationError("支付数据库表尚未安装")


def main() -> int:
    parser = argparse.ArgumentParser(description="Creem 支付回调服务")
    parser.add_argument(
        "--host",
        default=os.getenv("PAYMENT_WEBHOOK_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PAYMENT_WEBHOOK_PORT", "8511")),
    )
    args = parser.parse_args()

    configure_application_logging()
    validate_startup()
    server = PaymentWebhookServer((args.host, args.port), PaymentWebhookHandler)
    LOGGER.info("Creem 支付回调服务已启动：http://%s:%s", args.host, args.port)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        LOGGER.info("Creem 支付回调服务正在停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
