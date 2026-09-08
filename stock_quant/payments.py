from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from stock_quant.auth import _connect, _utcnow_naive


LOGGER = logging.getLogger(__name__)
TRUTHY_VALUES = {"1", "true", "yes", "on"}
PAYMENT_TABLES = {
    "payment_provider_products",
    "payment_checkout_sessions",
    "payment_subscriptions",
    "payment_webhook_events",
}
CREEM_EVENT_TYPES = {
    "checkout.completed",
    "subscription.active",
    "subscription.paid",
    "subscription.update",
    "subscription.canceled",
    "subscription.cancelled",
    "subscription.scheduled_cancel",
    "subscription.past_due",
    "subscription.paused",
    "subscription.trialing",
    "subscription.expired",
    "refund.created",
    "dispute.created",
}


class PaymentError(RuntimeError):
    """Base error for payment operations."""


class PaymentConfigurationError(PaymentError):
    """Raised when payment settings are missing or unsafe."""


class PaymentGatewayError(PaymentError):
    """Raised when Creem cannot create or return a checkout."""


class PaymentSignatureError(PaymentError):
    """Raised when a webhook signature cannot be verified."""


class PaymentValidationError(PaymentError):
    """Raised when a paid event does not match the configured product."""


@dataclass(frozen=True)
class CreemConfig:
    environment: str
    base_url: str
    api_key: str
    webhook_secret: str
    success_url: str
    live_enabled: bool

    @property
    def channel_name(self) -> str:
        return "Creem正式支付" if self.environment == "live" else "Creem测试支付"


@dataclass(frozen=True)
class WebhookResult:
    event_id: str
    event_type: str
    status: str
    message: str


def payment_enabled() -> bool:
    return os.getenv("PAYMENT_ENABLED", "false").strip().lower() in TRUTHY_VALUES


def creem_environment() -> str:
    value = os.getenv("CREEM_MODE", "test").strip().lower()
    if value in {"production", "prod"}:
        value = "live"
    if value not in {"test", "live"}:
        raise PaymentConfigurationError("CREEM_MODE 只能设置为 test 或 live")
    return value


def load_creem_config(
    *,
    require_api_key: bool = False,
    require_webhook_secret: bool = False,
) -> CreemConfig:
    environment = creem_environment()
    live_enabled = os.getenv("CREEM_LIVE_ENABLED", "false").strip().lower() in TRUTHY_VALUES
    if environment == "live" and not live_enabled:
        raise PaymentConfigurationError(
            "正式收款保护开关未开启；完成店铺审核和真实付款测试后再设置 CREEM_LIVE_ENABLED=true"
        )

    prefix = "CREEM_LIVE" if environment == "live" else "CREEM_TEST"
    api_key = os.getenv(f"{prefix}_API_KEY", "").strip()
    webhook_secret = os.getenv(f"{prefix}_WEBHOOK_SECRET", "").strip()
    success_url = os.getenv(
        "CREEM_SUCCESS_URL",
        "http://127.0.0.1:8501/subscription?checkout=success",
    ).strip()
    base_url = (
        "https://api.creem.io/v1"
        if environment == "live"
        else "https://test-api.creem.io/v1"
    )

    if require_api_key and not api_key:
        raise PaymentConfigurationError(f"缺少服务器环境变量 {prefix}_API_KEY")
    if require_webhook_secret and not webhook_secret:
        raise PaymentConfigurationError(f"缺少服务器环境变量 {prefix}_WEBHOOK_SECRET")
    _validate_success_url(success_url, environment)
    return CreemConfig(
        environment=environment,
        base_url=base_url,
        api_key=api_key,
        webhook_secret=webhook_secret,
        success_url=success_url,
        live_enabled=live_enabled,
    )


def _validate_success_url(value: str, environment: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PaymentConfigurationError("CREEM_SUCCESS_URL 必须是完整的 http 或 https 地址")
    if parsed.username or parsed.password:
        raise PaymentConfigurationError("CREEM_SUCCESS_URL 不能包含账号或密码")
    if environment == "live":
        if parsed.scheme != "https":
            raise PaymentConfigurationError("Creem 正式收款的成功返回地址必须使用 HTTPS")
        if parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
            raise PaymentConfigurationError("Creem 正式收款不能使用本机地址作为成功返回地址")


def _success_url_for_order(base_url: str, order_no: str) -> str:
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({"checkout": "success", "order": order_no})
    return urlunparse(parsed._replace(query=urlencode(query)))


def verify_creem_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    normalized = str(signature or "").strip()
    if normalized.lower().startswith("sha256="):
        normalized = normalized.split("=", 1)[1].strip()
    if not normalized or not secret:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected.lower(), normalized.lower())


def parse_creem_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _minor_to_amount(value: int) -> Decimal:
    return (Decimal(int(value)) / Decimal("100")).quantize(Decimal("0.01"))


def _normalized_currency(value: object) -> str:
    currency = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise PaymentValidationError("支付币种格式无效")
    return currency


def _validate_existing_payment_transaction(
    transaction: dict[str, Any] | None,
    *,
    user_id: int,
    plan_id: int,
    expected_amount: Decimal,
    expected_currency: str,
    expected_channel: str,
) -> None:
    if not transaction:
        return
    try:
        transaction_amount = Decimal(str(transaction.get("amount"))).quantize(
            Decimal("0.01")
        )
    except (ArithmeticError, TypeError, ValueError):
        raise PaymentValidationError("已存在交易的金额记录无效") from None
    if int(transaction.get("user_id") or 0) != int(user_id):
        raise PaymentValidationError("交易流水已被其他会员订单使用")
    if int(transaction.get("plan_id") or 0) != int(plan_id):
        raise PaymentValidationError("交易流水对应的会员套餐不一致")
    if transaction_amount != expected_amount:
        raise PaymentValidationError("交易流水对应的支付金额不一致")
    if _normalized_currency(transaction.get("currency")) != expected_currency:
        raise PaymentValidationError("交易流水对应的支付币种不一致")
    if str(transaction.get("channel") or "") != expected_channel:
        raise PaymentValidationError("交易流水对应的支付渠道不一致")
    if str(transaction.get("status") or "") != "success":
        raise PaymentValidationError("交易流水尚未处于支付成功状态")


def payment_tables_available() -> bool:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select table_name
                from information_schema.tables
                where table_schema = database()
                  and table_name in (
                    'payment_provider_products',
                    'payment_checkout_sessions',
                    'payment_subscriptions',
                    'payment_webhook_events'
                  )
                """
            )
            found = {str(row["table_name"]).lower() for row in cursor.fetchall()}
        return PAYMENT_TABLES.issubset(found)
    finally:
        connection.close()


def list_provider_product_mappings() -> list[dict[str, Any]]:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select
                    m.id,
                    m.plan_id,
                    p.plan_code,
                    p.plan_name,
                    p.duration_months,
                    p.price as local_price,
                    p.currency as local_currency,
                    m.provider,
                    m.environment,
                    m.external_product_id,
                    m.provider_price_minor,
                    m.provider_currency,
                    m.status,
                    m.created_at,
                    m.updated_at
                from payment_provider_products m
                join subscription_plans p on p.id = m.plan_id
                order by m.environment, p.price, p.id
                """
            )
            return list(cursor.fetchall())
    finally:
        connection.close()


def save_provider_product_mapping(
    plan_id: int,
    environment: str,
    external_product_id: str,
    provider_price_minor: int,
    provider_currency: str,
    status: str = "active",
) -> int:
    normalized_environment = str(environment or "").strip().lower()
    normalized_product_id = str(external_product_id or "").strip()
    normalized_currency = _normalized_currency(provider_currency)
    normalized_status = str(status or "").strip().lower()
    if normalized_environment not in {"test", "live"}:
        raise ValueError("支付环境只能是测试或正式")
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,100}", normalized_product_id):
        raise ValueError("Creem 产品编号格式不正确")
    if int(provider_price_minor) <= 0:
        raise ValueError("Creem 产品金额必须大于0")
    if normalized_status not in {"active", "disabled"}:
        raise ValueError("映射状态只能是启用或停用")

    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("select id from subscription_plans where id = %s", (int(plan_id),))
            if not cursor.fetchone():
                raise ValueError("套餐不存在")
            cursor.execute(
                """
                insert into payment_provider_products (
                    plan_id, provider, environment, external_product_id,
                    provider_price_minor, provider_currency, status
                ) values (%s, 'creem', %s, %s, %s, %s, %s)
                on duplicate key update
                    external_product_id = values(external_product_id),
                    provider_price_minor = values(provider_price_minor),
                    provider_currency = values(provider_currency),
                    status = values(status)
                """,
                (
                    int(plan_id),
                    normalized_environment,
                    normalized_product_id,
                    int(provider_price_minor),
                    normalized_currency,
                    normalized_status,
                ),
            )
            cursor.execute(
                """
                select id
                from payment_provider_products
                where provider = 'creem' and environment = %s and plan_id = %s
                """,
                (normalized_environment, int(plan_id)),
            )
            row = cursor.fetchone()
        connection.commit()
        return int(row["id"])
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_purchasable_plans(environment: str | None = None) -> list[dict[str, Any]]:
    selected_environment = environment or creem_environment()
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select
                    p.id,
                    p.plan_code,
                    p.plan_name,
                    p.duration_months,
                    p.daily_query_limit,
                    p.features_json,
                    m.environment,
                    m.external_product_id,
                    m.provider_price_minor,
                    m.provider_currency
                from subscription_plans p
                join payment_provider_products m
                  on m.plan_id = p.id
                 and m.provider = 'creem'
                 and m.environment = %s
                 and m.status = 'active'
                where p.status = 'active'
                order by m.provider_price_minor asc, p.id asc
                """,
                (selected_environment,),
            )
            return list(cursor.fetchall())
    finally:
        connection.close()


def list_member_payment_orders(user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select
                    o.id,
                    o.order_no,
                    o.plan_name_snapshot,
                    o.duration_months_snapshot,
                    o.order_amount,
                    o.paid_amount,
                    o.currency,
                    o.order_status,
                    o.pay_channel,
                    o.paid_at,
                    o.service_start_at,
                    o.service_end_at,
                    o.created_at,
                    c.environment,
                    c.status as checkout_status
                from subscription_orders o
                left join payment_checkout_sessions c on c.order_id = o.id
                where o.user_id = %s
                order by o.created_at desc, o.id desc
                limit %s
                """,
                (int(user_id), int(limit)),
            )
            return list(cursor.fetchall())
    finally:
        connection.close()


def list_payment_subscriptions(
    user_id: int | None = None,
    limit: int = 300,
) -> list[dict[str, Any]]:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            where = "where s.user_id = %s" if user_id is not None else ""
            params: tuple[Any, ...] = (
                (int(user_id), int(limit))
                if user_id is not None
                else (int(limit),)
            )
            cursor.execute(
                f"""
                select
                    s.id,
                    s.user_id,
                    u.mobile,
                    u.real_name,
                    s.plan_id,
                    p.plan_name,
                    s.environment,
                    s.provider_subscription_id,
                    s.provider_customer_id,
                    s.status,
                    s.current_period_start_at,
                    s.current_period_end_at,
                    s.next_transaction_at,
                    s.canceled_at,
                    s.updated_at
                from payment_subscriptions s
                join sys_users u on u.id = s.user_id
                join subscription_plans p on p.id = s.plan_id
                {where}
                order by s.updated_at desc, s.id desc
                limit %s
                """,
                params,
            )
            return list(cursor.fetchall())
    finally:
        connection.close()


def list_payment_webhook_events(limit: int = 300) -> list[dict[str, Any]]:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select
                    id,
                    provider,
                    environment,
                    event_id,
                    event_type,
                    process_status,
                    attempts,
                    error_message,
                    received_at,
                    last_attempt_at,
                    processed_at
                from payment_webhook_events
                order by received_at desc, id desc
                limit %s
                """,
                (int(limit),),
            )
            return list(cursor.fetchall())
    finally:
        connection.close()


def create_creem_checkout(user_id: int, plan_id: int) -> dict[str, Any]:
    if not payment_enabled():
        raise PaymentConfigurationError("在线订阅当前未开启，请联系管理员")
    config = load_creem_config(require_api_key=True)
    if not payment_tables_available():
        raise PaymentConfigurationError("支付数据库尚未升级，请管理员先执行支付迁移脚本")

    connection = _connect()
    order_id = 0
    order_no = ""
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select id, status, deleted_at, mobile, real_name
                from sys_users
                where id = %s
                for update
                """,
                (int(user_id),),
            )
            account = cursor.fetchone()
            if not account or account.get("deleted_at") is not None:
                raise ValueError("会员账号不存在")
            if str(account.get("status") or "") != "active":
                raise ValueError("账号已停用，不能创建支付订单")

            cursor.execute(
                """
                select
                    p.id,
                    p.plan_name,
                    p.duration_months,
                    m.external_product_id,
                    m.provider_price_minor,
                    m.provider_currency
                from subscription_plans p
                join payment_provider_products m
                  on m.plan_id = p.id
                 and m.provider = 'creem'
                 and m.environment = %s
                 and m.status = 'active'
                where p.id = %s and p.status = 'active'
                for update
                """,
                (config.environment, int(plan_id)),
            )
            plan = cursor.fetchone()
            if not plan:
                raise ValueError("套餐未启用，或尚未配置当前 Creem 环境的产品编号")

            cursor.execute(
                """
                select
                    o.order_no,
                    c.checkout_id,
                    c.checkout_url,
                    c.status
                from payment_checkout_sessions c
                join subscription_orders o on o.id = c.order_id
                where o.user_id = %s
                  and o.plan_id = %s
                  and o.order_status = 'pending'
                  and c.provider = 'creem'
                  and c.environment = %s
                  and c.status in ('ready', 'completed')
                  and c.created_at >= UTC_TIMESTAMP() - interval 15 minute
                  and c.checkout_url is not null
                order by c.id desc
                limit 1
                """,
                (int(user_id), int(plan_id), config.environment),
            )
            reusable = cursor.fetchone()
            if reusable:
                connection.commit()
                return {
                    "order_no": reusable["order_no"],
                    "checkout_id": reusable["checkout_id"],
                    "checkout_url": reusable["checkout_url"],
                    "environment": config.environment,
                    "reused": True,
                }

            now = _utcnow_naive()
            order_no = f"CR{now.strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:8].upper()}"
            amount = _minor_to_amount(int(plan["provider_price_minor"]))
            currency = _normalized_currency(plan["provider_currency"])
            cursor.execute(
                """
                insert into subscription_orders (
                    order_no, user_id, plan_id, plan_name_snapshot,
                    duration_months_snapshot, order_amount, paid_amount, currency,
                    order_status, pay_channel, created_by, remark
                ) values (%s, %s, %s, %s, %s, %s, 0, %s,
                    'pending', %s, null, %s)
                """,
                (
                    order_no,
                    int(user_id),
                    int(plan_id),
                    plan["plan_name"],
                    int(plan["duration_months"]),
                    amount,
                    currency,
                    config.channel_name,
                    f"Creem {config.environment} 自助订阅；等待签名回调确认",
                ),
            )
            order_id = int(cursor.lastrowid)
            cursor.execute(
                """
                insert into payment_checkout_sessions (
                    order_id, provider, environment, request_id, status,
                    metadata_json
                ) values (%s, 'creem', %s, %s, 'creating', %s)
                """,
                (
                    order_id,
                    config.environment,
                    order_no,
                    _json_text(
                        {
                            "order_no": order_no,
                            "user_id": str(user_id),
                            "plan_id": str(plan_id),
                            "environment": config.environment,
                        }
                    ),
                ),
            )
        connection.commit()

        payload = {
            "product_id": plan["external_product_id"],
            "request_id": order_no,
            "units": 1,
            "success_url": _success_url_for_order(config.success_url, order_no),
            "metadata": {
                "order_no": order_no,
                "user_id": str(user_id),
                "plan_id": str(plan_id),
                "environment": config.environment,
            },
        }
        response = requests.post(
            f"{config.base_url}/checkouts",
            headers={
                "x-api-key": config.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
            timeout=(10, 30),
        )
        response.raise_for_status()
        response_payload = response.json()
        checkout = response_payload.get("checkout", response_payload)
        checkout_id = str(checkout.get("id") or "").strip()
        checkout_url = str(
            checkout.get("checkout_url")
            or checkout.get("checkoutUrl")
            or response_payload.get("checkout_url")
            or ""
        ).strip()
        if not checkout_id or not checkout_url:
            raise PaymentGatewayError("Creem 未返回有效的结账编号或支付地址")

        with connection.cursor() as cursor:
            cursor.execute(
                """
                update payment_checkout_sessions
                set checkout_id = %s,
                    checkout_url = %s,
                    status = 'ready',
                    metadata_json = %s
                where order_id = %s
                """,
                (
                    checkout_id,
                    checkout_url,
                    _json_text(response_payload),
                    order_id,
                ),
            )
        connection.commit()
        return {
            "order_no": order_no,
            "checkout_id": checkout_id,
            "checkout_url": checkout_url,
            "environment": config.environment,
            "reused": False,
        }
    except (requests.RequestException, ValueError, PaymentError, json.JSONDecodeError) as exc:
        connection.rollback()
        if order_id:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        update payment_checkout_sessions
                        set status = 'failed'
                        where order_id = %s
                        """,
                        (order_id,),
                    )
                    cursor.execute(
                        """
                        update subscription_orders
                        set order_status = 'closed',
                            remark = concat(coalesce(remark, ''), '；创建结账失败')
                        where id = %s and order_status = 'pending'
                        """,
                        (order_id,),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                LOGGER.exception("记录 Creem 结账失败状态时发生异常")
        if isinstance(exc, (ValueError, PaymentError)):
            raise
        LOGGER.exception("Creem 创建结账失败")
        raise PaymentGatewayError("支付服务暂时无法创建结账，请稍后重试") from exc
    finally:
        connection.close()


def process_creem_webhook(raw_body: bytes, signature: str) -> WebhookResult:
    if len(raw_body) > 1024 * 1024:
        raise PaymentValidationError("回调内容超过允许大小")
    config = load_creem_config(require_webhook_secret=True)
    if not verify_creem_signature(raw_body, signature, config.webhook_secret):
        raise PaymentSignatureError("Creem 回调签名校验失败")
    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaymentValidationError("Creem 回调不是有效的 UTF-8 JSON") from exc
    if not isinstance(event, dict):
        raise PaymentValidationError("Creem 回调内容格式无效")

    event_type = str(event.get("eventType") or event.get("type") or "").strip()
    event_id = str(event.get("id") or "").strip() or hashlib.sha256(raw_body).hexdigest()
    payload_hash = hashlib.sha256(raw_body).hexdigest()
    acquisition = _acquire_webhook_event(
        config,
        event_id,
        event_type or "unknown",
        payload_hash,
        event,
        signature,
    )
    if acquisition in {"processed", "ignored", "processing"}:
        return WebhookResult(
            event_id=event_id,
            event_type=event_type or "unknown",
            status="duplicate",
            message="该回调已接收，无需重复处理",
        )

    try:
        handled = _dispatch_creem_event(event_type, event, config)
        final_status = "processed" if handled else "ignored"
        _finish_webhook_event(config, event_id, final_status, "")
        return WebhookResult(
            event_id=event_id,
            event_type=event_type or "unknown",
            status=final_status,
            message="回调处理完成" if handled else "事件已记录，无需变更会员状态",
        )
    except Exception as exc:
        LOGGER.exception("处理 Creem 回调失败：%s", event_type)
        _finish_webhook_event(config, event_id, "failed", str(exc)[:1000])
        raise


def _acquire_webhook_event(
    config: CreemConfig,
    event_id: str,
    event_type: str,
    payload_hash: str,
    event: dict[str, Any],
    signature: str,
) -> str:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                insert ignore into payment_webhook_events (
                    provider, environment, event_id, event_type,
                    payload_sha256, payload_json, signature, process_status
                ) values ('creem', %s, %s, %s, %s, %s, %s, 'received')
                """,
                (
                    config.environment,
                    event_id,
                    event_type,
                    payload_hash,
                    _json_text(event),
                    str(signature or "")[:255],
                ),
            )
            cursor.execute(
                """
                select process_status, last_attempt_at, payload_sha256
                from payment_webhook_events
                where provider = 'creem' and environment = %s and event_id = %s
                for update
                """,
                (config.environment, event_id),
            )
            row = cursor.fetchone()
            if str(row.get("payload_sha256") or "") != payload_hash:
                raise PaymentValidationError("同一 Creem 回调编号对应了不同内容")
            status = str(row.get("process_status") or "received")
            last_attempt = row.get("last_attempt_at")
            if status in {"processed", "ignored"}:
                connection.commit()
                return status
            if (
                status == "processing"
                and last_attempt
                and last_attempt > _utcnow_naive() - timedelta(minutes=5)
            ):
                connection.commit()
                return "processing"
            cursor.execute(
                """
                update payment_webhook_events
                set process_status = 'processing',
                    attempts = attempts + 1,
                    last_attempt_at = UTC_TIMESTAMP(),
                    error_message = null
                where provider = 'creem' and environment = %s and event_id = %s
                """,
                (config.environment, event_id),
            )
        connection.commit()
        return "acquired"
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _finish_webhook_event(
    config: CreemConfig,
    event_id: str,
    status: str,
    error_message: str,
) -> None:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                update payment_webhook_events
                set process_status = %s,
                    error_message = %s,
                    processed_at = case
                        when %s in ('processed', 'ignored') then UTC_TIMESTAMP()
                        else null
                    end
                where provider = 'creem' and environment = %s and event_id = %s
                """,
                (
                    status,
                    error_message or None,
                    status,
                    config.environment,
                    event_id,
                ),
            )
        connection.commit()
    finally:
        connection.close()


def _event_object(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("object")
    if not isinstance(value, dict):
        value = event.get("data")
    if isinstance(value, dict) and isinstance(value.get("object"), dict):
        value = value["object"]
    if not isinstance(value, dict):
        raise PaymentValidationError("Creem 回调缺少 object 数据")
    return value


def _dispatch_creem_event(
    event_type: str,
    event: dict[str, Any],
    config: CreemConfig,
) -> bool:
    if event_type not in CREEM_EVENT_TYPES:
        return False
    obj = _event_object(event)
    if event_type == "checkout.completed":
        _handle_checkout_completed(obj, config)
        return True
    if event_type == "subscription.paid":
        _handle_subscription_paid(obj, config)
        return True
    if event_type.startswith("subscription."):
        _sync_subscription(obj, event_type.split(".", 1)[1], config)
        return True
    if event_type in {"refund.created", "dispute.created"}:
        _flag_payment_review(obj, event_type, config)
        return True
    return False


def _metadata(obj: dict[str, Any]) -> dict[str, Any]:
    value = obj.get("metadata")
    return value if isinstance(value, dict) else {}


def _nested_id(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or "").strip()
    return str(value or "").strip()


def _product_details(obj: dict[str, Any]) -> tuple[str, int | None, str]:
    product = obj.get("product")
    if not product and isinstance(obj.get("items"), list) and obj["items"]:
        first = obj["items"][0]
        product = first.get("product") if isinstance(first, dict) else None
    product_id = _nested_id(product) or str(obj.get("product_id") or "").strip()
    price: int | None = None
    currency = ""
    if isinstance(product, dict):
        raw_price = product.get("price")
        if raw_price is not None:
            try:
                price = int(raw_price)
            except (TypeError, ValueError):
                price = None
        currency = str(product.get("currency") or "").strip().upper()
    return product_id, price, currency


def _subscription_id(obj: dict[str, Any]) -> str:
    return (
        str(obj.get("id") or "").strip()
        if str(obj.get("object") or "").lower() == "subscription"
        else _nested_id(obj.get("subscription"))
    )


def _handle_checkout_completed(obj: dict[str, Any], config: CreemConfig) -> None:
    metadata = _metadata(obj)
    request_id = str(
        obj.get("request_id")
        or obj.get("requestId")
        or metadata.get("order_no")
        or ""
    ).strip()
    checkout_id = str(obj.get("id") or "").strip()
    if not request_id and not checkout_id:
        raise PaymentValidationError("结账完成回调缺少订单号和结账编号")
    subscription_id = _nested_id(obj.get("subscription"))
    customer_id = _nested_id(obj.get("customer"))
    product_id, _, _ = _product_details(obj)

    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select c.id, c.order_id, o.plan_id
                from payment_checkout_sessions c
                join subscription_orders o on o.id = c.order_id
                where c.provider = 'creem'
                  and c.environment = %s
                  and (c.request_id = %s or c.checkout_id = %s)
                order by c.id desc
                limit 1
                for update
                """,
                (config.environment, request_id, checkout_id),
            )
            checkout = cursor.fetchone()
            if not checkout:
                raise PaymentValidationError("结账完成回调找不到本地订单")
            if product_id:
                cursor.execute(
                    """
                    select external_product_id
                    from payment_provider_products
                    where provider = 'creem' and environment = %s and plan_id = %s
                    """,
                    (config.environment, int(checkout["plan_id"])),
                )
                mapping = cursor.fetchone()
                if not mapping or str(mapping["external_product_id"]) != product_id:
                    raise PaymentValidationError("结账产品与本地套餐映射不一致")
            cursor.execute(
                """
                update payment_checkout_sessions
                set checkout_id = coalesce(nullif(%s, ''), checkout_id),
                    provider_customer_id = coalesce(nullif(%s, ''), provider_customer_id),
                    provider_subscription_id = coalesce(nullif(%s, ''), provider_subscription_id),
                    status = 'completed',
                    metadata_json = %s
                where id = %s
                """,
                (
                    checkout_id,
                    customer_id,
                    subscription_id,
                    _json_text(obj),
                    int(checkout["id"]),
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _mapping_for_product(
    cursor: Any,
    config: CreemConfig,
    product_id: str,
) -> dict[str, Any]:
    if not product_id:
        raise PaymentValidationError("订阅回调缺少 Creem 产品编号")
    cursor.execute(
        """
        select
            m.plan_id,
            m.provider_price_minor,
            m.provider_currency,
            p.plan_name,
            p.duration_months
        from payment_provider_products m
        join subscription_plans p on p.id = m.plan_id
        where m.provider = 'creem'
          and m.environment = %s
          and m.external_product_id = %s
          and m.status = 'active'
          and p.status = 'active'
        limit 1
        """,
        (config.environment, product_id),
    )
    mapping = cursor.fetchone()
    if not mapping:
        raise PaymentValidationError("Creem 产品没有对应的启用套餐")
    return mapping


def _handle_subscription_paid(obj: dict[str, Any], config: CreemConfig) -> None:
    subscription_id = str(obj.get("id") or "").strip()
    transaction_id = str(
        obj.get("last_transaction_id")
        or obj.get("transaction_id")
        or ""
    ).strip()
    if not subscription_id or not transaction_id:
        raise PaymentValidationError("订阅支付回调缺少订阅编号或交易流水号")
    product_id, event_price, event_currency = _product_details(obj)
    if event_price is None or not event_currency:
        raise PaymentValidationError("订阅支付回调缺少产品金额或币种")
    metadata = _metadata(obj)
    customer_id = _nested_id(obj.get("customer"))
    period_start = parse_creem_datetime(
        obj.get("current_period_start_date") or obj.get("current_period_start_at")
    )
    period_end = parse_creem_datetime(
        obj.get("current_period_end_date") or obj.get("current_period_end_at")
    )
    next_transaction = parse_creem_datetime(
        obj.get("next_transaction_date") or obj.get("next_transaction_at")
    )
    if not period_start or not period_end or period_end <= period_start:
        raise PaymentValidationError("订阅支付回调缺少有效的服务周期")

    connection = _connect()
    try:
        with connection.cursor() as cursor:
            mapping = _mapping_for_product(cursor, config, product_id)
            expected_price = int(mapping["provider_price_minor"])
            expected_currency = _normalized_currency(mapping["provider_currency"])
            if event_price != expected_price:
                raise PaymentValidationError("Creem 实付产品金额与本地配置不一致")
            if _normalized_currency(event_currency) != expected_currency:
                raise PaymentValidationError("Creem 实付币种与本地配置不一致")

            cursor.execute(
                """
                select
                    t.id, t.order_id, t.amount, t.status, t.channel,
                    o.user_id, o.plan_id, o.currency
                from payment_transactions t
                join subscription_orders o on o.id = t.order_id
                where t.transaction_no = %s
                limit 1
                """,
                (transaction_id,),
            )
            existing_transaction = cursor.fetchone()

            order = None
            order_no = str(metadata.get("order_no") or "").strip()
            if order_no:
                cursor.execute(
                    """
                    select *
                    from subscription_orders
                    where order_no = %s
                    limit 1
                    for update
                    """,
                    (order_no,),
                )
                order = cursor.fetchone()
            if not order:
                cursor.execute(
                    """
                    select o.*
                    from payment_checkout_sessions c
                    join subscription_orders o on o.id = c.order_id
                    where c.provider = 'creem'
                      and c.environment = %s
                      and c.provider_subscription_id = %s
                    order by c.id desc
                    limit 1
                    for update
                    """,
                    (config.environment, subscription_id),
                )
                order = cursor.fetchone()
            if not order:
                cursor.execute(
                    """
                    select o.*
                    from payment_subscriptions s
                    join subscription_orders o on o.id = s.initial_order_id
                    where s.provider = 'creem'
                      and s.environment = %s
                      and s.provider_subscription_id = %s
                    limit 1
                    for update
                    """,
                    (config.environment, subscription_id),
                )
                order = cursor.fetchone()
            if not order:
                raise PaymentValidationError("订阅支付回调无法匹配本地会员订单")
            if int(order["plan_id"]) != int(mapping["plan_id"]):
                raise PaymentValidationError("支付订单套餐与 Creem 产品映射不一致")

            user_id = int(order["user_id"])
            metadata_user_id = str(metadata.get("user_id") or "").strip()
            if metadata_user_id and metadata_user_id != str(user_id):
                raise PaymentValidationError("支付回调会员编号与本地订单不一致")
            cursor.execute(
                """
                select status, deleted_at, service_started_at, service_expires_at
                from sys_users
                where id = %s
                for update
                """,
                (user_id,),
            )
            account = cursor.fetchone()
            if not account or account.get("deleted_at") is not None:
                raise PaymentValidationError("付款会员账号已删除，需人工核对")
            if str(account.get("status") or "") != "active":
                raise PaymentValidationError("付款会员账号已停用，需人工核对后开通")

            _validate_existing_payment_transaction(
                existing_transaction,
                user_id=user_id,
                plan_id=int(mapping["plan_id"]),
                expected_amount=_minor_to_amount(expected_price),
                expected_currency=expected_currency,
                expected_channel=config.channel_name,
            )
            if existing_transaction:
                connection.commit()
                return

            cursor.execute(
                """
                insert into payment_subscriptions (
                    provider, environment, provider_subscription_id,
                    user_id, plan_id, initial_order_id, provider_customer_id,
                    status, current_period_start_at, current_period_end_at,
                    next_transaction_at, metadata_json
                ) values (
                    'creem', %s, %s, %s, %s, %s, %s,
                    'active', %s, %s, %s, %s
                )
                on duplicate key update
                    user_id = values(user_id),
                    plan_id = values(plan_id),
                    initial_order_id = coalesce(initial_order_id, values(initial_order_id)),
                    provider_customer_id = values(provider_customer_id),
                    status = 'active',
                    current_period_start_at = values(current_period_start_at),
                    current_period_end_at = values(current_period_end_at),
                    next_transaction_at = values(next_transaction_at),
                    metadata_json = values(metadata_json)
                """,
                (
                    config.environment,
                    subscription_id,
                    user_id,
                    int(mapping["plan_id"]),
                    int(order["id"]),
                    customer_id or None,
                    period_start,
                    period_end,
                    next_transaction,
                    _json_text(obj),
                ),
            )

            paid_order = order
            if (
                str(order.get("order_status") or "") == "paid"
                and str(order.get("external_trade_no") or "") != transaction_id
            ):
                now = _utcnow_naive()
                recurring_order_no = (
                    f"RN{now.strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:8].upper()}"
                )
                cursor.execute(
                    """
                    insert into subscription_orders (
                        order_no, user_id, plan_id, plan_name_snapshot,
                        duration_months_snapshot, order_amount, paid_amount, currency,
                        order_status, pay_channel, external_trade_no, paid_at,
                        service_start_at, service_end_at, created_by, remark
                    ) values (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        'paid', %s, %s, UTC_TIMESTAMP(), %s, %s, null, %s
                    )
                    """,
                    (
                        recurring_order_no,
                        user_id,
                        int(mapping["plan_id"]),
                        mapping["plan_name"],
                        int(mapping["duration_months"]),
                        _minor_to_amount(expected_price),
                        _minor_to_amount(expected_price),
                        expected_currency,
                        config.channel_name,
                        transaction_id,
                        period_start,
                        period_end,
                        f"Creem {config.environment} 自动续费",
                    ),
                )
                paid_order = {"id": int(cursor.lastrowid), "order_no": recurring_order_no}
            else:
                cursor.execute(
                    """
                    update subscription_orders
                    set order_status = 'paid',
                        paid_amount = %s,
                        currency = %s,
                        pay_channel = %s,
                        external_trade_no = %s,
                        paid_at = UTC_TIMESTAMP(),
                        service_start_at = %s,
                        service_end_at = %s,
                        remark = concat(coalesce(remark, ''), '；签名回调已确认')
                    where id = %s
                    """,
                    (
                        _minor_to_amount(expected_price),
                        expected_currency,
                        config.channel_name,
                        transaction_id,
                        period_start,
                        period_end,
                        int(order["id"]),
                    ),
                )

            cursor.execute(
                """
                insert into payment_transactions (
                    order_id, transaction_no, transaction_type, channel,
                    amount, status, channel_payload, occurred_at
                ) values (%s, %s, 'payment', %s, %s, 'success', %s, UTC_TIMESTAMP())
                """,
                (
                    int(paid_order["id"]),
                    transaction_id,
                    config.channel_name,
                    _minor_to_amount(expected_price),
                    _json_text(obj),
                ),
            )
            cursor.execute(
                """
                select id
                from subscription_periods
                where order_id = %s and status = 'active'
                limit 1
                """,
                (int(paid_order["id"]),),
            )
            if not cursor.fetchone():
                cursor.execute(
                    """
                    insert into subscription_periods (
                        user_id, order_id, plan_id, start_at, end_at,
                        source_type, status, granted_by, remark
                    ) values (%s, %s, %s, %s, %s, 'order', 'active', null, %s)
                    """,
                    (
                        user_id,
                        int(paid_order["id"]),
                        int(mapping["plan_id"]),
                        period_start,
                        period_end,
                        f"Creem {config.environment} 签名回调自动开通",
                    ),
                )
            existing_end = account.get("service_expires_at")
            effective_end = (
                existing_end
                if existing_end and existing_end > period_end
                else period_end
            )
            cursor.execute(
                """
                update sys_users
                set service_started_at = coalesce(service_started_at, %s),
                    service_expires_at = %s
                where id = %s
                """,
                (period_start, effective_end, user_id),
            )
            cursor.execute(
                """
                update payment_checkout_sessions
                set provider_customer_id = coalesce(nullif(%s, ''), provider_customer_id),
                    provider_subscription_id = %s,
                    status = 'completed'
                where order_id = %s
                """,
                (customer_id, subscription_id, int(order["id"])),
            )
            cursor.execute(
                """
                insert into audit_logs (
                    operator_user_id, action_code, target_type, target_id, after_json
                ) values (
                    null, 'payment.creem.subscription_paid', 'subscription_order', %s,
                    JSON_OBJECT(
                        'order_no', %s,
                        'transaction_id', %s,
                        'subscription_id', %s,
                        'service_end_at', %s
                    )
                )
                """,
                (
                    str(paid_order["id"]),
                    paid_order["order_no"],
                    transaction_id,
                    subscription_id,
                    period_end,
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _sync_subscription(
    obj: dict[str, Any],
    event_status: str,
    config: CreemConfig,
) -> None:
    subscription_id = str(obj.get("id") or "").strip()
    if not subscription_id:
        raise PaymentValidationError("订阅状态回调缺少订阅编号")
    product_id, _, _ = _product_details(obj)
    metadata = _metadata(obj)
    customer_id = _nested_id(obj.get("customer"))
    period_start = parse_creem_datetime(
        obj.get("current_period_start_date") or obj.get("current_period_start_at")
    )
    period_end = parse_creem_datetime(
        obj.get("current_period_end_date") or obj.get("current_period_end_at")
    )
    next_transaction = parse_creem_datetime(
        obj.get("next_transaction_date") or obj.get("next_transaction_at")
    )
    canceled_at = parse_creem_datetime(
        obj.get("canceled_at") or obj.get("cancelled_at")
    )
    normalized_status = event_status.replace("cancelled", "canceled")

    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select *
                from payment_subscriptions
                where provider = 'creem'
                  and environment = %s
                  and provider_subscription_id = %s
                limit 1
                for update
                """,
                (config.environment, subscription_id),
            )
            existing = cursor.fetchone()
            if existing:
                cursor.execute(
                    """
                    update payment_subscriptions
                    set provider_customer_id = coalesce(nullif(%s, ''), provider_customer_id),
                        status = %s,
                        current_period_start_at = coalesce(%s, current_period_start_at),
                        current_period_end_at = coalesce(%s, current_period_end_at),
                        next_transaction_at = %s,
                        canceled_at = coalesce(%s, canceled_at),
                        metadata_json = %s
                    where id = %s
                    """,
                    (
                        customer_id,
                        normalized_status,
                        period_start,
                        period_end,
                        next_transaction,
                        canceled_at,
                        _json_text(obj),
                        int(existing["id"]),
                    ),
                )
                connection.commit()
                return

            order_no = str(metadata.get("order_no") or "").strip()
            user_id_text = str(metadata.get("user_id") or "").strip()
            if not order_no or not user_id_text or not product_id:
                connection.commit()
                return
            mapping = _mapping_for_product(cursor, config, product_id)
            cursor.execute(
                """
                select id, user_id, plan_id
                from subscription_orders
                where order_no = %s
                limit 1
                """,
                (order_no,),
            )
            order = cursor.fetchone()
            if (
                not order
                or str(order["user_id"]) != user_id_text
                or int(order["plan_id"]) != int(mapping["plan_id"])
            ):
                raise PaymentValidationError("订阅状态回调与本地订单不一致")
            cursor.execute(
                """
                insert into payment_subscriptions (
                    provider, environment, provider_subscription_id,
                    user_id, plan_id, initial_order_id, provider_customer_id,
                    status, current_period_start_at, current_period_end_at,
                    next_transaction_at, canceled_at, metadata_json
                ) values (
                    'creem', %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    config.environment,
                    subscription_id,
                    int(order["user_id"]),
                    int(order["plan_id"]),
                    int(order["id"]),
                    customer_id or None,
                    normalized_status,
                    period_start,
                    period_end,
                    next_transaction,
                    canceled_at,
                    _json_text(obj),
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _flag_payment_review(
    obj: dict[str, Any],
    event_type: str,
    config: CreemConfig,
) -> None:
    subscription_id = _nested_id(obj.get("subscription"))
    transaction_id = _nested_id(obj.get("transaction")) or str(
        obj.get("transaction_id") or ""
    ).strip()
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            if subscription_id:
                cursor.execute(
                    """
                    update payment_subscriptions
                    set status = %s,
                        metadata_json = %s
                    where provider = 'creem'
                      and environment = %s
                      and provider_subscription_id = %s
                    """,
                    (
                        "disputed" if event_type == "dispute.created" else "refund_review",
                        _json_text(obj),
                        config.environment,
                        subscription_id,
                    ),
                )
            if transaction_id:
                cursor.execute(
                    """
                    update payment_transactions
                    set status = %s,
                        channel_payload = %s
                    where transaction_no = %s
                    """,
                    (
                        "disputed" if event_type == "dispute.created" else "refund_review",
                        _json_text(obj),
                        transaction_id,
                    ),
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
