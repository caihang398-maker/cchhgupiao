from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd


PLOTLY_CONFIG = {
    "displaylogo": False,
    "locale": "zh-CN",
    "responsive": True,
}

HORIZON_LABELS = {
    "short": "短期",
    "mid": "中期",
    "long": "长期",
}

RUN_STATUS_LABELS = {
    "running": "运行中",
    "done": "已完成",
    "failed": "失败",
}

ACCOUNT_STATUS_LABELS = {
    "active": "正常",
    "disabled": "已停用",
    "locked": "已锁定",
    "deleted": "已删除",
}

SERVICE_STATUS_LABELS = {
    "valid": "有效",
    "expired": "已到期",
    "not_opened": "未开通",
    "not_started": "未开始",
    "disabled": "已停用",
    "locked": "已锁定",
    "deleted": "已删除",
}

ROLE_LABELS = {
    "ADMIN": "系统管理员",
    "MEMBER": "付费会员",
}


def format_date(value: Any, empty: str = "-") -> str:
    if value is None or value == "" or pd.isna(value):
        return empty
    try:
        text = str(value).strip()
        if text.isdigit() and len(text) >= 8:
            return datetime.strptime(text[:8], "%Y%m%d").strftime("%Y年%m月%d日")
        return pd.to_datetime(value).strftime("%Y年%m月%d日")
    except Exception:
        return str(value)


def format_datetime(value: Any, empty: str = "-") -> str:
    if value is None or value == "" or pd.isna(value):
        return empty
    try:
        text = str(value).strip()
        digits = "".join(ch for ch in text if ch.isdigit())
        if len(digits) >= 14 and not any(separator in text for separator in ("-", "/", ":")):
            return datetime.strptime(digits[:14], "%Y%m%d%H%M%S").strftime("%Y年%m月%d日 %H:%M:%S")
        return pd.to_datetime(value).strftime("%Y年%m月%d日 %H:%M:%S")
    except Exception:
        return str(value)


def format_utc_datetime(value: Any, empty: str = "-") -> str:
    if value is None or value == "" or pd.isna(value):
        return empty
    try:
        timestamp = pd.to_datetime(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        return timestamp.tz_convert("Asia/Shanghai").strftime("%Y年%m月%d日 %H:%M:%S")
    except Exception:
        return str(value)


def format_time(value: Any, empty: str = "-") -> str:
    if value is None or value == "" or pd.isna(value):
        return empty
    try:
        return pd.to_datetime(value).strftime("%H:%M:%S")
    except Exception:
        text = str(value)
        return text.split("T")[-1].split(" ")[-1]


def format_roles(value: Any) -> str:
    roles = [item.strip() for item in str(value or "").split(",") if item.strip()]
    return "、".join(ROLE_LABELS.get(role, role) for role in roles) or "-"


def chinese_date_axis(fig: Any, row: int | None = None, col: int | None = None) -> None:
    kwargs: dict[str, Any] = {
        "tickformat": "%Y年%m月%d日",
        "hoverformat": "%Y年%m月%d日",
        "tickangle": 0,
    }
    if row is not None and col is not None:
        fig.update_xaxes(row=row, col=col, **kwargs)
    else:
        fig.update_xaxes(**kwargs)


def today_chinese() -> str:
    return date.today().strftime("%Y年%m月%d日")


def datetime_chinese(value: datetime) -> str:
    return value.strftime("%Y年%m月%d日 %H:%M:%S")
