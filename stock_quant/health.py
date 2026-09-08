from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Mapping


TRUTHY_VALUES = {"1", "true", "yes", "on"}
PRODUCTION_ENVIRONMENTS = {"production", "prod"}
LOCAL_ENVIRONMENTS = {"local", "development", "dev", "test"}


MYSQL_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "sys_users": {
        "id",
        "mobile",
        "login_name",
        "password_hash",
        "real_name",
        "status",
        "service_started_at",
        "service_expires_at",
        "failed_login_count",
        "locked_until",
        "deleted_at",
    },
    "sys_roles": {"id", "role_code", "role_name"},
    "sys_user_roles": {"user_id", "role_id"},
    "subscription_plans": {
        "id",
        "plan_code",
        "plan_name",
        "duration_months",
        "price",
        "daily_query_limit",
        "features_json",
        "status",
    },
    "subscription_orders": {
        "id",
        "order_no",
        "user_id",
        "plan_id",
        "order_amount",
        "paid_amount",
        "order_status",
        "pay_channel",
        "paid_at",
        "service_start_at",
        "service_end_at",
        "created_by",
    },
    "payment_transactions": {
        "id",
        "order_id",
        "transaction_no",
        "transaction_type",
        "channel",
        "amount",
        "status",
        "occurred_at",
    },
    "subscription_periods": {
        "id",
        "user_id",
        "order_id",
        "plan_id",
        "start_at",
        "end_at",
        "source_type",
        "granted_by",
    },
    "login_logs": {
        "id",
        "user_id",
        "login_identifier",
        "success",
        "failure_reason",
        "ip_address",
        "created_at",
    },
    "audit_logs": {
        "id",
        "operator_user_id",
        "action_code",
        "target_type",
        "target_id",
        "created_at",
    },
    "user_query_records": {
        "id",
        "request_id",
        "user_id",
        "query_date",
        "query_type",
        "stock_symbol",
        "stock_name",
        "request_params_json",
        "result_snapshot_json",
        "result_status",
        "duration_ms",
        "created_at",
    },
    "user_stock_analysis_results": {
        "id",
        "query_record_id",
        "user_id",
        "symbol",
        "stock_name",
        "trade_date",
        "created_at",
    },
    "user_daily_usage": {
        "user_id",
        "usage_date",
        "login_count",
        "query_count",
        "stock_analysis_count",
        "recommendation_view_count",
        "hotspot_view_count",
        "export_count",
        "last_activity_at",
    },
}
PAYMENT_MYSQL_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "payment_provider_products": {
        "id",
        "plan_id",
        "provider",
        "environment",
        "external_product_id",
        "provider_price_minor",
        "provider_currency",
        "status",
    },
    "payment_checkout_sessions": {
        "id",
        "order_id",
        "provider",
        "environment",
        "request_id",
        "checkout_id",
        "checkout_url",
        "provider_subscription_id",
        "status",
    },
    "payment_subscriptions": {
        "id",
        "provider",
        "environment",
        "provider_subscription_id",
        "user_id",
        "plan_id",
        "initial_order_id",
        "status",
        "current_period_start_at",
        "current_period_end_at",
    },
    "payment_webhook_events": {
        "id",
        "provider",
        "environment",
        "event_id",
        "event_type",
        "payload_sha256",
        "payload_json",
        "process_status",
        "attempts",
        "received_at",
    },
}
MYSQL_REQUIRED_VIEWS = {"v_user_account_status"}


def is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in TRUTHY_VALUES


def runtime_environment(value: str | None = None) -> str:
    raw = value if value is not None else os.getenv("APP_ENV")
    return str(raw or "").strip().lower()


@dataclass(frozen=True)
class RuntimeSafety:
    failures: tuple[str, ...]
    warnings: tuple[str, ...]


def assess_runtime_security(
    environment: str | None = None,
    auth_is_enabled: bool | None = None,
) -> RuntimeSafety:
    env_name = runtime_environment(environment)
    auth_on = is_truthy(os.getenv("AUTH_ENABLED")) if auth_is_enabled is None else bool(auth_is_enabled)
    failures: list[str] = []
    warnings: list[str] = []

    if not env_name:
        warnings.append("未设置 APP_ENV；本地调试请设为 local，正式服务器请设为 production。")
    elif env_name not in PRODUCTION_ENVIRONMENTS | LOCAL_ENVIRONMENTS:
        failures.append(f"APP_ENV 值不受支持：{env_name}")

    if env_name in PRODUCTION_ENVIRONMENTS and not auth_on:
        failures.append("正式环境禁止关闭会员登录；请设置 AUTH_ENABLED=true。")
    elif env_name in LOCAL_ENVIRONMENTS and auth_on:
        warnings.append("本地环境启用了会员登录，将依赖本机MySQL账号与密码。")
    elif not auth_on:
        warnings.append("会员登录当前未启用；该模式只能用于本机调试，不能直接对外发布。")

    return RuntimeSafety(tuple(failures), tuple(warnings))


def mysql_schema_findings(
    table_names: Iterable[str],
    view_names: Iterable[str],
    columns_by_table: Mapping[str, Iterable[str]],
    include_payment: bool = False,
) -> list[str]:
    tables = {str(item).lower() for item in table_names}
    views = {str(item).lower() for item in view_names}
    normalized_columns = {
        str(table).lower(): {str(column).lower() for column in columns}
        for table, columns in columns_by_table.items()
    }
    findings: list[str] = []

    required_columns = dict(MYSQL_REQUIRED_COLUMNS)
    if include_payment:
        required_columns.update(PAYMENT_MYSQL_REQUIRED_COLUMNS)

    missing_tables = sorted(set(required_columns) - tables)
    if missing_tables:
        findings.append("MySQL缺少数据表：" + "、".join(missing_tables))

    missing_views = sorted(MYSQL_REQUIRED_VIEWS - views)
    if missing_views:
        findings.append("MySQL缺少视图：" + "、".join(missing_views))

    for table, table_required_columns in required_columns.items():
        if table not in tables:
            continue
        missing_columns = sorted(table_required_columns - normalized_columns.get(table, set()))
        if missing_columns:
            findings.append(f"MySQL表 {table} 缺少字段：" + "、".join(missing_columns))
    return findings


def _parse_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text[:10], pattern).date()
        except ValueError:
            continue
    return None


def _weekdays_after(start: date, end: date) -> int:
    if end <= start:
        return 0
    total = 0
    current = start + timedelta(days=1)
    while current <= end:
        if current.weekday() < 5:
            total += 1
        current += timedelta(days=1)
    return total


@dataclass(frozen=True)
class RecommendationFreshness:
    level: str
    data_date: date | None
    calendar_days: int | None
    weekdays_elapsed: int | None
    message: str


def assess_recommendation_freshness(
    trade_date: object,
    today: date | None = None,
) -> RecommendationFreshness:
    data_date = _parse_date(trade_date)
    current_date = today or date.today()
    if data_date is None:
        return RecommendationFreshness(
            "missing",
            None,
            None,
            None,
            "当前没有可确认日期的推荐数据，请先刷新生成后再参考。",
        )
    if data_date > current_date:
        return RecommendationFreshness(
            "invalid",
            data_date,
            (current_date - data_date).days,
            0,
            "推荐数据日期晚于系统日期，请检查服务器时区和系统时间。",
        )

    calendar_days = (current_date - data_date).days
    weekdays_elapsed = _weekdays_after(data_date, current_date)
    formatted = data_date.strftime("%Y年%m月%d日")
    if weekdays_elapsed == 0:
        return RecommendationFreshness(
            "fresh",
            data_date,
            calendar_days,
            weekdays_elapsed,
            f"当前展示的是 {formatted} 推荐数据。",
        )
    if weekdays_elapsed == 1:
        return RecommendationFreshness(
            "aging",
            data_date,
            calendar_days,
            weekdays_elapsed,
            f"当前展示的是 {formatted} 推荐数据，已有1个工作日未更新；交易前请刷新确认。",
        )
    return RecommendationFreshness(
        "stale",
        data_date,
        calendar_days,
        weekdays_elapsed,
        f"当前展示的是 {formatted} 推荐数据，已过去{weekdays_elapsed}个工作日；"
        "不得作为当日买卖依据，请先刷新并检查数据源。",
    )


@dataclass(frozen=True)
class MarketDataStatus:
    level: str
    quote_date: date | None
    row_count: int
    valid_price_count: int
    source_label: str
    is_cached: bool
    message: str


def assess_market_data_status(
    quote_date: object,
    row_count: int,
    valid_price_count: int,
    source_warning: str | None = None,
    today: date | None = None,
) -> MarketDataStatus:
    """Summarize whether a quote snapshot is suitable for current-day decisions."""
    rows = max(0, int(row_count or 0))
    valid_prices = max(0, min(rows, int(valid_price_count or 0)))
    warning = str(source_warning or "").strip()
    cached_markers = (
        "最近一次完整行情快照",
        "最近一次成功缓存",
        "显示最近一次",
        "本地缓存",
    )
    is_cached = any(marker in warning for marker in cached_markers)

    if is_cached:
        source_label = "本地行情快照"
    elif "腾讯" in warning:
        source_label = "腾讯备用行情"
    elif "同花顺" in warning:
        source_label = "同花顺备用行情"
    elif "东方财富" in warning:
        source_label = "东方财富备用行情"
    else:
        source_label = "主行情源"

    parsed_date = _parse_date(quote_date)
    if rows == 0 or valid_prices == 0:
        return MarketDataStatus(
            "unavailable",
            parsed_date,
            rows,
            valid_prices,
            source_label,
            is_cached,
            "当前没有可用的全市场报价，请先更新行情。",
        )

    freshness = assess_recommendation_freshness(parsed_date, today=today)
    if freshness.level in {"missing", "invalid", "aging", "stale"}:
        date_text = parsed_date.strftime("%Y年%m月%d日") if parsed_date else "未知日期"
        return MarketDataStatus(
            "stale",
            parsed_date,
            rows,
            valid_prices,
            source_label,
            is_cached,
            f"行情报价日为 {date_text}，不适合直接作为当日交易判断。",
        )

    coverage = valid_prices / rows if rows else 0.0
    if coverage < 0.8:
        return MarketDataStatus(
            "degraded",
            parsed_date,
            rows,
            valid_prices,
            source_label,
            is_cached,
            f"行情仅有 {valid_prices:,}/{rows:,} 只股票具备有效价格，请谨慎使用。",
        )
    if is_cached:
        return MarketDataStatus(
            "cached",
            parsed_date,
            rows,
            valid_prices,
            source_label,
            True,
            "当前使用最近一次成功行情快照，交易前建议重新更新。",
        )
    if warning:
        return MarketDataStatus(
            "degraded",
            parsed_date,
            rows,
            valid_prices,
            source_label,
            False,
            "行情已自动启用备用链路，主要报价可用，请留意数据源说明。",
        )
    return MarketDataStatus(
        "fresh",
        parsed_date,
        rows,
        valid_prices,
        source_label,
        False,
        "行情日期与覆盖率检查通过。",
    )
