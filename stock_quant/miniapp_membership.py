from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .auth import auth_enabled, list_subscription_plans
from .miniapp_auth import public_user, wechat_auto_register_enabled, wechat_trial_days


DEFAULT_PLANS = [
    {
        "id": 0,
        "plan_code": "month",
        "plan_name": "月卡",
        "duration_months": 1,
        "price": 10.0,
        "currency": "CNY",
        "features": ["每日推荐", "个股买卖点", "持仓做T方案", "预警中心"],
    },
    {
        "id": 0,
        "plan_code": "quarter",
        "plan_name": "季卡",
        "duration_months": 3,
        "price": 28.0,
        "currency": "CNY",
        "features": ["每日推荐", "可信度评分", "持仓中心", "预警中心", "策略复盘"],
    },
    {
        "id": 0,
        "plan_code": "half_year",
        "plan_name": "半年卡",
        "duration_months": 6,
        "price": 52.0,
        "currency": "CNY",
        "features": ["全部核心功能", "策略复盘", "市场地图", "数据体检"],
    },
    {
        "id": 0,
        "plan_code": "year",
        "plan_name": "年卡",
        "duration_months": 12,
        "price": 98.0,
        "currency": "CNY",
        "features": ["全部功能", "策略回测", "会员专属复盘", "运营提醒"],
    },
]


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _features(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
    return []


def _plan(row: dict[str, Any]) -> dict[str, Any]:
    price = row.get("price", 0)
    if isinstance(price, Decimal):
        price = float(price)
    return {
        "id": int(row.get("id") or 0),
        "plan_code": str(row.get("plan_code") or ""),
        "plan_name": str(row.get("plan_name") or "会员套餐"),
        "duration_months": int(row.get("duration_months") or 1),
        "price": float(price or 0),
        "currency": str(row.get("currency") or "CNY"),
        "features": _features(row.get("features_json") or row.get("features")),
    }


def _plans() -> list[dict[str, Any]]:
    configured: list[dict[str, Any]] = []
    if auth_enabled():
        configured = [
            _plan(row)
            for row in list_subscription_plans(include_disabled=False)
            if str(row.get("plan_code") or "") != "trial_7d"
        ]
    by_code = {plan["plan_code"]: plan for plan in configured}
    plans = [by_code.get(default["plan_code"], dict(default)) for default in DEFAULT_PLANS]
    extra = [plan for plan in configured if plan["plan_code"] not in {item["plan_code"] for item in plans}]
    return plans + extra


def _remaining_days(expires_at: Any) -> int | None:
    if not expires_at:
        return None
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(expires_at, datetime):
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    seconds = (expires_at.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
    return max(0, math.ceil(seconds / 86400))


def payment_capability() -> dict[str, Any]:
    subject_type = os.getenv("WECHAT_MINIAPP_SUBJECT_TYPE", "individual").strip().lower()
    requested_mode = os.getenv("MINIAPP_PAYMENT_MODE", "disabled").strip().lower()
    individual = subject_type in {"individual", "personal", "person", "个人"}

    checklist = [
        {
            "key": "subject",
            "label": "个人主体和中国大陆居民身份证",
            "ready": individual,
        },
        {
            "key": "tools_category",
            "label": "服务类目包含“工具”",
            "ready": _truthy("WECHAT_MINIAPP_CATEGORY_TOOLS"),
        },
        {
            "key": "verified",
            "label": "小程序已完成认证",
            "ready": _truthy("WECHAT_MINIAPP_VERIFIED"),
        },
        {
            "key": "filed",
            "label": "小程序已完成备案",
            "ready": _truthy("WECHAT_MINIAPP_ICP_FILED"),
        },
    ]

    if individual:
        missing_eligibility = [item["label"] for item in checklist if not item["ready"]]
        if missing_eligibility:
            return {
                "enabled": False,
                "eligible": False,
                "mode": "wechat_virtual",
                "state": "eligibility_pending",
                "message": "个人主体可申请虚拟支付；请先完成下方开通条件。",
                "checklist": checklist,
            }
        if requested_mode == "disabled":
            return {
                "enabled": False,
                "eligible": True,
                "mode": "wechat_virtual",
                "state": "not_configured",
                "message": "已满足申请条件，请在微信公众平台开通虚拟支付并配置支付参数。",
                "checklist": checklist,
            }
        if requested_mode != "wechat_virtual":
            return {
                "enabled": False,
                "eligible": True,
                "mode": requested_mode,
                "state": "unsupported_mode",
                "message": "个人主体会员订阅应使用微信小程序虚拟支付。",
                "checklist": checklist,
            }

        required = [
            "WECHAT_MINIAPP_APP_ID",
            "WECHAT_VIRTUAL_PAY_OFFER_ID",
            "WECHAT_VIRTUAL_PAY_APP_KEY",
        ]
        missing = [name for name in required if not os.getenv(name, "").strip()]
        if missing:
            return {
                "enabled": False,
                "eligible": True,
                "mode": requested_mode,
                "state": "missing_configuration",
                "message": "虚拟支付已具备申请资格，但 AppID、OfferID 或 AppKey 尚未配置完整。",
                "checklist": checklist,
            }
        if not _truthy("MINIAPP_PAYMENT_LIVE_ENABLED"):
            return {
                "enabled": False,
                "eligible": True,
                "mode": requested_mode,
                "state": "ready_for_integration",
                "message": "支付参数已就绪；完成下单、发货回调、查单兜底和小额真单验收后再开放。",
                "checklist": checklist,
            }
        return {
            "enabled": False,
            "eligible": True,
            "mode": requested_mode,
            "state": "adapter_pending",
            "message": "正式支付保护开关已开启，但支付适配器尚未通过完整验收，暂不允许扣款。",
            "checklist": checklist,
        }
    if requested_mode == "disabled":
        return {
            "enabled": False,
            "eligible": True,
            "mode": "disabled",
            "state": "not_configured",
            "message": "支付尚未配置，当前由管理员开通或续费。",
            "checklist": checklist,
        }

    required = ["WECHAT_MINIAPP_APP_ID"]
    if requested_mode == "wechat_virtual":
        required.extend(["WECHAT_VIRTUAL_PAY_OFFER_ID", "WECHAT_VIRTUAL_PAY_APP_KEY"])
    else:
        required.extend(["WECHAT_PAY_MCH_ID", "WECHAT_PAY_API_V3_KEY"])
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        return {
            "enabled": False,
            "eligible": True,
            "mode": requested_mode,
            "state": "missing_configuration",
            "message": "支付参数尚未配置完整，当前由管理员开通或续费。",
            "checklist": checklist,
        }
    if not _truthy("MINIAPP_PAYMENT_LIVE_ENABLED"):
        return {
            "enabled": False,
            "eligible": True,
            "mode": requested_mode,
            "state": "ready_for_sandbox",
            "message": "支付参数已就绪，但正式支付开关仍关闭。",
            "checklist": checklist,
        }
    return {
        "enabled": False,
        "eligible": True,
        "mode": requested_mode,
        "state": "adapter_pending",
        "message": "支付资质已具备，需完成微信支付下单、回调验签和发货接口后再开放。",
        "checklist": checklist,
    }


def subscription_summary(user: dict[str, Any]) -> dict[str, Any]:
    visible_user = public_user(user)
    return {
        "user": visible_user,
        "remaining_days": _remaining_days(user.get("service_expires_at")),
        "plans": _plans(),
        "payment": payment_capability(),
        "login": {
            "wechat_direct_login": wechat_auto_register_enabled(),
            "trial_days": wechat_trial_days(),
            "identity_note": "微信快捷登录使用OpenID识别账号，不会读取用户微信号。",
        },
    }
