from __future__ import annotations

import json
import logging
from decimal import Decimal

import pandas as pd
import streamlit as st

from stock_quant.auth import refresh_user
from stock_quant.payments import (
    PaymentConfigurationError,
    PaymentError,
    create_creem_checkout,
    creem_environment,
    list_member_payment_orders,
    list_purchasable_plans,
    payment_enabled,
    payment_tables_available,
)
from stock_quant.presentation import format_utc_datetime


LOGGER = logging.getLogger(__name__)
SERVICE_STATUS_LABELS = {
    "active": "服务有效",
    "expired": "服务已到期",
    "not_opened": "尚未开通",
    "not_started": "服务尚未开始",
    "disabled": "账号已停用",
    "deleted": "账号已删除",
}
ORDER_STATUS_LABELS = {
    "pending": "等待支付",
    "paid": "支付成功",
    "cancelled": "已取消",
    "refunded": "已退款",
    "closed": "已关闭",
    "review": "等待人工复核",
}
CHECKOUT_STATUS_LABELS = {
    "creating": "正在创建",
    "ready": "可继续支付",
    "completed": "结账已完成",
    "failed": "创建失败",
    "expired": "已失效",
}


def _feature_lines(value: object) -> list[str]:
    if value in (None, ""):
        return []
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return [value]
    if isinstance(parsed, dict):
        return [f"{key}：{item}" for key, item in parsed.items()]
    if isinstance(parsed, list):
        return [str(item) for item in parsed if str(item).strip()]
    return [str(parsed)]


def _money(minor: object, currency: object) -> str:
    amount = (Decimal(int(minor or 0)) / Decimal("100")).quantize(Decimal("0.01"))
    return f"{str(currency or '').upper()} {amount:,.2f}"


st.set_page_config(page_title="订阅与续费 - A股每日量化推荐", layout="wide")
user = st.session_state.get("auth_user")
if not user:
    st.error("请先登录后再管理订阅。")
    st.stop()

st.title("订阅与续费")
st.caption("支付成功后由服务器签名回调自动延长服务期；返回本页面不代表已经付款。")

refresh_column, _ = st.columns([1, 4])
with refresh_column:
    if st.button("刷新付款结果", width="stretch"):
        try:
            refreshed_user = refresh_user(int(user["id"]))
            if not refreshed_user:
                st.session_state.pop("auth_user", None)
                st.error("账号已停用或删除，请联系管理员。")
                st.stop()
            st.session_state["auth_user"] = refreshed_user
            st.session_state.pop("creem_checkout_result", None)
            st.rerun()
        except Exception:
            LOGGER.exception("刷新会员付款结果失败")
            st.error("付款结果暂时无法刷新，请稍后重试。")

status = str(user.get("service_status") or "not_opened")
summary = st.columns(3)
summary[0].metric("当前状态", SERVICE_STATUS_LABELS.get(status, status))
summary[1].metric("会员账号", str(user.get("mobile") or user.get("login_name") or "-"))
summary[2].metric("服务到期", format_utc_datetime(user.get("service_expires_at")) or "-")

checkout_result = st.session_state.get("creem_checkout_result")
query_checkout = str(st.query_params.get("checkout", "") or "")
query_order = str(st.query_params.get("order", "") or "")
if query_checkout == "success":
    st.info(
        f"支付页面已返回{f'，订单号 {query_order}' if query_order else ''}。"
        "系统正在等待 Creem 的签名付款回调，确认到账后会员时间才会更新。"
    )

if not payment_enabled():
    st.warning("在线订阅当前未开启。管理员仍可在“账号管理”中手工开通或续费。")
elif not payment_tables_available():
    st.error("在线订阅数据库尚未升级，请管理员先执行 Creem 支付迁移脚本。")
else:
    environment = creem_environment()
    if environment == "test":
        st.warning("当前是 Creem 测试模式，只能验证支付流程，不会产生真实扣款或真实会员收入。")

    try:
        plans = list_purchasable_plans(environment)
    except Exception:
        LOGGER.exception("读取在线订阅套餐失败")
        plans = []
        st.error("套餐读取失败，请稍后重试或联系管理员。")

    st.subheader("选择套餐")
    if not plans:
        st.info("当前没有可购买套餐，请管理员先配置套餐与 Creem 产品编号。")
    else:
        plan_columns = st.columns(min(3, len(plans)))
        for index, plan in enumerate(plans):
            with plan_columns[index % len(plan_columns)]:
                with st.container(border=True):
                    st.markdown(f"### {plan['plan_name']}")
                    st.markdown(f"**{_money(plan['provider_price_minor'], plan['provider_currency'])}**")
                    st.caption(f"服务期：{int(plan['duration_months'])}个月")
                    limit = plan.get("daily_query_limit")
                    st.write("每日查询：不限" if limit is None else f"每日查询：{int(limit)}次")
                    for feature in _feature_lines(plan.get("features_json"))[:6]:
                        st.write(f"• {feature}")
                    if st.button(
                        "前往安全支付",
                        key=f"create_creem_checkout_{plan['id']}",
                        type="primary",
                        width="stretch",
                    ):
                        try:
                            checkout_result = create_creem_checkout(
                                int(user["id"]),
                                int(plan["id"]),
                            )
                            st.session_state["creem_checkout_result"] = checkout_result
                            st.rerun()
                        except (ValueError, PaymentError) as exc:
                            st.error(str(exc))
                        except Exception:
                            LOGGER.exception("创建 Creem 结账失败")
                            st.error("支付页面创建失败，请稍后重试。系统已记录详细错误。")

    if checkout_result:
        with st.container(border=True):
            st.success(f"订单 {checkout_result['order_no']} 已创建，可以继续支付。")
            st.link_button(
                "打开 Creem 支付页面",
                checkout_result["checkout_url"],
                type="primary",
                width="stretch",
            )
            st.caption("付款完成后请返回本页并稍等片刻；会员时间以服务器回调确认结果为准。")

st.subheader("我的订阅记录")
try:
    order_rows = list_member_payment_orders(int(user["id"]), limit=100) if payment_tables_available() else []
except Exception:
    LOGGER.exception("读取会员支付订单失败")
    order_rows = []
    st.error("订单记录暂时无法读取，请稍后重试。")

if not order_rows:
    st.info("暂无在线订阅订单。")
else:
    orders = pd.DataFrame(order_rows).rename(
        columns={
            "order_no": "订单号",
            "plan_name_snapshot": "套餐",
            "duration_months_snapshot": "服务月数",
            "order_amount": "订单金额",
            "paid_amount": "实收金额",
            "currency": "币种",
            "order_status": "订单状态",
            "pay_channel": "支付渠道",
            "paid_at": "支付时间",
            "service_start_at": "服务开始",
            "service_end_at": "服务结束",
            "created_at": "创建时间",
            "environment": "环境",
            "checkout_status": "结账状态",
        }
    )
    orders["订单状态"] = orders["订单状态"].map(
        lambda value: ORDER_STATUS_LABELS.get(str(value), str(value))
    )
    orders["结账状态"] = orders["结账状态"].map(
        lambda value: CHECKOUT_STATUS_LABELS.get(str(value), str(value))
    )
    orders["环境"] = orders["环境"].map({"test": "测试", "live": "正式"}).fillna("-")
    for column in ("支付时间", "服务开始", "服务结束", "创建时间"):
        orders[column] = orders[column].map(format_utc_datetime)
    visible = [
        "订单号",
        "套餐",
        "服务月数",
        "订单金额",
        "实收金额",
        "币种",
        "订单状态",
        "结账状态",
        "环境",
        "支付时间",
        "服务开始",
        "服务结束",
        "创建时间",
    ]
    st.dataframe(orders[visible], width="stretch", hide_index=True)

with st.expander("支付方式与重要说明"):
    st.markdown(
        """
        - Creem 结账当前支持银行卡、Apple Pay 和 Google Pay，是否显示某种方式由地区与设备决定。
        - 中国个人收款人可配置支付宝作为 Creem 的商户结算方式，但这不等于买家能在结账页使用支付宝付款。
        - 会员费用只购买软件使用期限，不承诺股票收益、保本或必涨。
        - 测试模式不会真实扣款；切换正式模式前必须完成 Creem 账户审核、商品配置和小额全链路验证。
        """
    )
