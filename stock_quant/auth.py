from __future__ import annotations

import calendar
import json
import math
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any


MOBILE_PATTERN = re.compile(r"^\+?[0-9]{7,20}$")


def auth_enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _connect():
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("服务器依赖未安装，请执行 pip install -r requirements-server.txt") from exc
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "stock_quant_app"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "stock_quant_saas"),
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=10,
        read_timeout=20,
        write_timeout=20,
        cursorclass=pymysql.cursors.DictCursor,
    )


def add_months(value: datetime, months: int) -> datetime:
    if months <= 0:
        raise ValueError("开通月数必须大于0")
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_service_valid(user: dict[str, Any], now: datetime | None = None) -> bool:
    return _service_status(user, now) == "active"


def _service_status(user: dict[str, Any], now: datetime | None = None) -> str:
    current = now or _utcnow_naive()
    if user.get("deleted_at") is not None:
        return "deleted"
    if user.get("status") != "active":
        return "disabled"

    start = user.get("service_started_at")
    end = user.get("service_expires_at")
    if start is None or end is None:
        return "not_opened"
    if start > current:
        return "not_started"
    if end <= current:
        return "expired"
    return "active"


def _user_query(identifier_clause: str) -> str:
    return f"""
        select
            u.*,
            (
                select group_concat(distinct r.role_code order by r.role_code)
                from sys_user_roles ur
                join sys_roles r on r.id = ur.role_id
                where ur.user_id = u.id
            ) as role_codes
        from sys_users u
        where {identifier_clause}
    """


def _public_user(row: dict[str, Any]) -> dict[str, Any]:
    roles = [value for value in str(row.get("role_codes") or "").split(",") if value]
    service_status = _service_status(row)
    return {
        "id": int(row["id"]),
        "login_name": row.get("login_name"),
        "mobile": row.get("mobile"),
        "real_name": row.get("real_name"),
        "status": row.get("status"),
        "service_started_at": row.get("service_started_at"),
        "service_expires_at": row.get("service_expires_at"),
        "roles": roles,
        "is_admin": "ADMIN" in roles,
        "service_status": service_status,
        "service_valid": service_status == "active",
    }


def authenticate(identifier: str, password: str, ip_address: str | None = None) -> dict[str, Any]:
    try:
        import bcrypt
    except ImportError as exc:
        raise RuntimeError("服务器依赖未安装，请执行 pip install -r requirements-server.txt") from exc

    login_identifier = identifier.strip()
    if not login_identifier or not password:
        raise ValueError("请输入手机号/登录名和密码")

    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                _user_query("(u.mobile = %s or u.login_name = %s)"),
                (login_identifier, login_identifier),
            )
            user = cursor.fetchone()
            now = _utcnow_naive()
            failure_reason = None
            if not user:
                failure_reason = "账号或密码错误"
            elif user.get("locked_until") and user["locked_until"] > now:
                failure_reason = "账号暂时锁定，请稍后重试"
            elif not bcrypt.checkpw(
                password.encode("utf-8"),
                str(user["password_hash"]).encode("ascii"),
            ):
                failure_reason = "账号或密码错误"
                attempts = int(user.get("failed_login_count") or 0) + 1
                locked_until = None
                if attempts >= 5:
                    from datetime import timedelta

                    locked_until = now + timedelta(minutes=15)
                    attempts = 0
                cursor.execute(
                    """
                    update sys_users
                    set failed_login_count = %s, locked_until = %s
                    where id = %s
                    """,
                    (attempts, locked_until, user["id"]),
                )
            elif _service_status(user, now) in {"disabled", "deleted"}:
                failure_reason = "账号已被停用，请联系管理员"

            cursor.execute(
                """
                insert into login_logs (
                    user_id, login_identifier, success, failure_reason, ip_address
                ) values (%s, %s, %s, %s, %s)
                """,
                (
                    user["id"] if user else None,
                    login_identifier,
                    int(failure_reason is None),
                    failure_reason,
                    ip_address,
                ),
            )
            if failure_reason:
                connection.commit()
                raise ValueError(failure_reason)

            cursor.execute(
                """
                update sys_users
                set failed_login_count = 0, locked_until = null,
                    last_login_at = UTC_TIMESTAMP(), last_login_ip = %s
                where id = %s
                """,
                (ip_address, user["id"]),
            )
            cursor.execute(
                """
                insert into user_daily_usage (user_id, usage_date, login_count, last_activity_at)
                values (%s, DATE(CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+08:00')), 1, UTC_TIMESTAMP())
                on duplicate key update
                    login_count = login_count + 1,
                    last_activity_at = UTC_TIMESTAMP()
                """,
                (user["id"],),
            )
        connection.commit()
        return _public_user(user)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def refresh_user(user_id: int) -> dict[str, Any] | None:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(_user_query("u.id = %s"), (user_id,))
            user = cursor.fetchone()
        if (
            not user
            or user.get("deleted_at") is not None
            or user.get("status") != "active"
        ):
            return None
        return _public_user(user)
    finally:
        connection.close()


def list_accounts() -> list[dict[str, Any]]:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select v.*, roles.role_codes
                from v_user_account_status v
                left join (
                    select
                        ur.user_id,
                        group_concat(distinct r.role_code order by r.role_code) as role_codes
                    from sys_user_roles ur
                    join sys_roles r on r.id = ur.role_id
                    group by ur.user_id
                ) roles on roles.user_id = v.id
                order by v.created_at desc
                """
            )
            return list(cursor.fetchall())
    finally:
        connection.close()


def list_subscription_periods(user_id: int | None = None, limit: int = 300) -> list[dict[str, Any]]:
    """Return manual/opened renewal periods for admin operation review."""
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            params: list[Any] = []
            where = ""
            if user_id is not None:
                where = "where p.user_id = %s"
                params.append(int(user_id))
            params.append(int(limit))
            cursor.execute(
                f"""
                select
                    p.id,
                    p.user_id,
                    u.mobile,
                    u.real_name,
                    p.start_at,
                    p.end_at,
                    p.source_type,
                    p.status,
                    p.remark,
                    p.created_at,
                    operator.real_name as granted_by_name,
                    operator.mobile as granted_by_mobile
                from subscription_periods p
                join sys_users u on u.id = p.user_id
                left join sys_users operator on operator.id = p.granted_by
                {where}
                order by p.created_at desc, p.id desc
                limit %s
                """,
                params,
            )
            return list(cursor.fetchall())
    finally:
        connection.close()


def list_user_query_records(user_id: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
    """Return recent user query records for usage and renewal analysis."""
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            params: list[Any] = []
            where = ""
            if user_id is not None:
                where = "where q.user_id = %s"
                params.append(int(user_id))
            params.append(int(limit))
            cursor.execute(
                f"""
                select
                    q.id,
                    q.user_id,
                    u.mobile,
                    u.real_name,
                    q.query_date,
                    q.query_type,
                    q.stock_symbol,
                    q.stock_name,
                    q.result_status,
                    q.duration_ms,
                    q.created_at
                from user_query_records q
                join sys_users u on u.id = q.user_id
                {where}
                order by q.created_at desc, q.id desc
                limit %s
                """,
                params,
            )
            return list(cursor.fetchall())
    finally:
        connection.close()


def list_daily_usage(user_id: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
    """Return recent daily usage counters."""
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            params: list[Any] = []
            where = ""
            if user_id is not None:
                where = "where d.user_id = %s"
                params.append(int(user_id))
            params.append(int(limit))
            cursor.execute(
                f"""
                select
                    d.user_id,
                    u.mobile,
                    u.real_name,
                    d.usage_date,
                    d.login_count,
                    d.query_count,
                    d.stock_analysis_count,
                    d.recommendation_view_count,
                    d.hotspot_view_count,
                    d.export_count,
                    d.last_activity_at
                from user_daily_usage d
                join sys_users u on u.id = d.user_id
                {where}
                order by d.usage_date desc, d.query_count desc, d.user_id desc
                limit %s
                """,
                params,
            )
            return list(cursor.fetchall())
    finally:
        connection.close()


def list_login_logs(user_id: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
    """Return recent login activity for retention and risk review."""
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            params: list[Any] = []
            where = ""
            if user_id is not None:
                where = "where l.user_id = %s"
                params.append(int(user_id))
            params.append(int(limit))
            cursor.execute(
                f"""
                select
                    l.id,
                    l.user_id,
                    u.mobile,
                    u.real_name,
                    l.login_identifier,
                    l.success,
                    l.failure_reason,
                    l.ip_address,
                    l.created_at
                from login_logs l
                left join sys_users u on u.id = l.user_id
                {where}
                order by l.created_at desc, l.id desc
                limit %s
                """,
                params,
            )
            return list(cursor.fetchall())
    finally:
        connection.close()


def list_subscription_plans(include_disabled: bool = True) -> list[dict[str, Any]]:
    """Return purchasable membership plans."""
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            where = "" if include_disabled else "where status = 'active'"
            cursor.execute(
                f"""
                select
                    id,
                    plan_code,
                    plan_name,
                    duration_months,
                    price,
                    currency,
                    daily_query_limit,
                    features_json,
                    status,
                    created_at,
                    updated_at
                from subscription_plans
                {where}
                order by status = 'active' desc, price asc, id desc
                """
            )
            return list(cursor.fetchall())
    finally:
        connection.close()


def save_subscription_plan(
    plan_code: str,
    plan_name: str,
    duration_months: int,
    price: float,
    daily_query_limit: int | None,
    features: list[str] | None = None,
    status: str = "active",
) -> int:
    """Create or update a membership plan."""
    normalized_code = plan_code.strip()
    normalized_name = plan_name.strip()
    if not normalized_code or not normalized_name:
        raise ValueError("套餐编码和套餐名称不能为空")
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,64}", normalized_code):
        raise ValueError("套餐编码只能包含字母、数字、下划线和短横线")
    if duration_months <= 0:
        raise ValueError("套餐时长必须大于0个月")
    if price < 0:
        raise ValueError("套餐价格不能小于0")
    if status not in {"active", "disabled"}:
        raise ValueError("套餐状态只能是启用或停用")

    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                insert into subscription_plans (
                    plan_code, plan_name, duration_months, price, currency,
                    daily_query_limit, features_json, status
                ) values (%s, %s, %s, %s, 'CNY', %s, %s, %s)
                on duplicate key update
                    plan_name = values(plan_name),
                    duration_months = values(duration_months),
                    price = values(price),
                    daily_query_limit = values(daily_query_limit),
                    features_json = values(features_json),
                    status = values(status)
                """,
                (
                    normalized_code,
                    normalized_name,
                    int(duration_months),
                    float(price),
                    int(daily_query_limit) if daily_query_limit is not None else None,
                    json.dumps(features or [], ensure_ascii=False),
                    status,
                ),
            )
            cursor.execute("select id from subscription_plans where plan_code = %s", (normalized_code,))
            row = cursor.fetchone()
        connection.commit()
        return int(row["id"])
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_subscription_orders(limit: int = 500) -> list[dict[str, Any]]:
    """Return membership order流水."""
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select
                    o.id,
                    o.order_no,
                    o.user_id,
                    u.mobile,
                    u.real_name,
                    o.plan_id,
                    o.plan_name_snapshot,
                    o.duration_months_snapshot,
                    o.order_amount,
                    o.paid_amount,
                    o.currency,
                    o.order_status,
                    o.pay_channel,
                    o.external_trade_no,
                    o.paid_at,
                    o.service_start_at,
                    o.service_end_at,
                    o.remark,
                    o.created_at
                from subscription_orders o
                join sys_users u on u.id = o.user_id
                order by o.created_at desc, o.id desc
                limit %s
                """,
                (int(limit),),
            )
            return list(cursor.fetchall())
    finally:
        connection.close()


def create_manual_order(
    user_id: int,
    plan_id: int,
    paid_amount: float,
    pay_channel: str,
    operator_user_id: int,
    remark: str = "",
    mark_paid: bool = True,
) -> str:
    """Create an offline order and, when paid, extend the user's service period."""
    if int(user_id) <= 0 or int(plan_id) <= 0 or int(operator_user_id) <= 0:
        raise ValueError("订单账号、套餐和操作员编号必须有效")
    normalized_channel = str(pay_channel or "").strip()
    normalized_remark = str(remark or "").strip()
    try:
        normalized_paid_amount = float(paid_amount)
    except (TypeError, ValueError) as exc:
        raise ValueError("实收金额必须是有效数字") from exc
    if not math.isfinite(normalized_paid_amount) or normalized_paid_amount < 0:
        raise ValueError("实收金额必须是大于或等于0的有限数字")
    if mark_paid and not normalized_channel:
        raise ValueError("已支付订单必须填写收款方式")
    if not mark_paid and normalized_paid_amount != 0:
        raise ValueError("待支付订单的实收金额必须为0")

    now = _utcnow_naive()
    order_no = f"SQ{now.strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:8].upper()}"
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("select * from subscription_plans where id = %s for update", (int(plan_id),))
            plan = cursor.fetchone()
            if not plan:
                raise ValueError("套餐不存在")
            if str(plan.get("status") or "") != "active":
                raise ValueError("套餐当前未启用")
            order_amount = float(plan["price"])
            if (
                mark_paid
                and abs(normalized_paid_amount - order_amount) > 0.01
                and normalized_channel not in {"赠送", "其他"}
                and not normalized_remark
            ):
                raise ValueError("实收金额与套餐价格不一致时，请在备注中说明折扣或调整原因")
            cursor.execute(
                """
                select status, deleted_at, service_expires_at
                from sys_users
                where id = %s
                for update
                """,
                (int(user_id),),
            )
            target = cursor.fetchone()
            if not target:
                raise ValueError("会员账号不存在")
            if target.get("deleted_at") is not None:
                raise ValueError("已删除的会员账号不能创建订单")
            status = "paid" if mark_paid else "pending"
            service_start = None
            service_end = None
            if mark_paid:
                service_start = target.get("service_expires_at") if target.get("service_expires_at") and target["service_expires_at"] > now else now
                service_end = add_months(service_start, int(plan["duration_months"]))
            cursor.execute(
                """
                insert into subscription_orders (
                    order_no, user_id, plan_id, plan_name_snapshot,
                    duration_months_snapshot, order_amount, paid_amount, currency,
                    order_status, pay_channel, paid_at, service_start_at,
                    service_end_at, created_by, remark
                ) values (%s, %s, %s, %s, %s, %s, %s, 'CNY', %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    order_no,
                    int(user_id),
                    int(plan_id),
                    plan["plan_name"],
                    int(plan["duration_months"]),
                    order_amount,
                    normalized_paid_amount,
                    status,
                    normalized_channel or "线下",
                    now if mark_paid else None,
                    service_start,
                    service_end,
                    int(operator_user_id),
                    normalized_remark,
                ),
            )
            order_id = int(cursor.lastrowid)
            if mark_paid:
                cursor.execute(
                    """
                    insert into payment_transactions (
                        order_id, transaction_no, transaction_type, channel,
                        amount, status, occurred_at
                    ) values (%s, %s, 'payment', %s, %s, 'success', %s)
                    """,
                    (
                        order_id,
                        f"PAY{order_no}",
                        normalized_channel or "线下",
                        normalized_paid_amount,
                        now,
                    ),
                )
                cursor.execute(
                    """
                    update sys_users
                    set status = 'active',
                        service_started_at = coalesce(service_started_at, %s),
                        service_expires_at = %s,
                        deleted_at = null
                    where id = %s
                    """,
                    (service_start, service_end, int(user_id)),
                )
                cursor.execute(
                    """
                    insert into subscription_periods (
                        user_id, order_id, plan_id, start_at, end_at,
                        source_type, granted_by, remark
                    ) values (%s, %s, %s, %s, %s, 'order', %s, %s)
                    """,
                    (
                        int(user_id),
                        order_id,
                        int(plan_id),
                        service_start,
                        service_end,
                        int(operator_user_id),
                        normalized_remark,
                    ),
                )
            cursor.execute(
                """
                insert into audit_logs (
                    operator_user_id, action_code, target_type, target_id, after_json
                ) values (%s, 'order.create', 'subscription_order', %s,
                    JSON_OBJECT('order_no', %s, 'user_id', %s, 'plan_id', %s, 'paid_amount', %s))
                """,
                (
                    int(operator_user_id),
                    str(order_id),
                    order_no,
                    int(user_id),
                    int(plan_id),
                    normalized_paid_amount,
                ),
            )
        connection.commit()
        return order_no
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_payment_transactions(limit: int = 500) -> list[dict[str, Any]]:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select
                    t.id,
                    o.order_no,
                    u.mobile,
                    u.real_name,
                    t.transaction_no,
                    t.transaction_type,
                    t.channel,
                    t.amount,
                    t.status,
                    t.occurred_at,
                    t.created_at
                from payment_transactions t
                join subscription_orders o on o.id = t.order_id
                join sys_users u on u.id = o.user_id
                order by t.created_at desc, t.id desc
                limit %s
                """,
                (int(limit),),
            )
            return list(cursor.fetchall())
    finally:
        connection.close()


def ensure_notification_tables() -> None:
    """Create optional notification settings table for P2 channel configuration."""
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                create table if not exists notification_channels (
                    id bigint unsigned not null auto_increment,
                    channel_code varchar(40) not null,
                    channel_name varchar(100) not null,
                    provider varchar(40) not null,
                    webhook_url varchar(1000) null,
                    enabled tinyint(1) not null default 0,
                    usage_scene varchar(200) null,
                    remark varchar(500) null,
                    created_at datetime not null default current_timestamp,
                    updated_at datetime not null default current_timestamp on update current_timestamp,
                    primary key (id),
                    unique key uk_notification_channels_code (channel_code),
                    key idx_notification_channels_enabled (enabled, provider)
                ) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci
                """
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_notification_channels() -> list[dict[str, Any]]:
    ensure_notification_tables()
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select id, channel_code, channel_name, provider, webhook_url,
                       enabled, usage_scene, remark, created_at, updated_at
                from notification_channels
                order by enabled desc, updated_at desc, id desc
                """
            )
            return list(cursor.fetchall())
    finally:
        connection.close()


def save_notification_channel(
    channel_code: str,
    channel_name: str,
    provider: str,
    webhook_url: str,
    enabled: bool,
    usage_scene: str = "",
    remark: str = "",
) -> int:
    ensure_notification_tables()
    normalized_code = channel_code.strip()
    if not normalized_code or not channel_name.strip():
        raise ValueError("通道编码和名称不能为空")
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,40}", normalized_code):
        raise ValueError("通道编码只能包含字母、数字、下划线和短横线")
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                insert into notification_channels (
                    channel_code, channel_name, provider, webhook_url,
                    enabled, usage_scene, remark
                ) values (%s, %s, %s, %s, %s, %s, %s)
                on duplicate key update
                    channel_name = values(channel_name),
                    provider = values(provider),
                    webhook_url = values(webhook_url),
                    enabled = values(enabled),
                    usage_scene = values(usage_scene),
                    remark = values(remark)
                """,
                (
                    normalized_code,
                    channel_name.strip(),
                    provider.strip(),
                    webhook_url.strip(),
                    int(bool(enabled)),
                    usage_scene.strip(),
                    remark.strip(),
                ),
            )
            cursor.execute("select id from notification_channels where channel_code = %s", (normalized_code,))
            row = cursor.fetchone()
        connection.commit()
        return int(row["id"])
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def create_member(
    mobile: str,
    real_name: str,
    password: str,
    months: int,
    operator_user_id: int,
    remark: str = "",
) -> int:
    try:
        import bcrypt
    except ImportError as exc:
        raise RuntimeError("服务器依赖未安装，请执行 pip install -r requirements-server.txt") from exc
    normalized_mobile = mobile.strip()
    normalized_name = real_name.strip()
    normalized_remark = remark.strip()
    if not normalized_mobile or not normalized_name:
        raise ValueError("手机号和姓名不能为空")
    if not MOBILE_PATTERN.fullmatch(normalized_mobile):
        raise ValueError("手机号格式不正确，应为7至20位数字，可使用国际区号前缀+")
    if len(normalized_name) > 64:
        raise ValueError("姓名不能超过64个字符")
    if len(password) < 8 or not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
        raise ValueError("会员初始密码至少8位，并同时包含字母和数字")
    if len(normalized_remark) > 500:
        raise ValueError("备注不能超过500个字符")

    now = _utcnow_naive()
    end_at = add_months(now, months)
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("select id from sys_users where mobile = %s for update", (normalized_mobile,))
            if cursor.fetchone():
                raise ValueError("该手机号已经存在")
            cursor.execute(
                """
                insert into sys_users (
                    mobile, real_name, password_hash, status,
                    service_started_at, service_expires_at, password_changed_at,
                    remark, created_by
                ) values (%s, %s, %s, 'active', %s, %s, %s, %s, %s)
                """,
                (
                    normalized_mobile,
                    normalized_name,
                    password_hash,
                    now,
                    end_at,
                    now,
                    normalized_remark,
                    operator_user_id,
                ),
            )
            user_id = int(cursor.lastrowid)
            cursor.execute(
                """
                insert into sys_user_roles (user_id, role_id, granted_by)
                select %s, id, %s from sys_roles where role_code = 'MEMBER'
                """,
                (user_id, operator_user_id),
            )
            cursor.execute(
                """
                insert into subscription_periods (
                    user_id, start_at, end_at, source_type, granted_by, remark
                ) values (%s, %s, %s, 'manual', %s, %s)
                """,
                (user_id, now, end_at, operator_user_id, normalized_remark),
            )
            cursor.execute(
                """
                insert into audit_logs (
                    operator_user_id, action_code, target_type, target_id, after_json
                ) values (%s, 'account.create', 'user', %s,
                    JSON_OBJECT('mobile', %s, 'months', %s, 'service_expires_at', %s))
                """,
                (operator_user_id, str(user_id), normalized_mobile, months, end_at),
            )
        connection.commit()
        return user_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def renew_member(user_id: int, months: int, operator_user_id: int, remark: str = "") -> datetime:
    now = _utcnow_naive()
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "select service_expires_at from sys_users where id = %s for update",
                (user_id,),
            )
            user = cursor.fetchone()
            if not user:
                raise ValueError("账号不存在")
            current_end = user.get("service_expires_at")
            start_at = current_end if current_end and current_end > now else now
            end_at = add_months(start_at, months)
            cursor.execute(
                """
                update sys_users
                set status = 'active',
                    service_started_at = coalesce(service_started_at, %s),
                    service_expires_at = %s,
                    deleted_at = null
                where id = %s
                """,
                (start_at, end_at, user_id),
            )
            cursor.execute(
                """
                insert into subscription_periods (
                    user_id, start_at, end_at, source_type, granted_by, remark
                ) values (%s, %s, %s, 'manual', %s, %s)
                """,
                (user_id, start_at, end_at, operator_user_id, remark.strip()),
            )
            cursor.execute(
                """
                insert into audit_logs (
                    operator_user_id, action_code, target_type, target_id, after_json
                ) values (%s, 'subscription.renew', 'user', %s,
                    JSON_OBJECT('months', %s, 'service_expires_at', %s))
                """,
                (operator_user_id, str(user_id), months, end_at),
            )
        connection.commit()
        return end_at
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def set_account_status(user_id: int, status: str, operator_user_id: int) -> None:
    if status not in {"active", "disabled"}:
        raise ValueError("不支持的账号状态")
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "update sys_users set status = %s where id = %s",
                (status, user_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("账号不存在")
            cursor.execute(
                """
                insert into audit_logs (
                    operator_user_id, action_code, target_type, target_id, after_json
                ) values (%s, 'account.status', 'user', %s, JSON_OBJECT('status', %s))
                """,
                (operator_user_id, str(user_id), status),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def record_user_query(
    user_id: int,
    query_type: str,
    stock_symbol: str | None = None,
    stock_name: str | None = None,
    request_params: dict[str, Any] | None = None,
    result_snapshot: dict[str, Any] | None = None,
) -> None:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                insert into user_query_records (
                    request_id, user_id, query_date, query_type, stock_symbol,
                    stock_name, request_params_json, result_snapshot_json,
                    result_status, created_at
                ) values (
                    %s, %s, DATE(CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+08:00')),
                    %s, %s, %s, %s, %s, 'success', UTC_TIMESTAMP()
                )
                """,
                (
                    str(uuid.uuid4()),
                    user_id,
                    query_type,
                    stock_symbol,
                    stock_name,
                    json.dumps(request_params or {}, ensure_ascii=False),
                    json.dumps(result_snapshot or {}, ensure_ascii=False),
                ),
            )
            stock_increment = 1 if query_type == "stock_analysis" else 0
            recommendation_increment = 1 if query_type.startswith("recommendation") else 0
            cursor.execute(
                """
                insert into user_daily_usage (
                    user_id, usage_date, query_count, stock_analysis_count,
                    recommendation_view_count, last_activity_at
                ) values (
                    %s, DATE(CONVERT_TZ(UTC_TIMESTAMP(), '+00:00', '+08:00')),
                    1, %s, %s, UTC_TIMESTAMP()
                )
                on duplicate key update
                    query_count = query_count + 1,
                    stock_analysis_count = stock_analysis_count + values(stock_analysis_count),
                    recommendation_view_count =
                        recommendation_view_count + values(recommendation_view_count),
                    last_activity_at = UTC_TIMESTAMP()
                """,
                (user_id, stock_increment, recommendation_increment),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _decode_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def load_latest_position_params(user_id: int) -> dict[str, Any]:
    """Return the user's most recently saved position-plan inputs."""
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select request_params_json
                from user_query_records
                where user_id = %s
                  and query_type = 'position_plan'
                  and result_status = 'success'
                order by created_at desc, id desc
                limit 1
                """,
                (user_id,),
            )
            row = cursor.fetchone()
        return _decode_json_object(row.get("request_params_json")) if row else {}
    finally:
        connection.close()
