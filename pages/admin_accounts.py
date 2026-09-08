from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from stock_quant.auth import (
    create_manual_order,
    create_member,
    list_login_logs,
    list_accounts,
    list_daily_usage,
    list_notification_channels,
    list_payment_transactions,
    list_subscription_orders,
    list_subscription_periods,
    list_subscription_plans,
    list_user_query_records,
    renew_member,
    save_notification_channel,
    save_subscription_plan,
    set_account_status,
)
from stock_quant.commercial import order_summary, user_behavior_frames, user_behavior_summary
from stock_quant.notify import mask_webhook_url, validate_webhook_url
from stock_quant.payments import (
    creem_environment,
    list_payment_subscriptions,
    list_payment_webhook_events,
    list_provider_product_mappings,
    payment_enabled,
    payment_tables_available,
    save_provider_product_mapping,
)
from stock_quant.product import membership_value_metrics
from stock_quant.presentation import (
    ACCOUNT_STATUS_LABELS,
    SERVICE_STATUS_LABELS,
    format_roles,
    format_utc_datetime,
)


LOGGER = logging.getLogger(__name__)


st.set_page_config(page_title="账号管理 - A股每日量化推荐", layout="wide")
user = st.session_state.get("auth_user")
if not user or not user.get("is_admin"):
    st.error("只有管理员可以访问账号管理。")
    st.stop()

st.title("会员账号管理")
st.caption("开通、续费、停用会员，并查看服务到期时间。所有操作都会写入审计日志。")

try:
    accounts = pd.DataFrame(list_accounts())
except Exception:
    LOGGER.exception("读取会员账号失败")
    st.error("读取账号失败，请检查会员数据库连接和服务日志。")
    st.stop()

now = datetime.now(timezone.utc).replace(tzinfo=None)
if accounts.empty:
    valid = pd.Series(False, index=accounts.index, dtype=bool)
    expired = pd.Series(False, index=accounts.index, dtype=bool)
    expiring = pd.Series(False, index=accounts.index, dtype=bool)
else:
    expires = pd.to_datetime(accounts["service_expires_at"], errors="coerce")
    valid = accounts["service_status"].eq("valid")
    expired = accounts["service_status"].eq("expired")
    expiring = valid & expires.between(now, now + pd.Timedelta(days=7))
columns = st.columns(4)
columns[0].metric("账号总数", str(len(accounts)))
columns[1].metric("有效会员", str(int(valid.sum())))
columns[2].metric("7天内到期", str(int(expiring.sum())))
columns[3].metric("已到期", str(int(expired.sum())))

try:
    periods = pd.DataFrame(list_subscription_periods(limit=500))
except Exception:
    LOGGER.exception("读取续费记录失败")
    periods = pd.DataFrame()
try:
    query_records = pd.DataFrame(list_user_query_records(limit=500))
except Exception:
    LOGGER.exception("读取查询记录失败")
    query_records = pd.DataFrame()
try:
    daily_usage = pd.DataFrame(list_daily_usage(limit=500))
except Exception:
    LOGGER.exception("读取使用统计失败")
    daily_usage = pd.DataFrame()
try:
    login_logs = pd.DataFrame(list_login_logs(limit=500))
except Exception:
    LOGGER.exception("读取登录日志失败")
    login_logs = pd.DataFrame()
try:
    plans = pd.DataFrame(list_subscription_plans())
except Exception:
    LOGGER.exception("读取套餐失败")
    plans = pd.DataFrame()
try:
    orders = pd.DataFrame(list_subscription_orders(limit=500))
except Exception:
    LOGGER.exception("读取订单失败")
    orders = pd.DataFrame()
try:
    payments = pd.DataFrame(list_payment_transactions(limit=500))
except Exception:
    LOGGER.exception("读取支付流水失败")
    payments = pd.DataFrame()
try:
    channels = pd.DataFrame(list_notification_channels())
except Exception:
    LOGGER.exception("读取通知通道失败")
    channels = pd.DataFrame()
payment_tables_ready = False
provider_products = pd.DataFrame()
provider_subscriptions = pd.DataFrame()
webhook_events = pd.DataFrame()
try:
    payment_tables_ready = payment_tables_available()
    if payment_tables_ready:
        provider_products = pd.DataFrame(list_provider_product_mappings())
        provider_subscriptions = pd.DataFrame(list_payment_subscriptions(limit=500))
        webhook_events = pd.DataFrame(list_payment_webhook_events(limit=500))
except Exception:
    LOGGER.exception("读取 Creem 订阅配置失败")

ops = membership_value_metrics(accounts, periods, query_records, daily_usage)
st.markdown("**会员运营看板**")
ops_cols = st.columns(5)
ops_cols[0].metric("账号总数", str(ops["account_count"]))
ops_cols[1].metric("7天内到期", str(ops["expiring_7d"]))
ops_cols[2].metric("续费记录", str(ops["renewal_count"]))
ops_cols[3].metric("查询记录", str(ops["query_count"]))
ops_cols[4].metric("近7日活跃", str(ops["active_users_7d"]))

behavior = user_behavior_summary(accounts, daily_usage, query_records, login_logs)
orders_summary = order_summary(plans, orders, payments, accounts)
st.markdown("**商业运营指标**")
biz_cols = st.columns(6)
biz_cols[0].metric("今日活跃", str(behavior["active_today"]))
biz_cols[1].metric("7日活跃", str(behavior["active_7d"]))
biz_cols[2].metric("7日沉默", str(behavior["silent_7d"]))
biz_cols[3].metric("付费订单", str(orders_summary["paid_order_count"]))
biz_cols[4].metric("实收金额", f"{orders_summary['paid_amount']:.2f}")
biz_cols[5].metric("试用订单", str(orders_summary["trial_count"]))

(
    list_tab,
    create_tab,
    manage_tab,
    behavior_tab,
    plan_tab,
    order_tab,
    payment_tab,
    creem_tab,
    notify_tab,
    renew_tab,
    query_tab,
    usage_tab,
) = st.tabs(
    [
        "账号列表",
        "开通会员",
        "续费与状态",
        "行为分析",
        "套餐管理",
        "订单流水",
        "支付流水",
        "Creem订阅",
        "通知通道",
        "续费记录",
        "查询记录",
        "使用统计",
    ]
)

with list_tab:
    if accounts.empty:
        st.info("暂无账号。")
    else:
        display = accounts.rename(
            columns={
                "id": "账号编号",
                "mobile": "手机号",
                "real_name": "姓名",
                "role_codes": "角色",
                "status": "账号状态",
                "service_status": "服务状态",
                "service_started_at": "开通时间",
                "service_expires_at": "到期时间",
                "remaining_days": "剩余天数",
                "last_login_at": "最后登录",
                "created_at": "创建时间",
            }
        )
        display["角色"] = display["角色"].map(format_roles)
        display["账号状态"] = display["账号状态"].map(ACCOUNT_STATUS_LABELS).fillna(display["账号状态"])
        display["服务状态"] = display["服务状态"].map(SERVICE_STATUS_LABELS).fillna(display["服务状态"])
        for column in ("开通时间", "到期时间", "最后登录", "创建时间"):
            if column in display:
                display[column] = display[column].map(format_utc_datetime)
        wanted = [
            "账号编号",
            "手机号",
            "姓名",
            "角色",
            "账号状态",
            "服务状态",
            "开通时间",
            "到期时间",
            "剩余天数",
            "最后登录",
            "创建时间",
        ]
        st.dataframe(display[[column for column in wanted if column in display]], width="stretch", hide_index=True)

with create_tab:
    with st.form("create_member_form"):
        mobile = st.text_input("手机号", placeholder="例如：13800000000")
        real_name = st.text_input("姓名")
        password = st.text_input(
            "初始密码",
            type="password",
            help="至少8位，并同时包含字母和数字。",
        )
        months = st.number_input("开通月数", min_value=1, max_value=60, value=1)
        remark = st.text_area("备注")
        submitted = st.form_submit_button("创建并开通", type="primary")
    if submitted:
        try:
            user_id = create_member(
                mobile,
                real_name,
                password,
                int(months),
                int(user["id"]),
                remark,
            )
            st.success(f"会员已创建，账号编号：{user_id}")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            LOGGER.exception("创建会员失败")
            st.error("创建会员失败，系统已记录详细信息。")

with manage_tab:
    member_accounts = accounts[
        ~accounts.get("role_codes", pd.Series(index=accounts.index, dtype=str))
        .fillna("")
        .str.contains("ADMIN")
    ].copy()
    if member_accounts.empty:
        st.info("暂无可管理的会员账号。")
    else:
        member_accounts["option"] = member_accounts.apply(
            lambda row: (
                f"{int(row['id'])} · {row['mobile']} · {row['real_name']} · "
                f"{SERVICE_STATUS_LABELS.get(str(row['service_status']), row['service_status'])}"
            ),
            axis=1,
        )
        selected_option = st.selectbox("选择会员", member_accounts["option"].tolist())
        selected = member_accounts[member_accounts["option"] == selected_option].iloc[0]
        st.write(f"当前到期时间：`{format_utc_datetime(selected['service_expires_at'])}`")
        months = st.number_input(
            "续费月数",
            min_value=1,
            max_value=60,
            value=1,
            key="renew_months",
        )
        remark = st.text_input("续费备注")
        renew_col, disable_col, enable_col = st.columns(3)
        with renew_col:
            if st.button("续费", type="primary", width="stretch"):
                try:
                    end_at = renew_member(
                        int(selected["id"]),
                        int(months),
                        int(user["id"]),
                        remark,
                    )
                    st.success(f"续费成功，新到期时间：{format_utc_datetime(end_at)}")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
                except Exception:
                    LOGGER.exception("会员续费失败")
                    st.error("续费失败，系统已记录详细信息。")
        with disable_col:
            if st.button("停用账号", width="stretch"):
                try:
                    set_account_status(int(selected["id"]), "disabled", int(user["id"]))
                    st.success("账号已停用。")
                    st.rerun()
                except Exception:
                    LOGGER.exception("停用会员失败")
                    st.error("停用失败，系统已记录详细信息。")
        with enable_col:
            if st.button("恢复账号", width="stretch"):
                try:
                    set_account_status(int(selected["id"]), "active", int(user["id"]))
                    st.success("账号已恢复；仍需确保服务期未到期。")
                    st.rerun()
                except Exception:
                    LOGGER.exception("恢复会员失败")
                    st.error("恢复失败，系统已记录详细信息。")

with behavior_tab:
    st.caption("用于判断谁每天登录、谁常查哪些股票、哪些功能最常用，以及哪些用户可能需要续费跟进。")
    metric_cols = st.columns(6)
    metric_cols[0].metric("今日活跃用户", str(behavior["active_today"]))
    metric_cols[1].metric("近7日活跃用户", str(behavior["active_7d"]))
    metric_cols[2].metric("近7日沉默用户", str(behavior["silent_7d"]))
    metric_cols[3].metric("近7日查询", str(behavior["query_7d"]))
    metric_cols[4].metric("近7日个股分析", str(behavior["stock_analysis_7d"]))
    metric_cols[5].metric("登录失败", str(behavior["login_failure_7d"]))
    st.write(f"最常用功能：`{behavior['top_feature']}`")
    st.write(f"最常查询股票：`{behavior['top_stock']}`")
    feature_rank, stock_rank, user_rank = user_behavior_frames(daily_usage, query_records)
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**功能使用排行**")
        st.dataframe(feature_rank, width="stretch", hide_index=True)
    with col_b:
        st.markdown("**股票查询排行**")
        st.dataframe(stock_rank, width="stretch", hide_index=True)
    st.markdown("**用户活跃排行**")
    if not user_rank.empty and "最后活跃" in user_rank:
        user_rank["最后活跃"] = user_rank["最后活跃"].map(format_utc_datetime)
    st.dataframe(user_rank, width="stretch", hide_index=True)
    with st.expander("查看最近登录记录"):
        if login_logs.empty:
            st.info("暂无登录记录。")
        else:
            display = login_logs.rename(
                columns={
                    "id": "记录编号",
                    "user_id": "账号编号",
                    "mobile": "手机号",
                    "real_name": "姓名",
                    "login_identifier": "登录账号",
                    "success": "是否成功",
                    "failure_reason": "失败原因",
                    "ip_address": "登录IP",
                    "created_at": "登录时间",
                }
            )
            display["是否成功"] = display["是否成功"].map(lambda value: "成功" if int(value or 0) == 1 else "失败")
            display["登录时间"] = display["登录时间"].map(format_utc_datetime)
            st.dataframe(display, width="stretch", hide_index=True)

with plan_tab:
    st.caption("配置月卡、季卡、年卡、试用期等套餐。后续接支付时，订单会按这里的套餐快照生成。")
    with st.form("subscription_plan_form", border=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            plan_code = st.text_input("套餐编码", placeholder="trial_7d / month / year")
            plan_name = st.text_input("套餐名称", placeholder="7天试用 / 月卡 / 年卡")
        with col2:
            duration_months = st.number_input("服务月数", min_value=1, max_value=120, value=1)
            price = st.number_input("售价（元）", min_value=0.0, max_value=999999.0, value=10.0, step=1.0)
        with col3:
            daily_limit = st.number_input("每日查询上限（0表示不限）", min_value=0, max_value=100000, value=0)
            plan_status = st.selectbox("套餐状态", ["active", "disabled"], format_func=lambda v: "启用" if v == "active" else "停用")
        features_text = st.text_area("套餐权益（一行一个）", value="每日推荐\n个股买卖点\n持仓做T方案\n预警中心")
        saved = st.form_submit_button("保存套餐", type="primary")
    if saved:
        try:
            features = [line.strip() for line in features_text.splitlines() if line.strip()]
            plan_id = save_subscription_plan(
                plan_code,
                plan_name,
                int(duration_months),
                float(price),
                None if int(daily_limit) == 0 else int(daily_limit),
                features,
                plan_status,
            )
            st.success(f"套餐已保存，编号：{plan_id}")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            LOGGER.exception("保存套餐失败")
            st.error("保存套餐失败，请查看服务日志。")
    if plans.empty:
        st.info("暂无套餐。建议先创建试用套餐、月卡、季卡、年卡。")
    else:
        display = plans.rename(
            columns={
                "id": "套餐编号",
                "plan_code": "套餐编码",
                "plan_name": "套餐名称",
                "duration_months": "服务月数",
                "price": "价格",
                "currency": "币种",
                "daily_query_limit": "每日查询上限",
                "features_json": "权益",
                "status": "状态",
                "updated_at": "更新时间",
            }
        )
        if "更新时间" in display:
            display["更新时间"] = display["更新时间"].map(format_utc_datetime)
        st.dataframe(display, width="stretch", hide_index=True)

with order_tab:
    st.caption("用于线下收款、试用开通、手工补单。标记已支付后，会自动生成服务期和支付流水。")
    if accounts.empty or plans.empty:
        st.info("需要先有会员账号和套餐，才能创建订单。")
    else:
        member_accounts = accounts[
            ~accounts.get("role_codes", pd.Series(index=accounts.index, dtype=str))
            .fillna("")
            .str.contains("ADMIN")
        ].copy()
        active_plans = plans[plans.get("status").fillna("").astype(str).eq("active")].copy()
        if member_accounts.empty or active_plans.empty:
            st.info("暂无可下单的会员或启用套餐。")
        else:
            member_accounts["option"] = member_accounts.apply(
                lambda row: f"{int(row['id'])} · {row['mobile']} · {row['real_name']}",
                axis=1,
            )
            active_plans["option"] = active_plans.apply(
                lambda row: f"{int(row['id'])} · {row['plan_name']} · {float(row['price']):.2f}元",
                axis=1,
            )
            with st.form("manual_order_form", border=True):
                order_user = st.selectbox("选择会员", member_accounts["option"].tolist())
                order_plan = st.selectbox("选择套餐", active_plans["option"].tolist())
                default_price = float(active_plans[active_plans["option"] == order_plan].iloc[0]["price"])
                paid_amount = st.number_input(
                    "实收金额",
                    min_value=0.0,
                    max_value=999999.0,
                    value=default_price,
                    step=1.0,
                    help="如与套餐价格不同，请在订单备注中说明折扣、赠送或调整原因。",
                )
                pay_channel = st.selectbox("收款方式", ["微信", "支付宝", "银行卡", "现金", "赠送", "其他"])
                mark_paid = st.checkbox("创建后立即标记为已支付并开通服务期", value=True)
                st.caption("若不立即标记为已支付，系统会将实收金额按0记录，且不会延长服务期。")
                order_remark = st.text_input("订单备注")
                created = st.form_submit_button("创建订单", type="primary")
            if created:
                try:
                    target_user_id = int(order_user.split("·", 1)[0].strip())
                    target_plan_id = int(order_plan.split("·", 1)[0].strip())
                    order_no = create_manual_order(
                        target_user_id,
                        target_plan_id,
                        float(paid_amount) if mark_paid else 0.0,
                        pay_channel,
                        int(user["id"]),
                        order_remark,
                        mark_paid=mark_paid,
                    )
                    st.success(f"订单已创建：{order_no}")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
                except Exception:
                    LOGGER.exception("创建订单失败")
                    st.error("创建订单失败，请查看服务日志。")
    summary_cols = st.columns(5)
    summary_cols[0].metric("订单总数", str(orders_summary["order_count"]))
    summary_cols[1].metric("已支付订单", str(orders_summary["paid_order_count"]))
    summary_cols[2].metric("实收金额", f"{orders_summary['paid_amount']:.2f}")
    summary_cols[3].metric("支付流水", str(orders_summary["payment_count"]))
    summary_cols[4].metric("7天内到期", str(orders_summary["expiring_7d"]))
    if orders.empty:
        st.info("暂无订单流水。")
    else:
        display = orders.rename(
            columns={
                "id": "订单编号",
                "order_no": "订单号",
                "mobile": "手机号",
                "real_name": "姓名",
                "plan_name_snapshot": "套餐名称",
                "duration_months_snapshot": "服务月数",
                "order_amount": "订单金额",
                "paid_amount": "实收金额",
                "order_status": "订单状态",
                "pay_channel": "收款方式",
                "paid_at": "支付时间",
                "service_start_at": "服务开始",
                "service_end_at": "服务结束",
                "remark": "备注",
                "created_at": "创建时间",
            }
        )
        for column in ("支付时间", "服务开始", "服务结束", "创建时间"):
            if column in display:
                display[column] = display[column].map(format_utc_datetime)
        st.dataframe(display, width="stretch", hide_index=True)

with payment_tab:
    st.caption("支付流水用于对账、退款和收入统计，包含后台手工记账与 Creem 签名回调确认的在线付款。")
    if payments.empty:
        st.info("暂无支付流水。")
    else:
        display = payments.rename(
            columns={
                "id": "流水编号",
                "order_no": "订单号",
                "mobile": "手机号",
                "real_name": "姓名",
                "transaction_no": "支付流水号",
                "transaction_type": "流水类型",
                "channel": "渠道",
                "amount": "金额",
                "status": "状态",
                "occurred_at": "发生时间",
                "created_at": "创建时间",
            }
        )
        for column in ("发生时间", "创建时间"):
            if column in display:
                display[column] = display[column].map(format_utc_datetime)
        st.dataframe(display, width="stretch", hide_index=True)

with creem_tab:
    st.subheader("Creem 在线订阅")
    configured_environment = "未配置"
    try:
        configured_environment = "正式" if creem_environment() == "live" else "测试"
    except Exception as exc:
        st.error(str(exc))
    prefix = "CREEM_LIVE" if configured_environment == "正式" else "CREEM_TEST"
    config_cols = st.columns(4)
    config_cols[0].metric("在线订阅", "已开启" if payment_enabled() else "未开启")
    config_cols[1].metric("当前环境", configured_environment)
    config_cols[2].metric("接口密钥", "已设置" if os.getenv(f"{prefix}_API_KEY", "").strip() else "未设置")
    config_cols[3].metric(
        "回调密钥",
        "已设置" if os.getenv(f"{prefix}_WEBHOOK_SECRET", "").strip() else "未设置",
    )
    st.caption(
        "Creem 结账支持银行卡、Apple Pay 和 Google Pay。中国个人收款人的支付宝配置属于商户结算，"
        "不是买家结账时的支付宝付款方式。"
    )
    if not payment_tables_ready:
        st.error("支付数据库表尚未安装，请先执行 database/migrations/20260729_creem_subscriptions.sql。")
    elif plans.empty:
        st.info("请先在“套餐管理”中创建至少一个启用套餐。")
    else:
        plan_options = {
            f"{int(row['id'])} · {row['plan_name']} · {int(row['duration_months'])}个月": int(row["id"])
            for _, row in plans.iterrows()
            if str(row.get("status") or "") == "active"
        }
        if not plan_options:
            st.info("当前没有启用套餐。")
        else:
            with st.form("creem_product_mapping_form", border=True):
                left, middle, right = st.columns(3)
                with left:
                    selected_plan = st.selectbox("本地会员套餐", list(plan_options))
                    mapping_environment = st.selectbox(
                        "支付环境",
                        ["test", "live"],
                        format_func=lambda value: "测试" if value == "test" else "正式",
                    )
                with middle:
                    external_product_id = st.text_input(
                        "Creem 产品编号",
                        placeholder="prod_xxxxxxxxx",
                    )
                    provider_price_minor = st.number_input(
                        "渠道金额（最小货币单位）",
                        min_value=1,
                        value=1000,
                        step=1,
                        help="例如 EUR 10.00 填写 1000。",
                    )
                with right:
                    provider_currency = st.selectbox("渠道币种", ["EUR", "USD"])
                    mapping_status = st.selectbox(
                        "映射状态",
                        ["active", "disabled"],
                        format_func=lambda value: "启用" if value == "active" else "停用",
                    )
                mapping_saved = st.form_submit_button("保存产品映射", type="primary")
            if mapping_saved:
                try:
                    mapping_id = save_provider_product_mapping(
                        plan_options[selected_plan],
                        mapping_environment,
                        external_product_id,
                        int(provider_price_minor),
                        provider_currency,
                        mapping_status,
                    )
                    st.success(f"Creem 产品映射已保存，编号：{mapping_id}")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
                except Exception:
                    LOGGER.exception("保存 Creem 产品映射失败")
                    st.error("保存产品映射失败，请查看服务日志。")

        st.markdown("**套餐与 Creem 产品映射**")
        if provider_products.empty:
            st.info("尚未配置产品映射。")
        else:
            display = provider_products.rename(
                columns={
                    "id": "映射编号",
                    "plan_code": "套餐编码",
                    "plan_name": "套餐名称",
                    "duration_months": "服务月数",
                    "environment": "环境",
                    "external_product_id": "Creem产品编号",
                    "provider_price_minor": "渠道金额（最小单位）",
                    "provider_currency": "渠道币种",
                    "status": "状态",
                    "updated_at": "更新时间",
                }
            )
            display["环境"] = display["环境"].map({"test": "测试", "live": "正式"})
            display["状态"] = display["状态"].map({"active": "启用", "disabled": "停用"})
            display["更新时间"] = display["更新时间"].map(format_utc_datetime)
            st.dataframe(
                display[
                    [
                        "映射编号",
                        "套餐编码",
                        "套餐名称",
                        "服务月数",
                        "环境",
                        "Creem产品编号",
                        "渠道金额（最小单位）",
                        "渠道币种",
                        "状态",
                        "更新时间",
                    ]
                ],
                width="stretch",
                hide_index=True,
            )

        st.markdown("**在线订阅状态**")
        if provider_subscriptions.empty:
            st.info("暂无 Creem 在线订阅。")
        else:
            display = provider_subscriptions.rename(
                columns={
                    "mobile": "手机号",
                    "real_name": "姓名",
                    "plan_name": "套餐",
                    "environment": "环境",
                    "provider_subscription_id": "Creem订阅编号",
                    "status": "订阅状态",
                    "current_period_start_at": "本期开始",
                    "current_period_end_at": "本期结束",
                    "next_transaction_at": "下次扣款时间",
                    "canceled_at": "取消时间",
                    "updated_at": "更新时间",
                }
            )
            display["环境"] = display["环境"].map({"test": "测试", "live": "正式"})
            for column in ("本期开始", "本期结束", "下次扣款时间", "取消时间", "更新时间"):
                display[column] = display[column].map(format_utc_datetime)
            st.dataframe(
                display[
                    [
                        "手机号",
                        "姓名",
                        "套餐",
                        "环境",
                        "Creem订阅编号",
                        "订阅状态",
                        "本期开始",
                        "本期结束",
                        "下次扣款时间",
                        "取消时间",
                        "更新时间",
                    ]
                ],
                width="stretch",
                hide_index=True,
            )

        with st.expander("查看最近支付回调"):
            if webhook_events.empty:
                st.info("尚未收到 Creem 回调。")
            else:
                display = webhook_events.rename(
                    columns={
                        "event_id": "事件编号",
                        "event_type": "事件类型",
                        "environment": "环境",
                        "process_status": "处理状态",
                        "attempts": "处理次数",
                        "error_message": "错误信息",
                        "received_at": "接收时间",
                        "processed_at": "完成时间",
                    }
                )
                display["环境"] = display["环境"].map({"test": "测试", "live": "正式"})
                for column in ("接收时间", "完成时间"):
                    display[column] = display[column].map(format_utc_datetime)
                st.dataframe(
                    display[
                        [
                            "事件编号",
                            "事件类型",
                            "环境",
                            "处理状态",
                            "处理次数",
                            "错误信息",
                            "接收时间",
                            "完成时间",
                        ]
                    ],
                    width="stretch",
                    hide_index=True,
                )

with notify_tab:
    st.caption("先支持企业微信/钉钉/飞书机器人配置；短信先预留配置入口，等你确定服务商后接入。")
    with st.form("notification_channel_form", border=True):
        col1, col2 = st.columns(2)
        with col1:
            channel_code = st.text_input("通道编码", placeholder="wecom_alert")
            channel_name = st.text_input("通道名称", placeholder="企业微信买卖点提醒")
            provider = st.selectbox("通道类型", ["企业微信", "钉钉", "飞书", "短信"])
        with col2:
            webhook_url = st.text_input("机器人Webhook或短信接口地址")
            enabled = st.checkbox("启用通道", value=False)
            usage_scene = st.text_input("使用场景", placeholder="买点提醒、止损提醒、到期提醒")
        channel_remark = st.text_area("备注")
        saved_channel = st.form_submit_button("保存通道", type="primary")
    if saved_channel:
        try:
            if provider != "短信" and webhook_url.strip():
                ok, message = validate_webhook_url(provider, webhook_url)
                if not ok:
                    raise ValueError(message)
            channel_id = save_notification_channel(
                channel_code,
                channel_name,
                provider,
                webhook_url,
                enabled,
                usage_scene,
                channel_remark,
            )
            st.success(f"通知通道已保存，编号：{channel_id}")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            LOGGER.exception("保存通知通道失败")
            st.error("保存通知通道失败，请查看服务日志。")
    if channels.empty:
        st.info("暂无通知通道。")
    else:
        display = channels.rename(
            columns={
                "id": "通道编号",
                "channel_code": "通道编码",
                "channel_name": "通道名称",
                "provider": "类型",
                "webhook_url": "接口地址",
                "enabled": "是否启用",
                "usage_scene": "使用场景",
                "remark": "备注",
                "updated_at": "更新时间",
            }
        )
        display["是否启用"] = display["是否启用"].map(lambda value: "启用" if int(value or 0) == 1 else "停用")
        if "接口地址" in display:
            display["接口地址"] = display["接口地址"].fillna("").map(mask_webhook_url)
        if "更新时间" in display:
            display["更新时间"] = display["更新时间"].map(format_utc_datetime)
        st.dataframe(display, width="stretch", hide_index=True)

with renew_tab:
    st.caption("这里用于查看每一次开通、续费、赠送或调整的服务期记录，方便后续对账和到期提醒。")
    if periods.empty:
        st.info("暂无续费记录。")
    else:
        display = periods.rename(
            columns={
                "id": "记录编号",
                "user_id": "账号编号",
                "mobile": "手机号",
                "real_name": "姓名",
                "start_at": "服务开始",
                "end_at": "服务结束",
                "source_type": "来源",
                "status": "状态",
                "remark": "备注",
                "created_at": "创建时间",
                "granted_by_name": "操作人",
                "granted_by_mobile": "操作人手机号",
            }
        )
        for column in ("服务开始", "服务结束", "创建时间"):
            if column in display:
                display[column] = display[column].map(format_utc_datetime)
        visible = [
            "记录编号",
            "账号编号",
            "手机号",
            "姓名",
            "服务开始",
            "服务结束",
            "来源",
            "状态",
            "操作人",
            "备注",
            "创建时间",
        ]
        st.dataframe(display[[column for column in visible if column in display]], width="stretch", hide_index=True)

with query_tab:
    st.caption("这里保存用户每天看过什么功能、查询过什么股票，便于判断活跃度和续费意向。")
    if query_records.empty:
        st.info("暂无用户查询记录。")
    else:
        display = query_records.rename(
            columns={
                "id": "记录编号",
                "user_id": "账号编号",
                "mobile": "手机号",
                "real_name": "姓名",
                "query_date": "查询日期",
                "query_type": "查询类型",
                "stock_symbol": "股票代码",
                "stock_name": "股票名称",
                "result_status": "结果状态",
                "duration_ms": "耗时毫秒",
                "created_at": "查询时间",
            }
        )
        if "查询时间" in display:
            display["查询时间"] = display["查询时间"].map(format_utc_datetime)
        visible = [
            "记录编号",
            "账号编号",
            "手机号",
            "姓名",
            "查询日期",
            "查询类型",
            "股票代码",
            "股票名称",
            "结果状态",
            "耗时毫秒",
            "查询时间",
        ]
        st.dataframe(display[[column for column in visible if column in display]], width="stretch", hide_index=True)

with usage_tab:
    st.caption("按天统计登录、推荐查看、个股分析和导出次数，用来观察付费粘性。")
    if daily_usage.empty:
        st.info("暂无使用统计。")
    else:
        display = daily_usage.rename(
            columns={
                "user_id": "账号编号",
                "mobile": "手机号",
                "real_name": "姓名",
                "usage_date": "使用日期",
                "login_count": "登录次数",
                "query_count": "查询次数",
                "stock_analysis_count": "个股分析次数",
                "recommendation_view_count": "推荐查看次数",
                "hotspot_view_count": "热点查看次数",
                "export_count": "导出次数",
                "last_activity_at": "最后活跃",
            }
        )
        if "最后活跃" in display:
            display["最后活跃"] = display["最后活跃"].map(format_utc_datetime)
        visible = [
            "账号编号",
            "手机号",
            "姓名",
            "使用日期",
            "登录次数",
            "查询次数",
            "个股分析次数",
            "推荐查看次数",
            "热点查看次数",
            "导出次数",
            "最后活跃",
        ]
        st.dataframe(display[[column for column in visible if column in display]], width="stretch", hide_index=True)
