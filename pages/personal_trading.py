from __future__ import annotations

import os
import uuid
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from stock_quant.auth import auth_enabled
from stock_quant.broker import (
    BrokerError,
    OrderDraft,
    QmtBrokerGateway,
    QmtConfig,
    qmt_symbol,
    validate_order_draft,
)
from stock_quant.data import (
    DataSourceError,
    fetch_daily_history,
    fetch_minute_history,
    fetch_spot,
    market_breadth,
    plain_code,
)
from stock_quant.indicators import add_indicators
from stock_quant.presentation import PLOTLY_CONFIG
from stock_quant.sentiment import fetch_sentiment_bundle
from stock_quant.strategy import analyze_stock
from stock_quant.storage import (
    get_trading_risk_state,
    load_broker_orders,
    save_broker_order,
    save_trading_event,
    update_trading_risk_state,
)
from stock_quant.trading import (
    ExecutionContext,
    TradingRiskPolicy,
    TradingRiskState,
    evaluate_execution_risk,
)


def enabled(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


user = st.session_state.get("auth_user")
personal_enabled = enabled(os.getenv("ENABLE_PERSONAL_TRADING", "false"))
if auth_enabled() and (not user or not user.get("is_admin")):
    st.error("该页面仅对系统管理员本人开放。")
    st.stop()
if not personal_enabled:
    st.error("个人实盘决策台尚未启用。请在本机设置 ENABLE_PERSONAL_TRADING=true 后重启。")
    st.stop()


@st.cache_data(ttl=15, show_spinner=False)
def live_market_snapshot(ignore_proxy: bool) -> tuple[pd.DataFrame, dict]:
    spot = fetch_spot(ignore_proxy=ignore_proxy)
    return spot, market_breadth(spot)


def minute_chart(frame: pd.DataFrame, symbol: str, name: str) -> go.Figure:
    fig = go.Figure(
        data=[
            go.Candlestick(
                x=frame["time"],
                open=frame["open"],
                high=frame["high"],
                low=frame["low"],
                close=frame["close"],
                name="分钟K线",
                increasing_line_color="#dc2626",
                decreasing_line_color="#059669",
            )
        ]
    )
    fig.add_trace(
        go.Bar(
            x=frame["time"],
            y=frame["volume"],
            name="成交量",
            marker_color="#94a3b8",
            yaxis="y2",
            opacity=0.45,
        )
    )
    fig.update_layout(
        title=f"{symbol} {name} 分钟K线",
        height=560,
        template="plotly_white",
        margin=dict(l=10, r=10, t=48, b=10),
        xaxis_rangeslider_visible=False,
        yaxis2=dict(overlaying="y", side="right", showgrid=False, title="成交量"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    return fig


def daily_chart(frame: pd.DataFrame, symbol: str, name: str) -> go.Figure:
    data = add_indicators(frame)
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=data["date"],
            open=data["open"],
            high=data["high"],
            low=data["low"],
            close=data["close"],
            name="日K线",
            increasing_line_color="#dc2626",
            decreasing_line_color="#059669",
        )
    )
    for column, label, color in [
        ("ma20", "20日均线", "#2563eb"),
        ("ma60", "60日均线", "#7c3aed"),
        ("ma120", "120日均线", "#6b7280"),
    ]:
        fig.add_trace(go.Scatter(x=data["date"], y=data[column], name=label, line=dict(color=color)))
    fig.update_layout(
        title=f"{symbol} {name} 日K趋势",
        height=520,
        template="plotly_white",
        margin=dict(l=10, r=10, t=48, b=10),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    return fig


st.title("个人实盘决策台")
st.caption("仅供管理员本人使用：实时分析、订单草稿、东吴QMT账户同步和人工确认委托。")
st.warning(
    "默认禁止自动实盘。系统不会保存交易密码；东吴QMT必须在同一台受控Windows机器上登录，"
    "每笔委托都要经过金额、价格偏离、可用资金、可卖数量和人工确认检查。"
)

config = QmtConfig.from_env()
gateway = st.session_state.get("personal_qmt_gateway")
if not isinstance(gateway, QmtBrokerGateway) or gateway.config != config:
    gateway = QmtBrokerGateway(config)
    st.session_state["personal_qmt_gateway"] = gateway

ready = gateway.readiness()
status_cols = st.columns(4)
status_cols[0].metric("QMT配置", "已配置" if ready["configured"] else "未配置")
status_cols[1].metric("QMT组件", "可用" if ready["xtquant_available"] else "不可用")
status_cols[2].metric("交易连接", "已连接" if gateway.connected else "未连接")
status_cols[3].metric("实盘开关", "开启" if config.live_enabled else "关闭")
st.caption(str(ready["message"]))

connect_col, refresh_col = st.columns([1, 3])
with connect_col:
    if st.button("连接东吴QMT", type="primary", width="stretch", disabled=not ready["configured"]):
        try:
            gateway.connect()
            st.success("东吴QMT连接成功。")
            st.rerun()
        except Exception as exc:
            st.error(f"连接失败：{exc}")
with refresh_col:
    st.caption("需先向东吴证券申请QMT权限，并保持MiniQMT客户端登录。")

st.divider()
st.subheader("实时行情与决策")
input_col, period_col, proxy_col, action_col = st.columns([2.2, 1, 1, 1.2])
with input_col:
    query = st.text_input("股票代码", value=st.session_state.get("personal_symbol", "600000"))
with period_col:
    period = st.selectbox("分钟周期", ["1", "5", "15", "30", "60"], index=1, format_func=lambda x: f"{x}分钟")
with proxy_col:
    ignore_proxy = st.toggle("忽略系统代理", value=True)
with action_col:
    refresh_analysis = st.button("刷新实时分析", type="primary", width="stretch")

if refresh_analysis:
    try:
        code = plain_code(query)
        with st.spinner("正在读取实时行情、日K和市场情绪"):
            minute = fetch_minute_history(code, period=period, ignore_proxy=ignore_proxy)
            daily = fetch_daily_history(code, ignore_proxy=ignore_proxy)
            spot, breadth = live_market_snapshot(ignore_proxy)
        name_row = spot[spot["code"].astype(str).str.zfill(6) == code]
        name = str(name_row.iloc[0]["name"]) if not name_row.empty else ""
        report = analyze_stock(daily, symbol=code, name=name)
        st.session_state.update(
            personal_symbol=code,
            personal_name=name,
            personal_minute=minute,
            personal_daily=daily,
            personal_report=report,
            personal_breadth=breadth,
            personal_latest_price=float(minute.iloc[-1]["close"]),
            personal_refreshed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        try:
            st.session_state["personal_sentiment"] = fetch_sentiment_bundle(
                breadth=breadth,
                ignore_proxy=ignore_proxy,
            )
        except Exception as exc:
            st.session_state["personal_sentiment_error"] = str(exc)
    except (DataSourceError, ValueError) as exc:
        st.error(f"实时分析失败：{exc}")
    except Exception as exc:
        st.error(f"实时分析异常：{exc}")

minute = st.session_state.get("personal_minute", pd.DataFrame())
daily = st.session_state.get("personal_daily", pd.DataFrame())
report = st.session_state.get("personal_report")
breadth = st.session_state.get("personal_breadth", {})
symbol = st.session_state.get("personal_symbol", "")
name = st.session_state.get("personal_name", "")
latest_price = float(st.session_state.get("personal_latest_price", 0) or 0)

if minute.empty:
    st.info("输入股票代码后点击“刷新实时分析”，系统会加载分钟K线、日K趋势、大盘宽度和情绪周期。")

if not minute.empty and report is not None:
    metrics = st.columns(7)
    metrics[0].metric("最新价", f"{latest_price:.2f}")
    metrics[1].metric("技术评分", f"{report.score:.0f}")
    metrics[2].metric("建议仓位", f"{report.position_pct:.0%}")
    metrics[3].metric("买点下沿", f"{report.buy_zone_low:.2f}")
    metrics[4].metric("买点上沿", f"{report.buy_zone_high:.2f}")
    metrics[5].metric("失效止损", f"{report.stop_loss:.2f}")
    metrics[6].metric("目标一", f"{report.take_profit_1:.2f}")
    st.caption(f"最近刷新：{st.session_state.get('personal_refreshed_at', '-')}；行情源时间以图表最后一根K线为准。")
    minute_tab, daily_tab = st.tabs(["分钟K线", "日K趋势"])
    with minute_tab:
        st.plotly_chart(minute_chart(minute, symbol, name), width="stretch", config=PLOTLY_CONFIG)
    with daily_tab:
        st.plotly_chart(daily_chart(daily, symbol, name), width="stretch", config=PLOTLY_CONFIG)

if breadth:
    st.subheader("大盘与大A情绪")
    market_cols = st.columns(7)
    market_cols[0].metric("上涨", f"{int(breadth.get('up', 0)):,}")
    market_cols[1].metric("下跌", f"{int(breadth.get('down', 0)):,}")
    market_cols[2].metric("涨停估算", f"{int(breadth.get('limit_up', 0)):,}")
    market_cols[3].metric("跌停估算", f"{int(breadth.get('limit_down', 0)):,}")
    market_cols[4].metric("涨跌家数比", f"{float(breadth.get('adv_dec_ratio', 0)):.2f}")
    market_cols[5].metric("市场温度", f"{float(breadth.get('temperature', 0)):.0f}")
    market_cols[6].metric("市场状态", str(breadth.get("temperature_label", "-")))
    sentiment = st.session_state.get("personal_sentiment")
    if sentiment is not None:
        snapshot = sentiment.snapshot
        st.info(
            f"情绪阶段：{snapshot.get('emotion_stage', '-')}；最高连板：{snapshot.get('max_streak', 0)}板；"
            f"炸板率：{float(snapshot.get('broken_rate', 0)):.1%}。"
        )
    elif st.session_state.get("personal_sentiment_error"):
        st.caption("情绪明细暂不可用：" + st.session_state["personal_sentiment_error"])

st.divider()
st.subheader("东吴QMT账户与订单草稿")
owner_key = str((user or {}).get("id") or (user or {}).get("mobile") or "local")
risk_record = get_trading_risk_state(owner_key)
risk_policy = TradingRiskPolicy(
    max_order_notional=float(risk_record.get("max_order_notional", 50_000) or 50_000),
    max_daily_loss=float(risk_record.get("max_daily_loss", 3_000) or 3_000),
    max_daily_orders=int(risk_record.get("max_daily_orders", 20) or 20),
    max_single_position_pct=float(risk_record.get("max_single_position_pct", 0.25) or 0.25),
)
risk_state = TradingRiskState(
    kill_switch=bool(risk_record.get("kill_switch", 0)),
    daily_realized_pnl=float(risk_record.get("daily_realized_pnl", 0) or 0),
    daily_order_count=int(risk_record.get("daily_order_count", 0) or 0),
)
risk_cols = st.columns([1, 1, 1, 1.4])
risk_cols[0].metric("交易总熔断", "已开启" if risk_state.kill_switch else "未开启")
risk_cols[1].metric("今日委托", f"{risk_state.daily_order_count}/{risk_policy.max_daily_orders}")
risk_cols[2].metric("今日已实现盈亏", f"{risk_state.daily_realized_pnl:,.2f}元")
with risk_cols[3]:
    switch_label = "解除总熔断" if risk_state.kill_switch else "立即开启总熔断"
    if st.button(switch_label, width="stretch"):
        new_value = not risk_state.kill_switch
        update_trading_risk_state(owner_key, kill_switch=new_value)
        save_trading_event(
            owner_key,
            "总熔断",
            "交易总熔断已开启" if new_value else "交易总熔断已解除",
            severity="警告" if new_value else "提示",
        )
        st.rerun()
st.caption(
    f"风控上限：单笔{risk_policy.max_order_notional:,.0f}元；单日亏损"
    f"{risk_policy.max_daily_loss:,.0f}元；单股仓位{risk_policy.max_single_position_pct:.0%}。"
)
account = st.session_state.get("personal_qmt_account", {})
positions = st.session_state.get("personal_qmt_positions", [])
orders = st.session_state.get("personal_qmt_orders", [])
if gateway.connected:
    if st.button("同步资金、持仓与委托"):
        try:
            account = gateway.account_snapshot()
            positions = gateway.positions()
            orders = gateway.orders()
            st.session_state["personal_qmt_account"] = account
            st.session_state["personal_qmt_positions"] = positions
            st.session_state["personal_qmt_orders"] = orders
        except BrokerError as exc:
            st.error(str(exc))
    if account:
        account_cols = st.columns(4)
        account_cols[0].metric("总资产", f"{account.get('total_asset', 0):,.2f}")
        account_cols[1].metric("可用资金", f"{account.get('cash', 0):,.2f}")
        account_cols[2].metric("持仓市值", f"{account.get('market_value', 0):,.2f}")
        account_cols[3].metric("冻结资金", f"{account.get('frozen_cash', 0):,.2f}")
    if positions:
        st.dataframe(pd.DataFrame(positions), hide_index=True, width="stretch")
    if orders:
        st.markdown("**最近委托**")
        st.dataframe(pd.DataFrame(orders), hide_index=True, width="stretch")
        order_ids = [int(item["订单编号"]) for item in orders if int(item.get("订单编号", 0)) > 0]
        if config.live_enabled and order_ids:
            cancel_col, confirm_col, button_col = st.columns([1.3, 1.5, 1])
            with cancel_col:
                cancel_order_id = st.selectbox(
                    "选择订单编号",
                    order_ids,
                )
            with confirm_col:
                cancel_confirmation = st.text_input("撤单确认文字", placeholder="输入：确认撤单")
            with button_col:
                if st.button("提交撤单", width="stretch"):
                    try:
                        gateway.cancel_order(int(cancel_order_id), cancel_confirmation)
                        st.success("撤单请求已提交，请重新同步委托状态。")
                    except BrokerError as exc:
                        st.error(f"撤单未提交：{exc}")
else:
    st.info("尚未连接QMT。你仍可查看实时决策，但不能读取账户或提交委托。")

if symbol and latest_price > 0:
    side_col, qty_col, price_col = st.columns(3)
    with side_col:
        side_label = st.selectbox("方向", ["买入", "卖出"])
    with qty_col:
        quantity = st.number_input("数量（股）", min_value=100, max_value=1_000_000, value=100, step=100)
    with price_col:
        limit_price = st.number_input("限价", min_value=0.01, value=round(latest_price, 2), step=0.01, format="%.2f")
    side = "buy" if side_label == "买入" else "sell"
    sellable = 0
    target_qmt_symbol = qmt_symbol(symbol)
    for item in positions:
        if str(item.get("代码", "")).upper() == target_qmt_symbol:
            sellable = int(item.get("可卖数量", 0) or 0)
            break
    draft = OrderDraft(symbol=symbol, side=side, quantity=int(quantity), limit_price=float(limit_price))
    account_synced = bool(account)
    effective_cash = float(account.get("cash", 0) or 0) if account_synced else float("inf")
    effective_sellable = sellable if account_synced else int(quantity)
    validation_errors = validate_order_draft(
        draft,
        latest_price=latest_price,
        available_cash=effective_cash,
        sellable_shares=effective_sellable,
        config=config,
    )
    current_position_market_value = 0.0
    for item in positions:
        if str(item.get("代码", "")).upper() == target_qmt_symbol:
            current_position_market_value = float(item.get("市值", 0) or 0)
            break
    risk_errors = evaluate_execution_risk(
        draft,
        ExecutionContext(
            latest_price=latest_price,
            available_cash=effective_cash,
            total_asset=float(account.get("total_asset", 0) or 0),
            current_position_market_value=current_position_market_value,
            sellable_shares=effective_sellable,
        ),
        risk_state,
        risk_policy,
    )
    validation_errors = list(dict.fromkeys(validation_errors + risk_errors))
    if config.live_enabled and gateway.connected and not account_synced:
        validation_errors.append("提交实盘前必须先同步资金与持仓")
    st.write(
        f"订单草稿：{target_qmt_symbol}，{side_label}{int(quantity)}股，限价{float(limit_price):.2f}元，"
        f"预计金额{draft.notional:,.2f}元。"
    )
    if validation_errors:
        st.warning("当前不能提交：" + "；".join(validation_errors))
    draft_col, ledger_col = st.columns([1, 2])
    with draft_col:
        if st.button("保存订单草稿", width="stretch", disabled=bool(validation_errors)):
            client_order_id = uuid.uuid4().hex
            save_broker_order(
                {
                    "owner_key": owner_key,
                    "client_order_id": client_order_id,
                    "account_mask": ("****" + config.account_id[-4:]) if config.account_id else "",
                    "symbol": target_qmt_symbol,
                    "side": side_label,
                    "quantity": int(quantity),
                    "limit_price": float(limit_price),
                    "notional": draft.notional,
                    "mode": "实盘待确认" if config.live_enabled else "草稿",
                    "status": "草稿",
                    "risk_snapshot": {
                        "最新价": latest_price,
                        "单笔上限": risk_policy.max_order_notional,
                        "今日委托数": risk_state.daily_order_count,
                        "总熔断": risk_state.kill_switch,
                    },
                }
            )
            save_trading_event(owner_key, "订单草稿", f"已保存{target_qmt_symbol}{side_label}草稿")
            st.success("订单草稿已保存，未向券商提交。")
    with ledger_col:
        st.caption("草稿和实盘委托统一留痕；数据库只保存脱敏账号，不保存交易密码。")
    if not config.live_enabled:
        st.info("当前为只读/订单草稿模式。设置 QMT_LIVE_TRADING=true 并重启后，才会出现实盘确认入口。")
    else:
        confirmation = st.text_input("实盘确认文字", placeholder="输入：确认实盘委托")
        acknowledge = st.checkbox("我已核对代码、方向、数量、价格、可用资金和止损计划")
        if st.button(
            "提交东吴QMT限价委托",
            type="primary",
            disabled=bool(validation_errors) or not gateway.connected or not acknowledge,
        ):
            client_order_id = uuid.uuid4().hex
            try:
                save_broker_order(
                    {
                        "owner_key": owner_key,
                        "client_order_id": client_order_id,
                        "account_mask": ("****" + config.account_id[-4:]) if config.account_id else "",
                        "symbol": target_qmt_symbol,
                        "side": side_label,
                        "quantity": int(quantity),
                        "limit_price": float(limit_price),
                        "notional": draft.notional,
                        "mode": "实盘",
                        "status": "提交中",
                        "risk_snapshot": {"风控检查": "通过"},
                    }
                )
                order_id = gateway.submit_limit_order(
                    draft,
                    latest_price=latest_price,
                    available_cash=float(account.get("cash", 0) or 0),
                    sellable_shares=sellable,
                    confirmation=confirmation,
                )
                save_broker_order(
                    {
                        "owner_key": owner_key,
                        "client_order_id": client_order_id,
                        "account_mask": ("****" + config.account_id[-4:]) if config.account_id else "",
                        "symbol": target_qmt_symbol,
                        "side": side_label,
                        "quantity": int(quantity),
                        "limit_price": float(limit_price),
                        "notional": draft.notional,
                        "mode": "实盘",
                        "status": "已提交",
                        "broker_order_id": str(order_id),
                        "risk_snapshot": {"风控检查": "通过"},
                    }
                )
                update_trading_risk_state(owner_key, increment_orders=1)
                st.success(f"委托已提交，QMT订单编号：{order_id}")
            except BrokerError as exc:
                save_broker_order(
                    {
                        "owner_key": owner_key,
                        "client_order_id": client_order_id,
                        "account_mask": ("****" + config.account_id[-4:]) if config.account_id else "",
                        "symbol": target_qmt_symbol,
                        "side": side_label,
                        "quantity": int(quantity),
                        "limit_price": float(limit_price),
                        "notional": draft.notional,
                        "mode": "实盘",
                        "status": "提交失败",
                        "error_message": str(exc),
                        "risk_snapshot": {"风控检查": "通过"},
                    }
                )
                st.error(f"委托未提交：{exc}")

ledger = load_broker_orders(owner_key, limit=30)
if not ledger.empty:
    st.markdown("**本地订单台账**")
    display_columns = {
        "updated_at": "更新时间",
        "symbol": "代码",
        "side": "方向",
        "quantity": "数量",
        "limit_price": "限价",
        "notional": "预计金额",
        "mode": "模式",
        "status": "状态",
        "broker_order_id": "券商订单号",
        "error_message": "失败原因",
    }
    st.dataframe(
        ledger[list(display_columns)].rename(columns=display_columns),
        hide_index=True,
        width="stretch",
    )

st.caption("本模块只提供决策支持和受控委托入口，不保证成交或收益。实盘前至少连续运行模拟模式20个交易日。")
