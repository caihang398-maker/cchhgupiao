from __future__ import annotations

import json
import html as html_lib
import logging
import math
import os
from datetime import date, timedelta
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from stock_quant.backtest import run_backtest
from stock_quant.commercial import (
    commercial_audit,
    commercial_plan_frame,
    compliance_text,
    credibility_summary,
    data_source_health_frame,
    p0_readiness_frame,
)
from stock_quant.data import (
    DataSourceError,
    append_spot_bar,
    fetch_daily_history,
    fetch_global_indices,
    fetch_market_news,
    fetch_spot,
    global_risk_summary,
    load_cached_spot_snapshot,
    market_breadth,
    plain_code,
    prefixed_symbol,
)
from stock_quant.health import assess_market_data_status, assess_recommendation_freshness
from stock_quant.indicators import add_indicators
from stock_quant.leaders import LeaderBundle, fetch_leader_bundle, leader_switches
from stock_quant.logging_config import configure_application_logging
from stock_quant.miniapp_feed import publish_market_feed_if_configured
from stock_quant.notify import send_webhook
from stock_quant.presentation import (
    PLOTLY_CONFIG,
    RUN_STATUS_LABELS,
    format_date,
    format_datetime,
    format_time,
    today_chinese,
)
from stock_quant.position import PositionPlan, analyze_position
from stock_quant.product import (
    ALERT_TYPES,
    CONDITION_LABELS,
    STRATEGY_TYPES,
    build_ai_research_table,
    build_daily_review_report,
    build_market_map,
    build_paper_trade_candidates,
    credibility_display,
    credibility_explanation,
    enrich_recommendations_for_product,
    evaluate_alert_rules,
    filter_by_conditions,
    paper_trade_summary,
    p1_paid_value_frame,
    research_brief,
    strategy_backtest_matrix,
)
from stock_quant.rotation import (
    board_flow_history,
    build_mainline_snapshot,
    build_rotation_heatmap,
)
from stock_quant.sentiment import SentimentBundle, fetch_sentiment_bundle
from stock_quant.recommender import (
    HORIZON_LABELS,
    display_recommendations,
    infer_industry,
    scan_recommendations,
)
from stock_quant.storage import (
    create_run,
    deactivate_watchlist_position,
    get_or_create_paper_account,
    load_alert_events,
    load_alert_rules,
    load_capital_hotspots,
    load_capital_hotspot_history,
    load_capital_hotspot_timeline,
    load_latest_market_snapshot,
    load_limit_up_ladder,
    load_daily_review_reports,
    latest_run,
    load_paper_accounts,
    load_paper_trades,
    load_recommendations,
    load_recommendation_outcomes,
    load_runs,
    load_sentiment_history,
    load_position_plan_snapshots,
    load_watchlist_positions,
    save_alert_events,
    save_alert_rule,
    save_capital_hotspots,
    save_daily_review_report,
    save_market_snapshot,
    save_paper_trades,
    save_position_plan_snapshot,
    save_recommendations,
    save_watchlist_position,
    save_sentiment_snapshot,
    save_stock_analysis,
    update_run,
)
from stock_quant.strategy import SignalReport, analyze_stock
from stock_quant.settings import STRATEGY_VERSION


configure_application_logging()


LOGGER = logging.getLogger(__name__)


BOOLEAN_LABELS = {
    True: "是",
    False: "否",
    1: "是",
    0: "否",
}

COMMON_VALUE_LABELS = {
    "short": "短期",
    "mid": "中期",
    "long": "长期",
    "running": "运行中",
    "done": "已完成",
    "failed": "失败",
    "buy": "买入",
    "sell": "卖出",
    "hold": "持有",
    "watch": "观察",
    "active": "启用",
    "disabled": "停用",
    "paused": "暂停",
    "opportunity": "机会",
    "risk": "风险",
}


DASHBOARD_WORKSPACES = {
    "今日决策": ("短中长期推荐", "每日复盘"),
    "选股研究": (
        "条件选股",
        "市场地图",
        "行业题材",
        "资金热点",
        "主线雷达",
        "情绪周期",
        "龙头追踪",
        "个股买卖点",
    ),
    "持仓风控": ("持仓中心", "预警中心", "模拟交易"),
    "复盘管理": ("策略回测", "数据表", "推送", "商业体检"),
}

DASHBOARD_MODULE_HINTS = {
    "短中长期推荐": "每天先看候选、买点、止损和策略类型，再决定是否进入自选观察。",
    "每日复盘": "收盘后复核推荐表现、市场状态与次日关注重点。",
    "条件选股": "按价格、策略、行业和风险条件缩小候选范围。",
    "市场地图": "快速识别市场最强行业、题材和资金聚集方向。",
    "行业题材": "查看本轮扫描使用的行业与题材强度。",
    "资金热点": "观察每日、每周、每月的主力资金方向。",
    "主线雷达": "跟踪板块从启动、加速到退潮的阶段变化。",
    "情绪周期": "结合涨停梯队、炸板率和连板高度判断市场情绪。",
    "龙头追踪": "比较板块龙一、龙二、龙三的走势与切换记录。",
    "个股买卖点": "分析单只股票的K线、风险位和个人持仓回本方案。",
    "持仓中心": "保存持仓并批量生成当日做T、止损和回本路径。",
    "预警中心": "集中查看买点、止损、资金与板块变化提醒。",
    "模拟交易": "先在模拟账户验证策略执行，再考虑真实交易。",
    "策略回测": "用历史数据检查策略收益、回撤和交易稳定性。",
    "数据表": "查看扫描记录、数据版本并导出推荐结果。",
    "推送": "把当日推荐摘要发送到已配置的通知通道。",
    "商业体检": "检查数据、会员、合规和运营功能的发布完整度。",
}


def friendly_data_source_error(exc: Exception, action: str) -> str:
    detail = str(exc)
    lowered = detail.lower()
    if "proxy" in lowered or "remote end closed" in lowered:
        return (
            f"{action}暂不可用：系统已尝试直连和系统代理，行情源仍未响应。"
            "其他功能不受影响，可稍后重试；已有缓存时系统会自动使用最近成功结果。"
        )
    if "timeout" in lowered or "timed out" in lowered:
        return f"{action}失败：行情数据源响应超时，请稍后重试。"
    if "ssl" in lowered or "certificate" in lowered:
        return f"{action}失败：行情数据源的安全连接异常，请检查服务器时间和证书环境。"
    if "connection" in lowered or "无法连接" in detail:
        return f"{action}失败：暂时无法连接行情数据源，请检查网络后重试。"
    return f"{action}失败：行情数据暂时不可用，请稍后重试。"


st.set_page_config(page_title="A股每日量化推荐", layout="wide")

st.markdown(
    """
    <style>
    .block-container { padding-top: 1.1rem; padding-bottom: 2.5rem; max-width: 1460px; }
    div[data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 12px 14px;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }
    .risk-note {
        border-left: 4px solid #2563eb;
        background: #eff6ff;
        padding: 10px 12px;
        border-radius: 6px;
        color: #1f2937;
        font-size: 0.92rem;
        margin: 0.35rem 0 1rem 0;
    }
    .metric-row {
        display: flex;
        gap: 10px;
        flex-wrap: nowrap;
        overflow-x: auto;
        padding-bottom: 4px;
        margin: 0.4rem 0 1.1rem 0;
    }
    .metric-card {
        min-width: 124px;
        flex: 1 0 124px;
        box-sizing: border-box;
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 12px 14px;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }
    .metric-card-wide { min-width: 220px; flex-basis: 220px; }
    .metric-label { font-size: 0.82rem; color: #6b7280; margin-bottom: 6px; white-space: nowrap; }
    .metric-value {
        font-size: 1.12rem;
        line-height: 1.25;
        color: #111827;
        font-weight: 700;
        white-space: normal;
        overflow-wrap: anywhere;
    }
    .metric-card-wide .metric-value { white-space: nowrap; font-size: 1.05rem; }
    .data-status {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 9px 12px;
        margin: 0.15rem 0 0.7rem 0;
        border: 1px solid #d1d5db;
        border-left-width: 4px;
        border-radius: 6px;
        background: #ffffff;
        color: #374151;
        font-size: 0.88rem;
        line-height: 1.45;
    }
    .data-status strong { color: #111827; }
    .data-status--fresh { border-left-color: #16a34a; background: #f0fdf4; }
    .data-status--degraded, .data-status--cached { border-left-color: #d97706; background: #fffbeb; }
    .data-status--stale, .data-status--unavailable { border-left-color: #dc2626; background: #fef2f2; }
    .workspace-hint {
        color: #6b7280;
        font-size: 0.88rem;
        padding-top: 0.4rem;
    }
    .pill {
        display: inline-block;
        padding: 4px 9px;
        border-radius: 999px;
        background: #f3f4f6;
        color: #374151;
        font-size: 0.84rem;
        margin: 0 6px 6px 0;
    }
    .section-title { font-size: 1.05rem; font-weight: 700; margin: 0.4rem 0 0.2rem 0; }
    </style>
    """,
    unsafe_allow_html=True,
)


def record_activity(
    query_type: str,
    stock_symbol: str | None = None,
    stock_name: str | None = None,
    request_params: dict[str, Any] | None = None,
    result_snapshot: dict[str, Any] | None = None,
) -> bool:
    user = st.session_state.get("auth_user")
    if not user or os.getenv("AUTH_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
        return False
    try:
        from stock_quant.auth import record_user_query

        record_user_query(
            int(user["id"]),
            query_type,
            stock_symbol=stock_symbol,
            stock_name=stock_name,
            request_params=request_params,
            result_snapshot=result_snapshot,
        )
        return True
    except Exception:
        return False


def current_owner_key() -> str:
    user = st.session_state.get("auth_user")
    if user and user.get("id"):
        return f"user:{user['id']}"
    return "local"


def initialize_position_inputs() -> None:
    user = st.session_state.get("auth_user")
    owner = current_owner_key()
    if st.session_state.get("position_defaults_owner") == owner:
        return

    base_defaults: dict[str, Any] = {
        "query": "平安银行",
        "years": 2,
        "holding_cost": 10.0,
        "holding_shares": 1000,
        "available_cash": 0.0,
        "t_ratio": 0.2,
        "account_assets": 0.0,
        "sellable_shares": 1000,
        "t_preference": "自动判断",
        "slippage_bps": 10,
    }
    defaults = dict(base_defaults)
    if user and os.getenv("AUTH_ENABLED", "false").lower() in {"1", "true", "yes", "on"}:
        try:
            from stock_quant.auth import load_latest_position_params

            defaults.update(load_latest_position_params(int(user["id"])))
        except Exception:
            LOGGER.exception("读取用户上次持仓参数失败")

    def safe_int(name: str, minimum: int, fallback: int) -> int:
        try:
            return max(minimum, int(float(defaults.get(name, fallback))))
        except (TypeError, ValueError, OverflowError):
            return fallback

    def safe_float(name: str, minimum: float, fallback: float) -> float:
        try:
            value = float(defaults.get(name, fallback))
            if not pd.notna(value):
                return fallback
            return max(minimum, value)
        except (TypeError, ValueError, OverflowError):
            return fallback

    years_options = [1, 2, 3, 5]
    ratio_options = [10, 15, 20, 25, 30, 40, 50]
    mode_options = ["自动判断", "先卖后买", "先买后卖"]
    slippage_options = [0, 5, 10, 15, 20, 30]
    shares = safe_int("holding_shares", 100, 1000) // 100 * 100
    sellable = min(shares, safe_int("sellable_shares", 0, shares)) // 100 * 100
    ratio_raw = safe_float("t_ratio", 0.0, 0.2)
    ratio_value = round(ratio_raw * 100 if ratio_raw <= 1 else ratio_raw)
    years = safe_int("years", 1, 2)

    st.session_state["position_input_query"] = str(defaults.get("query") or base_defaults["query"])
    st.session_state["position_input_years"] = years if years in years_options else 2
    st.session_state["position_input_holding_cost"] = safe_float("holding_cost", 0.01, 10.0)
    st.session_state["position_input_holding_shares"] = shares
    st.session_state["position_input_available_cash"] = safe_float("available_cash", 0.0, 0.0)
    st.session_state["position_input_t_ratio"] = ratio_value if ratio_value in ratio_options else 20
    st.session_state["position_input_account_assets"] = safe_float("account_assets", 0.0, 0.0)
    st.session_state["position_input_sellable_shares"] = sellable
    mode = str(defaults.get("t_preference") or base_defaults["t_preference"])
    st.session_state["position_input_t_preference"] = mode if mode in mode_options else "自动判断"
    slippage = safe_int("slippage_bps", 0, 10)
    st.session_state["position_input_slippage_bps"] = slippage if slippage in slippage_options else 10
    st.session_state["position_defaults_owner"] = owner


@st.cache_data(ttl=1800, show_spinner=False)
def load_history(symbol: str, years: int, ignore_proxy: bool) -> pd.DataFrame:
    start = date.today() - timedelta(days=years * 365 + 90)
    return fetch_daily_history(symbol, start_date=start, adjust="qfq", ignore_proxy=ignore_proxy)


@st.cache_data(ttl=60, show_spinner=False)
def load_spot_for_lookup(ignore_proxy: bool) -> pd.DataFrame:
    return fetch_spot(ignore_proxy=ignore_proxy)


@st.cache_data(ttl=60, show_spinner=False)
def load_market_dashboard(ignore_proxy: bool) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame, dict[str, Any]]:
    spot = fetch_spot(ignore_proxy=ignore_proxy)
    breadth = market_breadth(spot)
    try:
        global_indices = fetch_global_indices(ignore_proxy=ignore_proxy)
        global_summary = global_risk_summary(global_indices)
    except Exception:
        global_indices = pd.DataFrame()
        global_summary = {}
    return spot, breadth, global_indices, global_summary


@st.cache_data(ttl=300, show_spinner=False)
def load_news_context(symbol: str, ignore_proxy: bool) -> tuple[pd.DataFrame, list[str]]:
    return fetch_market_news(symbol, ignore_proxy=ignore_proxy, timeout=4.0)


@st.cache_data(ttl=300, show_spinner=False)
def load_leader_bundle(
    board_type: str,
    board_name: str,
    lookback_days: int,
    ignore_proxy: bool,
    fallback_recommendations: pd.DataFrame,
) -> LeaderBundle:
    return fetch_leader_bundle(
        board_type,
        board_name,
        lookback_days,
        ignore_proxy,
        fallback_recommendations,
    )


def parse_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if value is None or pd.isna(value):
        return []
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item)]
    except Exception:
        pass
    if "、" in text:
        return [item for item in text.split("、") if item]
    return [text]


def hydrate(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    for column in ("topics", "reasons", "sell_triggers", "warnings"):
        if column in out.columns:
            out[column] = out[column].map(parse_list)
    if "topics" in out.columns:
        out["topic_text"] = out["topics"].map(lambda values: "、".join(values))
        if "industry" in out.columns:
            def industry_value(row: pd.Series) -> str:
                value = row.get("industry")
                if value is not None and not pd.isna(value) and str(value).strip():
                    return str(value)
                return infer_industry(row["topics"])

            out["industry"] = out.apply(industry_value, axis=1)
    else:
        out["topic_text"] = ""
    return out


def metric_row(items: list[tuple[str, str]]) -> None:
    parts = ['<div class="metric-row">']
    for label, value in items:
        label_text = str(label)
        value_text = str(value)
        is_wide = any(keyword in label_text for keyword in ("日期", "时间", "版本", "开通", "到期")) or len(value_text) >= 12
        card_class = "metric-card metric-card-wide" if is_wide else "metric-card"
        parts.append(
            f'<div class="{card_class}"><div class="metric-label">{html_lib.escape(label_text)}</div>'
            f'<div class="metric-value">{html_lib.escape(value_text)}</div></div>'
        )
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


DISPLAY_COLUMN_LABELS = {
    "id": "编号",
    "run_id": "扫描编号",
    "trade_date": "数据日期",
    "created_at": "创建时间",
    "updated_at": "更新时间",
    "symbol": "代码",
    "code": "代码",
    "name": "名称",
    "horizon": "周期",
    "priority": "优先级",
    "strategy_type": "策略打法",
    "industry": "行业",
    "topics": "题材",
    "topic_text": "题材",
    "score": "综合评分",
    "base_score": "技术评分",
    "rating": "级别",
    "action": "操作建议",
    "credibility_score": "可信度评分",
    "credibility_label": "可信度标签",
    "credibility_sample_count": "复盘样本数",
    "history_win_rate": "历史胜率",
    "avg_t1_return": "T+1平均收益",
    "avg_t3_return": "T+3平均收益",
    "avg_t5_return": "T+5平均收益",
    "avg_t20_return": "T+20平均收益",
    "worst_max_drawdown": "最大回撤",
    "stop_trigger_rate": "止损触发率",
    "target_touch_rate": "目标触达率",
    "high_win_repeat": "高胜率重复命中",
    "close": "收盘价",
    "buy_zone_low": "买点下沿",
    "buy_zone_high": "买点上沿",
    "stop_loss": "止损价",
    "take_profit_1": "目标价1",
    "take_profit_2": "目标价2",
    "trailing_stop": "移动止盈",
    "position_pct": "建议仓位",
    "net_inflow_3d": "3日净流入",
    "net_inflow_5d": "5日净流入",
    "net_inflow_10d": "10日净流入",
    "pe_est": "估算市盈率",
    "pb_est": "估算市净率",
    "operating_cash_flow": "经营现金流",
    "dividend_year_ratio": "分红稳定度",
    "reasons": "推荐原因",
    "sell_triggers": "卖出条件",
    "warnings": "风险提示",
    "owner_key": "账号标识",
    "alert_type": "预警类型",
    "comparator": "比较方式",
    "threshold_value": "阈值",
    "enabled": "是否启用",
    "note": "备注",
    "severity": "提醒级别",
    "observed_value": "触发值",
    "message": "提醒内容",
    "position_id": "持仓编号",
    "cost_price": "持仓成本",
    "shares": "持股数量",
    "available_cash": "可用资金",
    "account_assets": "账户总资产",
    "sellable_shares": "可卖底仓",
    "t_ratio": "做T比例",
    "t_preference": "做T方式",
    "current_price": "当前价",
    "t_buy_low": "低吸下沿",
    "t_buy_high": "低吸上沿",
    "t_sell_low": "高抛下沿",
    "t_sell_high": "高抛上沿",
    "feasibility_score": "可行性评分",
    "feasibility_label": "可行性",
    "action_summary": "执行摘要",
    "recovery_price_after_one": "一轮后成本",
    "recovery_price_after_three": "三轮后成本",
    "period": "周期",
    "category": "类型",
    "rank": "排名",
    "board_type": "板块类型",
    "board_name": "行业/题材",
    "net_inflow_yi": "主力净流入（亿元）",
    "change_pct": "涨跌幅%",
    "leader": "领涨股",
    "leader_change_pct": "领涨幅%",
    "leader_label": "龙头级别",
    "leader_score": "龙头分",
    "relative_strength": "相对强弱",
    "max_drawdown": "最大回撤",
    "trade_count": "交易次数",
    "win_rate": "胜率",
    "annual_return": "年化收益",
    "total_return": "累计收益",
    "benchmark_return": "基准收益",
    "excess_return": "超额收益",
    "available_days": "可跟踪天数",
    "t1_return": "T+1收益",
    "t3_return": "T+3收益",
    "t5_return": "T+5收益",
    "t20_return": "T+20收益",
    "max_drawdown_20": "20日最大回撤",
    "stop_hit": "止损命中",
    "target1_hit": "目标一触达",
    "query": "查询内容",
    "request_params": "请求参数",
    "result_snapshot": "结果快照",
    "action_type": "操作类型",
    "ip_address": "IP地址",
    "user_agent": "浏览器信息",
    "login_name": "登录名",
    "mobile": "手机号",
    "real_name": "姓名",
    "roles": "角色",
    "status": "状态",
    "service_status": "会员状态",
    "start_at": "开通时间",
    "expire_at": "到期时间",
    "last_login_at": "最近登录",
    "login_count": "登录次数",
    "plan_code": "套餐编码",
    "plan_name": "套餐名称",
    "duration_days": "有效天数",
    "price_yuan": "价格（元）",
    "is_active": "是否启用",
    "order_no": "订单号",
    "amount_yuan": "金额（元）",
    "payment_method": "支付方式",
    "paid_at": "支付时间",
    "channel_type": "通道类型",
    "webhook_url": "Webhook地址",
}


def localize_dataframe(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    display = frame.copy()
    date_columns = {"trade_date", "data_date", "report_date", "event_date", "usage_date", "order_date"}
    datetime_columns = {
        "run_time",
        "refresh_time",
        "last_activity_at",
        "created_at",
        "updated_at",
        "start_at",
        "expire_at",
        "last_login_at",
        "paid_at",
    }
    for column in display.columns:
        if column in date_columns:
            display[column] = display[column].map(format_date)
        elif column in datetime_columns or column.endswith("_at"):
            display[column] = display[column].map(format_datetime)
    if "horizon" in display.columns:
        display["horizon"] = display["horizon"].map(lambda value: HORIZON_LABELS.get(str(value), str(value)))
    if "high_win_repeat" in display.columns:
        display["high_win_repeat"] = display["high_win_repeat"].map(lambda value: "是" if bool(value) else "否")
    if "enabled" in display.columns:
        display["enabled"] = display["enabled"].map(lambda value: "启用" if bool(value) else "停用")
    for column in display.columns:
        if column in DISPLAY_COLUMN_LABELS:
            continue
        if display[column].dtype == bool:
            display[column] = display[column].map(lambda value: BOOLEAN_LABELS.get(value, value))
    for column in ("status", "service_status", "action", "side", "severity", "horizon", "period"):
        if column in display.columns:
            display[column] = display[column].map(lambda value: COMMON_VALUE_LABELS.get(str(value), value))
    return display.rename(columns={col: DISPLAY_COLUMN_LABELS.get(col, col) for col in display.columns})


def pct(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.2%}"


def yi(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value) / 100_000_000:.2f}亿"


def recommendation_summary(frame: pd.DataFrame, run: dict[str, Any] | None) -> None:
    if frame.empty:
        metric_row(
            [
                ("数据日期", "-"),
                ("推荐总数", "0"),
                ("重点", "0"),
                ("短期", "0"),
                ("中期", "0"),
                ("长期", "0"),
            ]
        )
        return
    date_text = format_date(frame["trade_date"].iloc[0]) if "trade_date" in frame.columns else "-"
    run_text = format_time(run["run_time"]) if run else "-"
    metric_row(
        [
            ("数据日期", date_text),
            ("刷新时间", run_text),
            ("策略版本", str((run or {}).get("strategy_version") or STRATEGY_VERSION)),
            ("推荐总数", str(len(frame))),
            ("重点", str((frame["priority"] == "重点").sum())),
            ("短期", str((frame["horizon"] == "short").sum())),
            ("中期", str((frame["horizon"] == "mid").sum())),
            ("长期", str((frame["horizon"] == "long").sum())),
        ]
    )


def market_summary(
    breadth: dict[str, Any],
    global_summary: dict[str, Any],
) -> None:
    if not breadth:
        metric_row([("市场数据", "暂不可用")])
        return
    turnover_yi = float(breadth.get("turnover", 0)) / 100_000_000
    global_text = str(global_summary.get("label", "暂缺"))
    metric_row(
        [
            ("全市场股票", f"{int(breadth['total']):,}"),
            ("上涨", f"{int(breadth['up']):,}"),
            ("下跌", f"{int(breadth['down']):,}"),
            ("平盘", f"{int(breadth['flat']):,}"),
            ("涨停估算", str(int(breadth["limit_up"]))),
            ("跌停估算", str(int(breadth["limit_down"]))),
            ("成交额", f"{turnover_yi:,.0f}亿"),
            ("市场温度", f"{breadth['temperature_label']} {breadth['temperature']:.0f}"),
            ("全球风险", global_text),
        ]
    )
    st.caption(
        f"上涨占比 {pct(float(breadth['up_ratio']))} · "
        f"涨跌家数比 {float(breadth['adv_dec_ratio']):.2f} · "
        f"涨跌幅中位数 {float(breadth['median_change']):.2f}% · "
        f"涨幅≥5% {int(breadth['strong'])}只 · 跌幅≤-5% {int(breadth['weak'])}只"
    )


def render_market_data_status(spot: pd.DataFrame) -> None:
    quote_date: Any = None
    source_warning = ""
    valid_price_count = 0
    if isinstance(spot, pd.DataFrame) and not spot.empty:
        quote_date = spot.attrs.get("quote_date")
        if not quote_date and "quote_date" in spot.columns:
            parsed_dates = pd.to_datetime(spot["quote_date"], errors="coerce").dropna()
            if not parsed_dates.empty:
                quote_date = parsed_dates.max().date()
        source_warning = str(spot.attrs.get("source_warning") or "")
        if "price" in spot.columns:
            valid_price_count = int(
                (pd.to_numeric(spot["price"], errors="coerce").fillna(0) > 0).sum()
            )

    status = assess_market_data_status(
        quote_date,
        len(spot) if isinstance(spot, pd.DataFrame) else 0,
        valid_price_count,
        source_warning=source_warning,
    )
    date_text = format_date(status.quote_date) if status.quote_date else "未知"
    summary = (
        f"<strong>行情状态：{html_lib.escape(status.message)}</strong>"
        f"<span>报价日 {html_lib.escape(date_text)} · "
        f"有效报价 {status.valid_price_count:,}/{status.row_count:,}只 · "
        f"来源 {html_lib.escape(status.source_label)}</span>"
    )
    st.markdown(
        f'<div class="data-status data-status--{status.level}">{summary}</div>',
        unsafe_allow_html=True,
    )
    if source_warning:
        st.caption("数据源说明：" + source_warning)


def latest_recommendation_set() -> tuple[dict[str, Any] | None, pd.DataFrame]:
    runs = load_runs(limit=20)
    if runs.empty:
        return latest_run(), pd.DataFrame()
    for _, row in runs.iterrows():
        if str(row.get("status", "")) != "done":
            continue
        frame = hydrate(load_recommendations(run_id=int(row["id"])))
        if not frame.empty:
            return row.to_dict(), frame
    run = runs.iloc[0].to_dict()
    return run, hydrate(load_recommendations(run_id=int(run["id"])))


def chart_price(history: pd.DataFrame, report: SignalReport | None = None) -> go.Figure:
    df = add_indicators(history)
    candle_hover = [
        (
            f"日期：{pd.to_datetime(row['date']).strftime('%Y年%m月%d日')}<br>"
            f"开盘：{float(row['open']):.2f}<br>"
            f"最高：{float(row['high']):.2f}<br>"
            f"最低：{float(row['low']):.2f}<br>"
            f"收盘：{float(row['close']):.2f}"
        )
        for _, row in df.iterrows()
    ]
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.62, 0.2, 0.18],
        vertical_spacing=0.035,
    )
    fig.add_trace(
        go.Candlestick(
            x=df["date"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="K线",
            hovertext=candle_hover,
            hoverinfo="text",
            increasing_line_color="#dc2626",
            decreasing_line_color="#059669",
            increasing_fillcolor="#fecaca",
            decreasing_fillcolor="#bbf7d0",
        ),
        row=1,
        col=1,
    )
    for column, label, color in [
        ("ma20", "20日均线", "#2563eb"),
        ("ma60", "60日均线", "#7c3aed"),
        ("ma120", "120日均线", "#6b7280"),
    ]:
        fig.add_trace(
            go.Scatter(x=df["date"], y=df[column], name=label, mode="lines", line=dict(width=1.4, color=color)),
            row=1,
            col=1,
        )
    fig.add_trace(go.Bar(x=df["date"], y=df["volume"], name="成交量", marker_color="#94a3b8"), row=2, col=1)
    fig.add_trace(go.Scatter(x=df["date"], y=df["macd"], name="MACD", line=dict(color="#2563eb", width=1)), row=3, col=1)
    fig.add_trace(
        go.Scatter(x=df["date"], y=df["macd_signal"], name="信号线", line=dict(color="#f59e0b", width=1)),
        row=3,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            x=df["date"],
            y=df["macd_hist"],
            name="MACD柱",
            marker_color=["#dc2626" if value >= 0 else "#059669" for value in df["macd_hist"].fillna(0)],
        ),
        row=3,
        col=1,
    )
    if report:
        for label, value, color in [
            ("买点上沿", report.buy_zone_high, "#2563eb"),
            ("买点下沿", report.buy_zone_low, "#2563eb"),
            ("止损", report.stop_loss, "#059669"),
            ("目标一", report.take_profit_1, "#dc2626"),
        ]:
            fig.add_hline(y=value, line_dash="dot", line_color=color, annotation_text=label, row=1, col=1)
    fig.update_layout(
        height=650,
        margin=dict(l=10, r=10, t=22, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        xaxis_rangeslider_visible=False,
        template="plotly_white",
    )
    fig.update_xaxes(tickformat="%Y年%m月", hoverformat="%Y年%m月%d日")
    return fig


def chart_backtest(curve: pd.DataFrame) -> go.Figure:
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.72, 0.28],
        vertical_spacing=0.08,
    )
    fig.add_trace(
        go.Scatter(
            x=curve["date"],
            y=curve["equity"],
            name="策略资金",
            line=dict(color="#2563eb", width=2),
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=curve["date"],
            y=curve["benchmark_equity"],
            name="买入持有",
            line=dict(color="#64748b", width=1.5, dash="dot"),
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=curve["date"],
            y=curve["drawdown"] * 100,
            name="回撤",
            fill="tozeroy",
            line=dict(color="#dc2626", width=1),
        ),
        row=2,
        col=1,
    )
    fig.update_yaxes(title_text="资金", row=1, col=1)
    fig.update_yaxes(title_text="回撤%", row=2, col=1)
    fig.update_layout(
        height=520,
        template="plotly_white",
        margin=dict(l=10, r=10, t=25, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    fig.update_xaxes(tickformat="%Y年%m月", hoverformat="%Y年%m月%d日")
    return fig


def chart_paper_equity(curve: pd.DataFrame) -> go.Figure:
    if curve.empty:
        return go.Figure()
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.72, 0.28],
        vertical_spacing=0.08,
    )
    fig.add_trace(
        go.Scatter(
            x=curve["trade_date"],
            y=curve["equity"],
            mode="lines+markers",
            name="模拟账户权益",
            line=dict(color="#2563eb", width=2),
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            x=curve["trade_date"],
            y=curve["pnl"],
            name="单日盈亏",
            marker_color=curve["pnl"].map(lambda value: "#dc2626" if value >= 0 else "#16a34a"),
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=curve["trade_date"],
            y=curve["drawdown"] * 100,
            name="回撤",
            fill="tozeroy",
            line=dict(color="#ef4444", width=1),
        ),
        row=2,
        col=1,
    )
    fig.update_yaxes(title_text="权益/盈亏", row=1, col=1)
    fig.update_yaxes(title_text="回撤%", row=2, col=1)
    fig.update_layout(
        height=460,
        template="plotly_white",
        margin=dict(l=10, r=10, t=25, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    return fig


def chart_boards(boards: pd.DataFrame) -> go.Figure:
    if boards.empty:
        return go.Figure()
    df = boards.sort_values("heat_score", ascending=False).head(16).copy()
    df["label"] = df["board_type"] + "：" + df["board_name"]
    fig = go.Figure(
        go.Bar(
            x=df["heat_score"],
            y=df["label"],
            orientation="h",
            marker_color=df["board_type"].map({"题材": "#2563eb", "行业": "#16a34a"}).fillna("#64748b"),
            text=df["change_pct"].map(lambda value: f"{value:.2f}%"),
            hovertemplate="%{y}<br>热度 %{x:.1f}<extra></extra>",
        )
    )
    fig.update_layout(
        height=430,
        template="plotly_white",
        margin=dict(l=10, r=10, t=18, b=10),
        xaxis_title="热点强度",
        yaxis=dict(autorange="reversed"),
    )
    return fig


def chart_capital_hotspots(frame: pd.DataFrame, period: str, category: str) -> go.Figure:
    data = frame[(frame["period"] == period) & (frame["category"] == category)].copy()
    if data.empty:
        return go.Figure()
    data = data.sort_values("net_inflow_yi", ascending=False).head(15).sort_values("net_inflow_yi")
    fig = go.Figure(
        go.Bar(
            x=data["net_inflow_yi"],
            y=data["name"],
            orientation="h",
            marker_color=["#dc2626" if value >= 0 else "#16a34a" for value in data["net_inflow_yi"].fillna(0)],
            text=data["net_inflow_yi"].map(lambda value: f"{value:.1f}亿"),
            textposition="auto",
            hovertemplate="%{y}<br>主力净流入 %{x:.2f}亿<extra></extra>",
        )
    )
    fig.update_layout(
        height=470,
        template="plotly_white",
        margin=dict(l=10, r=55, t=16, b=10),
        xaxis_title="主力资金净流入（亿元）",
    )
    return fig


def render_capital_hotspots(frame: pd.DataFrame) -> None:
    if frame.empty:
        st.info("刷新推荐后，这里会保存并显示每日、每周、每月的行业与题材资金热点。")
        return
    period_label = st.segmented_control(
        "观察周期",
        options=["每日", "每周", "每月"],
        default="每日",
        key="capital_period",
    )
    category = st.segmented_control(
        "热点类型",
        options=["行业", "题材"],
        default="行业",
        key="capital_category",
    )
    period = {"每日": "daily", "每周": "weekly", "每月": "monthly"}[period_label or "每日"]
    data = frame[(frame["period"] == period) & (frame["category"] == category)].copy()
    if data.empty:
        st.info("当前周期的资金热点数据暂不可用。")
        return
    st.plotly_chart(
        chart_capital_hotspots(frame, period, category),
        width="stretch",
        config=PLOTLY_CONFIG,
    )
    display = data.head(30).rename(
        columns={
            "rank": "排名",
            "name": "行业/题材",
            "net_inflow_yi": "主力净流入(亿)",
            "change_pct": "阶段涨跌幅%",
            "stock_count": "成分股数",
            "leader": "领涨股",
            "leader_change_pct": "领涨股涨幅%",
        }
    )
    columns = [
        column
        for column in ["排名", "行业/题材", "主力净流入(亿)", "阶段涨跌幅%", "成分股数", "领涨股", "领涨股涨幅%"]
        if column in display.columns
    ]
    st.dataframe(display[columns], width="stretch", hide_index=True)


def chart_rotation_heatmap(frame: pd.DataFrame) -> go.Figure:
    if frame.empty:
        return go.Figure()
    values = frame.fillna(0)
    text = values.map(lambda value: f"{value:.1f}")
    fig = go.Figure(
        go.Heatmap(
            z=values.values,
            x=values.columns,
            y=values.index,
            text=text.values,
            texttemplate="%{text}",
            colorscale=[
                [0.0, "#16a34a"],
                [0.5, "#f8fafc"],
                [1.0, "#dc2626"],
            ],
            zmid=0,
            colorbar=dict(title="亿元"),
            hovertemplate="%{y}<br>%{x|%Y年%m月%d日}<br>净流入 %{z:.2f}亿<extra></extra>",
        )
    )
    fig.update_layout(
        height=max(360, 42 * len(values.index)),
        template="plotly_white",
        margin=dict(l=10, r=10, t=18, b=10),
        xaxis_title="扫描日期",
        yaxis_title="行业/题材",
    )
    fig.update_xaxes(tickformat="%m月%d日")
    return fig


def chart_board_flow_trend(frame: pd.DataFrame, name: str) -> go.Figure:
    fig = go.Figure()
    if frame.empty:
        return fig
    period_labels = {"daily": "每日", "weekly": "每周", "monthly": "每月"}
    colors = {"daily": "#2563eb", "weekly": "#16a34a", "monthly": "#dc2626"}
    for period in ["daily", "weekly", "monthly"]:
        data = frame[frame["period"] == period]
        if data.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=data["trade_date"],
                y=data["net_inflow_yi"],
                name=period_labels[period],
                mode="lines+markers",
                line=dict(color=colors[period], width=2),
                hovertemplate="%{x|%Y年%m月%d日}<br>净流入 %{y:.2f}亿<extra>%{fullData.name}</extra>",
            )
        )
    fig.add_hline(y=0, line_color="#94a3b8", line_dash="dot")
    fig.update_layout(
        height=390,
        template="plotly_white",
        margin=dict(l=10, r=10, t=30, b=10),
        title=f"{name}资金趋势",
        yaxis_title="主力资金净流入（亿元）",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        hovermode="x unified",
    )
    fig.update_xaxes(tickformat="%m月%d日")
    return fig


def render_mainline_radar(
    capital_hotspots: pd.DataFrame,
    history: pd.DataFrame,
) -> None:
    st.subheader("市场主线雷达与板块轮动")
    st.caption(
        "综合每日、每周、每月资金排名、净流入、排名变化和近10次扫描持续性，"
        "判断板块处于启动、加速、主升、分化、退潮或观察阶段。阶段标签用于跟踪，不是买入指令。"
    )
    combined = pd.concat([history, capital_hotspots], ignore_index=True) if not history.empty else capital_hotspots.copy()
    if combined.empty:
        st.info("请先刷新每日推荐。系统积累多日热点记录后，轮动趋势会更有参考价值。")
        return

    category = st.segmented_control(
        "主线类型",
        ["行业", "题材"],
        default="行业",
        key="mainline_category",
    )
    snapshot = build_mainline_snapshot(combined, str(category))
    if snapshot.empty:
        st.info("当前类型暂无足够的资金热点数据。")
        return

    stage_counts = snapshot["stage"].value_counts()
    top = snapshot.iloc[0]
    metric_row(
        [
            ("当前主线", str(top["name"])),
            ("主线评分", f"{float(top['mainline_score']):.1f}"),
            ("生命周期", str(top["stage"])),
            ("10次强势天数", str(int(top["strong_days_10"]))),
            ("启动/加速方向", str(int(stage_counts.get("启动", 0) + stage_counts.get("加速", 0)))),
            ("分化/退潮方向", str(int(stage_counts.get("分化", 0) + stage_counts.get("退潮", 0)))),
        ]
    )

    display = snapshot.head(20).rename(
        columns={
            "name": "行业/题材",
            "mainline_score": "主线评分",
            "stage": "生命周期",
            "daily_flow": "每日净流入(亿)",
            "weekly_flow": "每周净流入(亿)",
            "monthly_flow": "每月净流入(亿)",
            "daily_rank": "每日排名",
            "rank_change": "排名变化",
            "strong_days_10": "近10次强势",
            "leader": "领涨股",
            "leader_change_pct": "领涨股涨幅%",
        }
    )
    visible_columns = [
        "行业/题材",
        "主线评分",
        "生命周期",
        "每日净流入(亿)",
        "每周净流入(亿)",
        "每月净流入(亿)",
        "每日排名",
        "排名变化",
        "近10次强势",
        "领涨股",
        "领涨股涨幅%",
    ]
    st.dataframe(
        display[[column for column in visible_columns if column in display.columns]],
        width="stretch",
        hide_index=True,
    )

    top_names = snapshot.head(12)["name"].astype(str).tolist()
    heatmap = build_rotation_heatmap(combined, str(category), top_names)
    st.markdown("**主力资金轮动热力图**")
    if heatmap.empty or len(heatmap.columns) < 2:
        st.caption("历史扫描日期不足。每日刷新后会逐步形成资金轮动热力图。")
    else:
        st.plotly_chart(chart_rotation_heatmap(heatmap), width="stretch", config=PLOTLY_CONFIG)

    detail_left, detail_right = st.columns([0.7, 0.3])
    with detail_left:
        selected_name = st.selectbox(
            "查看方向资金趋势",
            top_names,
            key="mainline_selected_name",
        )
    with detail_right:
        if st.button("带入龙头追踪", width="stretch"):
            st.session_state["leader_board_type"] = str(category)
            st.session_state["leader_board_name"] = str(selected_name)
            st.toast(f"已带入 {selected_name}，请切换到“龙头追踪”。")
    trend = board_flow_history(combined, str(category), str(selected_name))
    st.plotly_chart(chart_board_flow_trend(trend, str(selected_name)), width="stretch", config=PLOTLY_CONFIG)

    st.markdown("**阶段含义**")
    st.caption(
        "启动：日资金先转正；加速：日周共振且排名快速提升；主升：日周月资金同时为正；"
        "分化：短期转负但中期资金仍在；退潮：日周资金同步转负。"
    )


def _sentiment_stage_note(stage: str) -> str:
    notes = {
        "高潮": "高标和涨停数量活跃，但一致性过强后容易出现分化；避免只因情绪高涨追价。",
        "升温": "赚钱效应正在扩散，可重点观察二板晋级、主线前排和封板质量。",
        "修复": "情绪从弱势区回升，先看核心股能否持续晋级，不宜把单日反弹当成反转。",
        "分化": "涨停与炸板并存，资金开始筛选方向；优先强主线、低位换手和确定性较高的标的。",
        "退潮": "昨日涨停反馈、炸板或跌停压力偏弱，控制仓位并回避高位补跌风险。",
        "冰点": "市场接力意愿低，等待跌停压力下降、首板回暖和晋级率改善。",
        "震荡": "多空缺少一致方向，适合等待梯队结构与主线资金给出更清晰信号。",
    }
    return notes.get(stage, notes["震荡"])


def chart_sentiment_history(history: pd.DataFrame) -> go.Figure:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    if history.empty:
        return fig
    frame = history.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame = frame.dropna(subset=["trade_date"]).sort_values("trade_date")
    for column, label, color in [
        ("limit_up_count", "涨停数", "#dc2626"),
        ("broken_count", "炸板数", "#f59e0b"),
        ("limit_down_count", "跌停数", "#16a34a"),
    ]:
        fig.add_trace(
            go.Bar(
                x=frame["trade_date"],
                y=frame[column],
                name=label,
                marker_color=color,
                opacity=0.32,
            ),
            secondary_y=False,
        )
    fig.add_trace(
        go.Scatter(
            x=frame["trade_date"],
            y=frame["emotion_score"],
            name="情绪评分",
            mode="lines+markers+text",
            text=frame["emotion_stage"],
            textposition="top center",
            line=dict(color="#2563eb", width=3),
            marker=dict(size=8),
            hovertemplate="%{x|%Y年%m月%d日}<br>情绪评分 %{y:.1f}<extra></extra>",
        ),
        secondary_y=True,
    )
    fig.add_hrect(y0=75, y1=100, fillcolor="#fee2e2", opacity=0.18, line_width=0, secondary_y=True)
    fig.add_hrect(y0=0, y1=30, fillcolor="#dcfce7", opacity=0.18, line_width=0, secondary_y=True)
    fig.update_layout(
        height=450,
        template="plotly_white",
        margin=dict(l=10, r=10, t=20, b=10),
        barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="家数", secondary_y=False)
    fig.update_yaxes(title_text="情绪评分", range=[0, 105], secondary_y=True)
    fig.update_xaxes(tickformat="%m月%d日")
    return fig


def chart_limit_ladder(ladder: pd.DataFrame) -> go.Figure:
    if ladder.empty:
        return go.Figure()
    streaks = pd.to_numeric(ladder["streak"], errors="coerce").fillna(1).astype(int)
    labels = ["首板", "二板", "三板", "四板及以上"]
    values = [
        int(streaks.eq(1).sum()),
        int(streaks.eq(2).sum()),
        int(streaks.eq(3).sum()),
        int(streaks.ge(4).sum()),
    ]
    fig = go.Figure(
        go.Bar(
            x=labels,
            y=values,
            text=values,
            textposition="outside",
            marker_color=["#94a3b8", "#60a5fa", "#2563eb", "#dc2626"],
            hovertemplate="%{x}<br>%{y}只<extra></extra>",
        )
    )
    fig.update_layout(
        height=350,
        template="plotly_white",
        margin=dict(l=10, r=10, t=20, b=10),
        yaxis_title="涨停家数",
        showlegend=False,
    )
    return fig


def render_sentiment_cycle(breadth: dict[str, Any], ignore_proxy: bool) -> None:
    st.subheader("涨停梯队与市场情绪周期")
    st.caption(
        "综合涨停、炸板、跌停、封板率、连板高度、昨日涨停反馈、晋级率和全市场涨跌结构，"
        "识别冰点、修复、升温、高潮、分化和退潮。数据为研究与风控参考，不构成收益承诺。"
    )
    history = load_sentiment_history()
    previous_score = (
        float(history.iloc[0]["emotion_score"])
        if not history.empty and pd.notna(history.iloc[0].get("emotion_score"))
        else None
    )
    refresh_col, note_col = st.columns([0.25, 0.75])
    with refresh_col:
        refresh = st.button("刷新涨停梯队", type="primary", width="stretch")
    with note_col:
        st.write("刷新后保存当日快照；同一交易日重复刷新会更新该日记录，不会产生重复数据。")

    if refresh:
        try:
            with st.spinner("正在读取涨停、炸板、跌停和昨日涨停反馈"):
                bundle = fetch_sentiment_bundle(
                    breadth=breadth,
                    previous_score=previous_score,
                    ignore_proxy=ignore_proxy,
                )
                save_sentiment_snapshot(bundle.snapshot, bundle.limit_up)
                st.session_state["sentiment_bundle"] = bundle
                history = load_sentiment_history()
            record_activity(
                "sentiment_refresh",
                request_params={"trade_date": bundle.trade_date},
                result_snapshot=bundle.snapshot,
            )
            st.success(f"{format_date(bundle.trade_date)}情绪快照已更新。")
        except DataSourceError as exc:
            LOGGER.warning("刷新市场情绪数据源失败: %s", exc)
            st.error(friendly_data_source_error(exc, "刷新市场情绪"))
        except Exception:
            LOGGER.exception("刷新市场情绪失败")
            st.error("刷新市场情绪失败，详细原因已记录到服务器日志。")

    bundle = st.session_state.get("sentiment_bundle")
    snapshot: dict[str, Any] = {}
    if isinstance(bundle, SentimentBundle):
        snapshot = bundle.snapshot
    elif not history.empty:
        snapshot = history.iloc[0].to_dict()

    if not snapshot:
        st.info("尚无情绪快照。点击“刷新涨停梯队”后，系统会建立第一天的情绪基准。")
        return

    trade_date = str(snapshot.get("trade_date", ""))
    ladder = bundle.limit_up if isinstance(bundle, SentimentBundle) else load_limit_up_ladder(trade_date)
    metric_row(
        [
            ("数据日期", format_date(trade_date)),
            ("情绪阶段", str(snapshot.get("emotion_stage", "-"))),
            ("情绪评分", f"{float(snapshot.get('emotion_score', 0)):.1f}"),
            ("涨停", str(int(snapshot.get("limit_up_count", 0)))),
            ("炸板", str(int(snapshot.get("broken_count", 0)))),
            ("跌停", str(int(snapshot.get("limit_down_count", 0)))),
            ("封板率", f"{float(snapshot.get('seal_rate', 0)):.1%}"),
            ("连板高度", f"{int(snapshot.get('max_streak', 0))}板"),
            ("昨日涨停溢价", f"{float(snapshot.get('previous_premium', 0)):+.2f}%"),
        ]
    )
    stage = str(snapshot.get("emotion_stage", "震荡"))
    st.markdown(
        f'<div class="risk-note"><strong>当前判断：{stage}</strong>。'
        f'{_sentiment_stage_note(stage)}</div>',
        unsafe_allow_html=True,
    )

    left, right = st.columns([0.38, 0.62])
    with left:
        st.markdown("**涨停梯队结构**")
        st.plotly_chart(chart_limit_ladder(ladder), width="stretch", config=PLOTLY_CONFIG)
        metric_row(
            [
                ("首板", str(int(snapshot.get("first_board_count", 0)))),
                ("二板", str(int(snapshot.get("second_board_count", 0)))),
                ("三板", str(int(snapshot.get("third_board_count", 0)))),
                ("四板+", str(int(snapshot.get("high_board_count", 0)))),
            ]
        )
    with right:
        st.markdown("**市场情绪周期历史**")
        if len(history) < 2:
            st.info("目前只有一个交易日快照；连续刷新后会形成情绪周期曲线。")
        st.plotly_chart(chart_sentiment_history(history), width="stretch", config=PLOTLY_CONFIG)

    st.markdown("**连板梯队明细**")
    if ladder.empty:
        st.caption("当前快照没有可展示的涨停梯队明细。")
    else:
        ladder_display = ladder.copy()
        ladder_display["封板资金(亿元)"] = (
            pd.to_numeric(ladder_display["seal_amount"], errors="coerce") / 100_000_000
        ).round(2)
        ladder_display["成交额(亿元)"] = (
            pd.to_numeric(ladder_display["turnover"], errors="coerce") / 100_000_000
        ).round(2)
        ladder_display["梯队"] = pd.to_numeric(
            ladder_display["streak"], errors="coerce"
        ).fillna(1).astype(int).map(lambda value: f"{value}板")
        display = ladder_display.rename(
            columns={
                "symbol": "代码",
                "name": "名称",
                "change_pct": "涨幅%",
                "turnover_rate": "换手率%",
                "first_seal_time": "首次封板",
                "last_seal_time": "最后封板",
                "break_count": "炸板次数",
                "industry": "行业",
            }
        )
        st.dataframe(
            display[
                [
                    "梯队",
                    "代码",
                    "名称",
                    "行业",
                    "涨幅%",
                    "换手率%",
                    "封板资金(亿元)",
                    "成交额(亿元)",
                    "首次封板",
                    "最后封板",
                    "炸板次数",
                ]
            ],
            width="stretch",
            hide_index=True,
            height=430,
        )
        high = ladder.sort_values(
            ["streak", "seal_amount"], ascending=[False, False]
        ).head(4)
        st.markdown("**带入个股买卖点分析**")
        columns = st.columns(max(1, len(high)))
        for column, (_, item) in zip(columns, high.iterrows()):
            with column:
                if st.button(
                    f"{int(item['streak'])}板 · {item['name']}",
                    key=f"sentiment_to_stock_{item['symbol']}",
                    width="stretch",
                ):
                    st.session_state["position_input_query"] = str(item["symbol"]).upper()
                    st.toast(f"已带入{item['name']}，请切换到“个股买卖点”。")

    if isinstance(bundle, SentimentBundle) and not bundle.previous.empty:
        st.markdown("**昨日涨停反馈**")
        previous = bundle.previous.copy()
        previous["change_pct"] = pd.to_numeric(previous["change_pct"], errors="coerce")
        feedback_left, feedback_right = st.columns(2)
        columns = ["symbol", "name", "change_pct", "previous_streak", "industry"]
        with feedback_left:
            st.caption("反馈较强")
            st.dataframe(
                previous.nlargest(8, "change_pct")[columns].rename(
                    columns={
                        "symbol": "代码",
                        "name": "名称",
                        "change_pct": "今日涨幅%",
                        "previous_streak": "昨日连板数",
                        "industry": "行业",
                    }
                ),
                width="stretch",
                hide_index=True,
            )
        with feedback_right:
            st.caption("反馈较弱")
            st.dataframe(
                previous.nsmallest(8, "change_pct")[columns].rename(
                    columns={
                        "symbol": "代码",
                        "name": "名称",
                        "change_pct": "今日涨幅%",
                        "previous_streak": "昨日连板数",
                        "industry": "行业",
                    }
                ),
                width="stretch",
                hide_index=True,
            )
        if bundle.errors:
            with st.expander(f"数据源提示（{len(bundle.errors)}条）"):
                st.write("\n".join(bundle.errors[-20:]))


def chart_leader_curve(bundle: LeaderBundle) -> go.Figure:
    fig = go.Figure()
    if bundle.curve.empty:
        return fig
    colors = {
        "龙一": "#dc2626",
        "龙二": "#2563eb",
        "龙三": "#16a34a",
        "龙头等权指数": "#111827",
        "沪深300": "#94a3b8",
    }
    for column in bundle.curve.columns:
        if column == "date":
            continue
        prefix = next((key for key in colors if str(column).startswith(key)), "")
        fig.add_trace(
            go.Scatter(
                x=bundle.curve["date"],
                y=bundle.curve[column],
                name=str(column),
                mode="lines",
                line=dict(
                    color=colors.get(prefix, "#64748b"),
                    width=3 if prefix in {"龙一", "龙头等权指数"} else 2,
                    dash="dash" if prefix == "沪深300" else "solid",
                ),
                hovertemplate="%{x|%Y年%m月%d日}<br>相对净值 %{y:.2f}<extra>%{fullData.name}</extra>",
            )
        )
    fig.add_hline(y=100, line_color="#cbd5e1", line_dash="dot")
    fig.update_layout(
        height=500,
        template="plotly_white",
        margin=dict(l=10, r=10, t=20, b=10),
        yaxis_title="归一化净值（起点=100）",
        xaxis_title="交易日期",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        hovermode="x unified",
    )
    fig.update_xaxes(tickformat="%m月%d日", hoverformat="%Y年%m月%d日")
    return fig


def chart_leader_strength(bundle: LeaderBundle) -> go.Figure:
    if bundle.metrics.empty:
        return go.Figure()
    frame = bundle.metrics.copy()
    frame["标签"] = frame["排名"] + " " + frame["名称"]
    fig = go.Figure()
    for column, color in [
        ("5日涨幅%", "#2563eb"),
        ("10日涨幅%", "#16a34a"),
        ("20日涨幅%", "#dc2626"),
    ]:
        fig.add_trace(go.Bar(x=frame["标签"], y=frame[column], name=column, marker_color=color))
    fig.update_layout(
        height=380,
        template="plotly_white",
        margin=dict(l=10, r=10, t=20, b=10),
        yaxis_title="阶段涨跌幅%",
        barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    return fig


def render_leader_tracking(
    capital_hotspots: pd.DataFrame,
    boards: pd.DataFrame,
    ignore_proxy: bool,
) -> None:
    st.subheader("行业与题材龙头曲线")
    st.caption(
        "按成分股当日涨幅、成交额、换手率和量比综合识别龙一、龙二、龙三；"
        "曲线以观察期起点归一为100，并与沪深300比较。龙头是相对排名，不代表未来必涨。"
    )

    options = pd.DataFrame()
    if not capital_hotspots.empty:
        options = (
            capital_hotspots[["category", "name", "rank", "net_inflow_yi"]]
            .dropna(subset=["name"])
            .drop_duplicates(["category", "name"])
            .copy()
        )
    if options.empty and not boards.empty:
        options = boards.rename(columns={"board_type": "category", "board_name": "name"})[
            ["category", "name"]
        ].drop_duplicates()
        options["rank"] = pd.NA
        options["net_inflow_yi"] = pd.NA
    if options.empty:
        st.info("请先刷新一次每日推荐，系统获得行业和题材热点后即可生成龙头曲线。")
        return

    categories = [item for item in ["行业", "题材"] if item in set(options["category"].astype(str))]
    control_1, control_2, control_3, control_4 = st.columns([0.18, 0.37, 0.2, 0.25])
    with control_1:
        board_type = st.segmented_control(
            "板块类型",
            categories or sorted(options["category"].astype(str).unique()),
            default=(categories or sorted(options["category"].astype(str).unique()))[0],
            key="leader_board_type",
        )
    selected_options = options[options["category"].astype(str) == str(board_type)].copy()
    selected_options = selected_options.sort_values(["rank", "net_inflow_yi"], na_position="last")
    board_names = selected_options["name"].astype(str).tolist()
    with control_2:
        board_name = st.selectbox("行业/题材", board_names, key="leader_board_name")
    with control_3:
        lookback = st.selectbox(
            "观察周期",
            [20, 40, 60, 120],
            index=2,
            format_func=lambda value: f"{value}个交易日",
            key="leader_lookback",
        )
    with control_4:
        generate = st.button("生成龙头曲线", type="primary", width="stretch")

    bundle_key = f"{board_type}|{board_name}|{lookback}"
    if generate:
        try:
            current_run = latest_run()
            fallback_recommendations = (
                load_recommendations(run_id=int(current_run["id"]))
                if current_run and current_run.get("id")
                else pd.DataFrame()
            )
            with st.spinner(f"正在识别{board_name}龙头并计算相对强弱"):
                st.session_state["leader_bundle"] = load_leader_bundle(
                    str(board_type),
                    str(board_name),
                    int(lookback),
                    ignore_proxy,
                    fallback_recommendations,
                )
                st.session_state["leader_bundle_key"] = bundle_key
                for key in [
                    "leader_selected_stock",
                    "leader_kline_history",
                    "leader_kline_report",
                    "leader_kline_cache_key",
                ]:
                    st.session_state.pop(key, None)
            record_activity(
                "leader_tracking",
                request_params={
                    "board_type": board_type,
                    "board_name": board_name,
                    "lookback": lookback,
                },
            )
        except DataSourceError as exc:
            LOGGER.warning("生成龙头曲线数据源失败: %s", exc)
            st.warning(friendly_data_source_error(exc, "生成龙头曲线"))
        except Exception:
            LOGGER.exception("生成龙头曲线失败")
            st.error("生成龙头曲线失败，详细原因已记录到服务器日志。")

    bundle = st.session_state.get("leader_bundle")
    if not isinstance(bundle, LeaderBundle) or st.session_state.get("leader_bundle_key") != bundle_key:
        st.info("选择板块和观察周期后，点击“生成龙头曲线”。")
        return

    if not bundle.metrics.empty:
        top = bundle.metrics.iloc[0]
        leader_index_return = (
            float(bundle.curve["龙头等权指数"].iloc[-1] - 100)
            if "龙头等权指数" in bundle.curve.columns
            else 0.0
        )
        benchmark_return = (
            float(bundle.curve["沪深300"].iloc[-1] - 100)
            if "沪深300" in bundle.curve.columns
            else None
        )
        relative_text = (
            f"{leader_index_return - benchmark_return:+.2f}%"
            if benchmark_return is not None
            else "基准暂缺"
        )
        metric_row(
            [
                ("当前龙一", str(top["名称"])),
                ("龙一评分", f"{float(top['龙头分']):.1f}"),
                ("龙一20日涨幅", f"{float(top['20日涨幅%']):.2f}%" if pd.notna(top["20日涨幅%"]) else "-"),
                ("龙头等权涨幅", f"{leader_index_return:.2f}%"),
                ("相对沪深300", relative_text),
                ("龙一最大回撤", f"{float(top['20日最大回撤%']):.2f}%"),
            ]
        )
    source_note = getattr(bundle, "source_note", "")
    if source_note:
        st.caption(source_note)

    st.plotly_chart(chart_leader_curve(bundle), width="stretch", config=PLOTLY_CONFIG)
    left, right = st.columns([0.58, 0.42])
    with left:
        st.markdown("**龙一、龙二、龙三阶段强度**")
        st.plotly_chart(chart_leader_strength(bundle), width="stretch", config=PLOTLY_CONFIG)
    with right:
        st.markdown("**龙头风险与交易特征**")
        st.dataframe(bundle.metrics, width="stretch", hide_index=True)

    st.markdown("**带入个股买卖点分析**")
    leader_columns = st.columns(max(1, len(bundle.leaders)))
    for column, (_, leader) in zip(leader_columns, bundle.leaders.iterrows()):
        with column:
            if st.button(
                f"{leader['leader_label']} · {leader['name']}",
                key=f"leader_to_stock_{leader['symbol']}",
                width="stretch",
            ):
                st.session_state["position_input_query"] = str(leader["symbol"]).upper()
                st.session_state["leader_selected_stock"] = {
                    "symbol": str(leader["symbol"]).upper(),
                    "name": str(leader["name"]),
                    "label": str(leader["leader_label"]),
                }
                st.toast(f"已打开 {leader['name']} 的K线图，并同步带入个股买卖点分析。")

    selected_leader = st.session_state.get("leader_selected_stock")
    if isinstance(selected_leader, dict) and selected_leader.get("symbol"):
        st.markdown("**龙头个股K线图**")
        year_col, action_col, hint_col = st.columns([0.18, 0.18, 0.64])
        with year_col:
            leader_k_years = st.selectbox(
                "K线年限",
                [1, 2, 3, 5],
                index=1,
                key="leader_kline_years",
            )
        with action_col:
            refresh_kline = st.button("刷新K线", width="stretch", key="leader_refresh_kline")
        with hint_col:
            st.caption(
                f"当前查看：{selected_leader.get('label', '')} · "
                f"{selected_leader.get('symbol')} {selected_leader.get('name', '')}"
            )
        cache_key = f"{selected_leader.get('symbol')}|{leader_k_years}"
        if refresh_kline or st.session_state.get("leader_kline_cache_key") != cache_key:
            try:
                with st.spinner(f"正在加载 {selected_leader.get('name', selected_leader.get('symbol'))} 的K线数据"):
                    kline_history = load_history(str(selected_leader["symbol"]), int(leader_k_years), ignore_proxy)
                    report = analyze_stock(
                        kline_history,
                        symbol=str(selected_leader["symbol"]),
                        name=str(selected_leader.get("name") or ""),
                    )
                st.session_state["leader_kline_history"] = kline_history
                st.session_state["leader_kline_report"] = report
                st.session_state["leader_kline_cache_key"] = cache_key
            except DataSourceError as exc:
                st.warning(friendly_data_source_error(exc, "加载龙头K线"))
            except Exception:
                LOGGER.exception("加载龙头个股K线失败")
                st.error("加载K线失败，详细原因已记录到服务器日志。")
        kline_history = st.session_state.get("leader_kline_history")
        report = st.session_state.get("leader_kline_report")
        if isinstance(kline_history, pd.DataFrame) and not kline_history.empty:
            if isinstance(report, SignalReport):
                metric_row(
                    [
                        ("代码", str(report.symbol)),
                        ("名称", str(report.name)),
                        ("评分", f"{report.score:.0f}"),
                        ("级别", report.rating),
                        ("建议仓位", f"{report.position_pct:.0%}"),
                        ("收盘价", f"{report.close:.2f}"),
                    ]
                )
            st.plotly_chart(
                chart_price(
                    kline_history,
                    report if isinstance(report, SignalReport) and report.position_pct > 0 else None,
                ),
                width="stretch",
                config=PLOTLY_CONFIG,
            )
            if isinstance(report, SignalReport):
                st.info(
                    f"买点 {report.buy_zone_low:.2f}-{report.buy_zone_high:.2f}；"
                    f"止损 {report.stop_loss:.2f}；"
                    f"目标 {report.take_profit_1:.2f}/{report.take_profit_2:.2f}。"
                )

    history = load_capital_hotspot_history(str(board_type), str(board_name))
    switches = leader_switches(history)
    st.markdown("**历史龙头切换记录**")
    if switches.empty:
        st.caption("当前保存的历史扫描中尚未发现龙头切换；系统会随每日扫描持续积累。")
    else:
        switches["日期"] = switches["日期"].map(format_date)
        st.dataframe(switches.head(30), width="stretch", hide_index=True)

    if bundle.errors:
        with st.expander(f"数据源提示（{len(bundle.errors)}条）"):
            st.write("\n".join(bundle.errors))


def chart_scores(frame: pd.DataFrame) -> go.Figure:
    if frame.empty:
        return go.Figure()
    df = frame.copy()
    df["label"] = df["symbol"] + " " + df["name"] + "（" + df["horizon"].map(HORIZON_LABELS) + "）"
    df = df.sort_values("score", ascending=True).tail(24)
    chart_height = min(520, max(180, 88 + len(df) * 30))
    fig = go.Figure(
        go.Bar(
            x=df["score"],
            y=df["label"],
            orientation="h",
            marker_color=df["horizon"].map({"short": "#dc2626", "mid": "#2563eb", "long": "#16a34a"}),
            text=df["score"].map(lambda value: f"{float(value):.1f}"),
            textposition="outside" if len(df) <= 8 else "auto",
            cliponaxis=False,
            hovertemplate="%{y}<br>评分 %{x:.1f}<extra></extra>",
        )
    )
    fig.update_layout(
        height=chart_height,
        template="plotly_white",
        margin=dict(l=10, r=34, t=18, b=10),
        xaxis_title="综合评分",
        yaxis_title="",
        bargap=0.58 if len(df) <= 3 else 0.22,
    )
    fig.update_xaxes(range=[0, 105], fixedrange=True)
    fig.update_yaxes(automargin=True)
    return fig


def push_report(frame: pd.DataFrame) -> str:
    lines = ["## A股每日量化推荐", f"生成日期：{today_chinese()}", ""]
    for horizon in ("short", "mid", "long"):
        data = frame[frame["horizon"] == horizon].sort_values("score", ascending=False)
        if data.empty:
            continue
        lines.append(f"### {HORIZON_LABELS[horizon]}")
        for _, row in data.head(8).iterrows():
            topics = row.get("topic_text", "")
            theme = f"，题材：{topics}" if topics else ""
            lines.append(
                f"- {row['symbol']} {row['name']}：{row['priority']}，评分 {row['score']:.1f}，"
                f"买点 {row['buy_zone_low']:.2f}-{row['buy_zone_high']:.2f}，"
                f"止损 {row['stop_loss']:.2f}，目标 {row['take_profit_1']:.2f}/{row['take_profit_2']:.2f}{theme}"
            )
        lines.append("")
    lines.append("量化结果仅作研究和风控参考，不承诺收益。")
    return "\n".join(lines)


def render_market_map_page(
    recommendations: pd.DataFrame,
    capital_hotspots: pd.DataFrame,
    ladder: pd.DataFrame,
    history: pd.DataFrame | None = None,
) -> None:
    st.subheader("市场地图")
    st.caption("用于观察今日最强行业、题材、龙头股、跟风股、主线持续和是否进入高潮/退潮。")
    market_map = build_market_map(recommendations, capital_hotspots, ladder, history=history)
    if market_map.empty:
        st.info("暂无市场地图数据，刷新推荐或情绪周期后会自动生成。")
        return
    metric_row(
        [
            ("覆盖方向", f"{len(market_map)}个"),
            ("高潮方向", str(int((market_map["阶段"] == "高潮").sum()))),
            ("主升/启动", str(int(market_map["阶段"].isin(["主升", "启动"]).sum()))),
            ("退潮方向", str(int((market_map["阶段"] == "退潮").sum()))),
        ]
    )
    chart_data = market_map.head(18).copy()
    chart_data["热度"] = (
        pd.to_numeric(chart_data["板块资金净流入"], errors="coerce").fillna(0) * 4
        + pd.to_numeric(chart_data["平均评分"], errors="coerce").fillna(0)
        + pd.to_numeric(chart_data["涨停数量"], errors="coerce").fillna(0) * 8
        + pd.to_numeric(chart_data["推荐数量"], errors="coerce").fillna(0) * 3
    )
    fig = go.Figure(
        go.Bar(
            x=chart_data["热度"],
            y=chart_data["方向"],
            orientation="h",
            marker_color=["#dc2626" if stage in {"高潮", "主升"} else "#2563eb" for stage in chart_data["阶段"]],
            text=chart_data["阶段"],
            textposition="outside",
        )
    )
    fig.update_layout(height=520, yaxis={"autorange": "reversed"}, xaxis_title="综合热度", yaxis_title="")
    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)
    display = market_map.rename(
        columns={
            "板块资金净流入": "资金净流入(亿)",
            "平均评分": "推荐均分",
        }
    )
    st.dataframe(display, width="stretch", hide_index=True)
    st.caption("主线持续按最近资金记录中连续强势次数估算；数据不足时优先使用当日推荐池和资金热点。")


def build_position_plan_from_record(
    position: dict[str, Any],
    ignore_proxy: bool,
    recommendations: pd.DataFrame,
    breadth: dict[str, Any],
    global_summary: dict[str, Any],
) -> PositionPlan:
    data_warning = ""
    try:
        history = load_history(str(position["symbol"]), 2, ignore_proxy)
        try:
            spot = load_spot_for_lookup(ignore_proxy)
            spot_match = spot[spot["code"] == plain_code(str(position["symbol"]))]
            if not spot_match.empty:
                history = append_spot_bar(history, spot_match.iloc[0])
        except Exception as exc:
            data_warning = f"实时行情不可用，已使用最近K线收盘价生成方案：{exc}"
        report = analyze_stock(history, symbol=str(position["symbol"]), name=str(position.get("name") or ""))
    except Exception as exc:
        data_warning = f"行情/K线接口不可用，已使用推荐池或持仓成本生成备用方案：{exc}"
        history, report = fallback_position_history_and_report(position, recommendations, data_warning)
    stock_context = (
        recommendations[recommendations["symbol"].astype(str).str.upper() == str(position["symbol"]).upper()]
        if not recommendations.empty
        else pd.DataFrame()
    )
    industry = str(stock_context.iloc[0].get("industry") or "") if not stock_context.empty else ""
    topics = parse_list(stock_context.iloc[0].get("topics")) if not stock_context.empty else []
    capital_context = st.session_state.get("latest_capital_hotspots", pd.DataFrame())
    plan = analyze_position(
        history,
        report,
        cost_price=float(position["cost_price"]),
        shares=int(position["shares"]),
        available_cash=float(position["available_cash"]),
        t_ratio=float(position["t_ratio"]),
        account_assets=float(position["account_assets"]),
        sellable_shares=int(position["sellable_shares"]),
        t_preference=str(position["t_preference"]),
        breadth=breadth,
        global_summary=global_summary,
        capital_hotspots=capital_context,
        industry=industry,
        topics=topics,
        news=pd.DataFrame(),
    )
    if data_warning:
        plan.risks.insert(0, data_warning)
        plan.steps.insert(0, "当前方案使用备用数据生成，仅作为观察区间；实时行情恢复后请重新生成。")
        plan.action_summary = f"{plan.action_summary}（备用数据方案，需等实时行情恢复后复核。）"
    return plan


def save_position_plan(
    owner_key: str,
    position_id: int,
    plan: PositionPlan,
    symbol: str,
    name: str,
) -> None:
    save_position_plan_snapshot(
        {
            "owner_key": owner_key,
            "position_id": int(position_id),
            "trade_date": date.today().strftime("%Y-%m-%d"),
            "symbol": symbol,
            "name": name,
            "current_price": plan.current_price,
            "cost_price": plan.cost_price,
            "shares": plan.shares,
            "t_buy_low": plan.t_buy_low,
            "t_buy_high": plan.t_buy_high,
            "t_sell_low": plan.t_sell_low,
            "t_sell_high": plan.t_sell_high,
            "feasibility_score": plan.feasibility_score,
            "feasibility_label": plan.feasibility_label,
            "action_summary": plan.action_summary,
            "recovery_price_after_one": plan.cost_after_one_round,
            "recovery_price_after_three": plan.cost_after_three_rounds,
            "plan_json": plan.as_dict(),
        }
    )


def alert_rule_exists(rules: pd.DataFrame, symbol: str, alert_type: str) -> bool:
    if rules.empty:
        return False
    symbols = rules["symbol"].fillna("").astype(str).str.upper()
    types = rules["alert_type"].fillna("").astype(str)
    return bool(((symbols == symbol.upper()) & (types == alert_type)).any())


def create_recommendation_alert_rules(owner_key: str, recommendations: pd.DataFrame, existing_rules: pd.DataFrame, limit: int = 10) -> int:
    if recommendations.empty:
        return 0
    sort_cols = [col for col in ["credibility_score", "score"] if col in recommendations.columns]
    pool = recommendations.sort_values(sort_cols, ascending=False).head(limit) if sort_cols else recommendations.head(limit)
    created = 0
    for _, row in pool.iterrows():
        symbol = str(row.get("symbol") or "").upper()
        name = str(row.get("name") or "")
        if not symbol:
            continue
        for alert_type, comparator, threshold, note in [
            ("到达买点", "进入区间", None, "推荐池自动规则：价格进入买点区间"),
            ("跌破止损", "<=", row.get("stop_loss"), "推荐池自动规则：跌破止损位"),
            ("主力资金连续流入", ">=", row.get("net_inflow_10d"), "推荐池自动规则：3/5/10日资金持续观察"),
        ]:
            if alert_rule_exists(existing_rules, symbol, alert_type):
                continue
            save_alert_rule(
                {
                    "owner_key": owner_key,
                    "symbol": symbol,
                    "name": name,
                    "alert_type": alert_type,
                    "comparator": comparator,
                    "threshold_value": threshold,
                    "enabled": True,
                    "note": note,
                }
            )
            created += 1
    return created


def create_position_alert_rules(owner_key: str, positions: pd.DataFrame, existing_rules: pd.DataFrame) -> int:
    if positions.empty:
        return 0
    created = 0
    for _, row in positions.iterrows():
        symbol = str(row.get("symbol") or "").upper()
        if not symbol or alert_rule_exists(existing_rules, symbol, "跌破止损"):
            continue
        save_alert_rule(
            {
                "owner_key": owner_key,
                "symbol": symbol,
                "name": str(row.get("name") or ""),
                "alert_type": "跌破止损",
                "comparator": "<=",
                "threshold_value": None,
                "enabled": True,
                "note": "持仓中心自动规则：持仓风险优先",
            }
        )
        created += 1
    return created


def create_market_alert_rules(owner_key: str, existing_rules: pd.DataFrame) -> int:
    created = 0
    for alert_type, note in [
        ("板块主线升温", "市场类自动规则：主线板块资金升温提醒"),
        ("龙头切换", "市场类自动规则：推荐池龙头候选发生变化"),
        ("情绪退潮", "市场类自动规则：炸板率或情绪阶段转弱提醒"),
    ]:
        if alert_rule_exists(existing_rules, "", alert_type):
            continue
        save_alert_rule(
            {
                "owner_key": owner_key,
                "symbol": "",
                "name": "",
                "alert_type": alert_type,
                "comparator": ">=",
                "threshold_value": None,
                "enabled": True,
                "note": note,
            }
        )
        created += 1
    return created


def render_position_center_page(ignore_proxy: bool, recommendations: pd.DataFrame, breadth: dict[str, Any], global_summary: dict[str, Any]) -> None:
    st.subheader("自选股与持仓中心")
    st.caption("保存持仓后，系统每天可按成本、数量、资金、趋势和市场温度生成做T回本方案。")
    owner_key = current_owner_key()
    with st.form("save_position_form", border=True):
        q_col, cost_col, shares_col, cash_col = st.columns([0.32, 0.18, 0.18, 0.18])
        with q_col:
            query = st.text_input("股票名称或代码", placeholder="例如：中国神华、601088", key="watch_query")
        with cost_col:
            cost_price = st.number_input("持仓成本", min_value=0.01, max_value=10000.0, value=10.0, step=0.01, format="%.2f")
        with shares_col:
            shares = st.number_input("持股数量", min_value=100, max_value=100_000_000, value=1000, step=100)
        with cash_col:
            cash = st.number_input("可用资金", min_value=0.0, max_value=100_000_000.0, value=0.0, step=1000.0, format="%.2f")
        assets_col, sellable_col, ratio_col, mode_col = st.columns(4)
        with assets_col:
            account_assets = st.number_input("账户总资产", min_value=0.0, max_value=1_000_000_000.0, value=0.0, step=10000.0, format="%.2f")
        with sellable_col:
            sellable = st.number_input("今日可卖底仓", min_value=0, max_value=int(shares), value=int(shares), step=100)
        with ratio_col:
            ratio = st.select_slider("单次做T比例", options=[10, 15, 20, 25, 30, 40, 50], value=20)
        with mode_col:
            preference = st.selectbox("做T方式", ["自动判断", "先卖后买", "先买后卖"])
        note = st.text_input("备注", placeholder="例如：想三个月内降低成本")
        submitted = st.form_submit_button("保存到持仓中心", type="primary", width="stretch")
    if submitted:
        try:
            if not str(query).strip():
                raise ValueError("请先输入股票名称或代码。")
            try:
                spot = load_spot_for_lookup(ignore_proxy)
            except Exception:
                spot = pd.DataFrame()
            existing_positions = load_watchlist_positions(owner_key)
            symbol, name = resolve_stock_with_fallback(query, spot, recommendations, existing_positions)
            if not symbol:
                raise ValueError("没有识别到股票代码，请换成6位代码或完整股票名称。")
            if not name:
                name = str(query).strip() if not "".join(ch for ch in str(query) if ch.isdigit()) else symbol
            position_id = save_watchlist_position(
                {
                    "owner_key": owner_key,
                    "symbol": symbol,
                    "name": name,
                    "cost_price": cost_price,
                    "shares": shares,
                    "available_cash": cash,
                    "account_assets": account_assets,
                    "sellable_shares": sellable,
                    "t_ratio": float(ratio) / 100,
                    "t_preference": preference,
                    "target_break_even": cost_price,
                    "note": note,
                }
            )
            st.success(f"已保存 {symbol} {name} 到持仓中心。")
            record_activity("watchlist_position_save", stock_symbol=symbol, stock_name=name, request_params={"position_id": position_id})
        except Exception as exc:
            st.error(f"保存失败：{exc}")

    positions = load_watchlist_positions(owner_key)
    if positions.empty:
        st.info("还没有保存持仓。先录入一只股票，后续每天打开都会自动带出。")
        return
    st.markdown("**我的持仓**")
    st.dataframe(
        positions[
            ["id", "symbol", "name", "cost_price", "shares", "available_cash", "sellable_shares", "t_ratio", "t_preference", "updated_at"]
        ].rename(
            columns={
                "id": "编号",
                "symbol": "代码",
                "name": "名称",
                "cost_price": "成本",
                "shares": "数量",
                "available_cash": "可用资金",
                "sellable_shares": "可卖底仓",
                "t_ratio": "做T比例",
                "t_preference": "做T方式",
                "updated_at": "更新时间",
            }
        ),
        width="stretch",
        hide_index=True,
    )
    st.markdown("**今日持仓作战表**")
    batch_col, hint_col = st.columns([0.28, 0.72])
    with batch_col:
        run_batch = st.button("一键生成全部持仓今日方案", type="primary", width="stretch")
    with hint_col:
        st.caption("批量方案会保存为当天快照，用于观察每天回本路径、做T区间和风险变化。")
    if run_batch:
        rows = []
        progress = st.progress(0.0)
        for idx, (_, pos_row) in enumerate(positions.iterrows(), start=1):
            pos = pos_row.to_dict()
            try:
                plan_item = build_position_plan_from_record(pos, ignore_proxy, recommendations, breadth, global_summary)
                symbol = str(pos.get("symbol") or "").upper()
                name = str(pos.get("name") or "")
                save_position_plan(owner_key, int(pos["id"]), plan_item, symbol, name)
                rows.append(
                    {
                        "代码": symbol,
                        "名称": name,
                        "当前价": round(plan_item.current_price, 2),
                        "收益率": f"{plan_item.unrealized_return:+.2%}",
                        "低吸区": f"{plan_item.t_buy_low:.2f}-{plan_item.t_buy_high:.2f}",
                        "高抛区": f"{plan_item.t_sell_low:.2f}-{plan_item.t_sell_high:.2f}",
                        "今日动作": plan_item.feasibility_label,
                        "回本距离": f"{plan_item.break_even_gap:+.2%}",
                        "三轮后成本": round(plan_item.cost_after_three_rounds, 2),
                        "执行摘要": plan_item.action_summary,
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "代码": str(pos.get("symbol") or ""),
                        "名称": str(pos.get("name") or ""),
                        "当前价": "-",
                        "收益率": "-",
                        "低吸区": "-",
                        "高抛区": "-",
                        "今日动作": "生成失败",
                        "回本距离": "-",
                        "三轮后成本": "-",
                        "执行摘要": friendly_data_source_error(exc, "生成持仓方案"),
                    }
                )
            progress.progress(idx / max(len(positions), 1))
        st.session_state["position_center_batch_plans"] = pd.DataFrame(rows)
        st.success("全部持仓今日方案已生成。")
    batch_plans = st.session_state.get("position_center_batch_plans")
    if isinstance(batch_plans, pd.DataFrame) and not batch_plans.empty:
        metric_row(
            [
                ("覆盖持仓", f"{len(batch_plans)}只"),
                ("可执行/偏强", str(int(batch_plans["今日动作"].astype(str).str.contains("可|强|执行").sum()))),
                ("等待观察", str(int(batch_plans["今日动作"].astype(str).str.contains("等待|观察|弱").sum()))),
                ("生成失败", str(int((batch_plans["今日动作"] == "生成失败").sum()))),
            ]
        )
        st.dataframe(batch_plans, width="stretch", hide_index=True)

    p_col, action_col, delete_col = st.columns([0.45, 0.3, 0.25])
    with p_col:
        selected_id = st.selectbox("选择持仓", positions["id"].astype(int).tolist(), format_func=lambda pid: f"#{pid} {positions[positions['id'] == pid].iloc[0]['symbol']} {positions[positions['id'] == pid].iloc[0]['name']}")
    with action_col:
        generate_plan = st.button("生成今日做T方案", type="primary", width="stretch")
    with delete_col:
        remove_position = st.button("移出持仓中心", width="stretch")
    if remove_position:
        deactivate_watchlist_position(int(selected_id), owner_key)
        st.success("已移出持仓中心。")
        st.rerun()
    if generate_plan:
        pos = positions[positions["id"] == int(selected_id)].iloc[0].to_dict()
        try:
            plan = build_position_plan_from_record(pos, ignore_proxy, recommendations, breadth, global_summary)
            save_position_plan(
                owner_key,
                int(selected_id),
                plan,
                str(pos.get("symbol") or "").upper(),
                str(pos.get("name") or ""),
            )
            st.session_state["position_center_plan"] = plan
            st.success("今日做T方案已生成并保存。")
        except Exception as exc:
            LOGGER.exception("持仓中心生成方案失败")
            st.error(f"生成失败：{exc}")
    plan = st.session_state.get("position_center_plan")
    if plan:
        metric_row(
            [
                ("当前价", f"{plan.current_price:.2f}"),
                ("收益率", f"{plan.unrealized_return:+.2%}"),
                ("低吸区", f"{plan.t_buy_low:.2f}-{plan.t_buy_high:.2f}"),
                ("高抛区", f"{plan.t_sell_low:.2f}-{plan.t_sell_high:.2f}"),
                ("可行性", f"{plan.feasibility_score}分"),
                ("三轮后成本", f"{plan.cost_after_three_rounds:.2f}"),
            ]
        )
        st.info(plan.action_summary)
        st.write("\n".join(f"{idx}. {step}" for idx, step in enumerate(plan.steps, 1)))
    snapshots = load_position_plan_snapshots(owner_key, limit=30)
    if not snapshots.empty:
        with st.expander("查看历史方案变化"):
            st.dataframe(localize_dataframe(snapshots), width="stretch", hide_index=True)


def render_alert_center_page(
    ignore_proxy: bool,
    recommendations: pd.DataFrame,
    capital_hotspots: pd.DataFrame,
    sentiment_history: pd.DataFrame,
) -> None:
    st.subheader("预警中心")
    st.caption("先支持网页内提醒：买点、止损、资金流入、主线升温、龙头切换、情绪退潮。")
    owner_key = current_owner_key()
    with st.form("alert_rule_form", border=True):
        q_col, type_col, comp_col, value_col = st.columns([0.3, 0.28, 0.16, 0.18])
        with q_col:
            query = st.text_input("股票名称或代码（市场类预警可留空）", key="alert_query")
        with type_col:
            alert_type = st.selectbox("预警类型", ALERT_TYPES)
        with comp_col:
            comparator = st.selectbox("比较", [">=", "<=", "进入区间"], index=0)
        with value_col:
            threshold = st.number_input("阈值（可选）", value=0.0, step=0.01, format="%.2f")
        note = st.text_input("备注", placeholder="例如：跌破止损及时处理")
        add_rule = st.form_submit_button("新增预警规则", type="primary", width="stretch")
    if add_rule:
        symbol = ""
        name = ""
        if query.strip():
            try:
                spot = load_spot_for_lookup(ignore_proxy)
                symbol, name = resolve_stock(query, spot)
            except Exception as exc:
                st.warning(f"股票解析失败，将保存为市场类规则：{exc}")
        save_alert_rule(
            {
                "owner_key": owner_key,
                "symbol": symbol,
                "name": name,
                "alert_type": alert_type,
                "comparator": comparator,
                "threshold_value": threshold,
                "enabled": True,
                "note": note,
            }
        )
        st.success("预警规则已保存。")
    rules = load_alert_rules(owner_key)
    st.markdown("**快捷预警配置**")
    quick_col1, quick_col2, quick_col3 = st.columns(3)
    with quick_col1:
        if st.button("为推荐池生成预警", width="stretch"):
            created = create_recommendation_alert_rules(owner_key, recommendations, rules, limit=10)
            st.success(f"已新增 {created} 条推荐池预警规则。")
            rules = load_alert_rules(owner_key)
    with quick_col2:
        if st.button("为持仓生成止损预警", width="stretch"):
            positions = load_watchlist_positions(owner_key)
            created = create_position_alert_rules(owner_key, positions, rules)
            st.success(f"已新增 {created} 条持仓止损预警规则。")
            rules = load_alert_rules(owner_key)
    with quick_col3:
        if st.button("开启市场情绪预警", width="stretch"):
            created = create_market_alert_rules(owner_key, rules)
            st.success(f"已新增 {created} 条市场类预警规则。")
            rules = load_alert_rules(owner_key)

    if rules.empty:
        st.info("暂无预警规则。")
    else:
        enabled_count = int(pd.to_numeric(rules["enabled"], errors="coerce").fillna(0).sum()) if "enabled" in rules else len(rules)
        metric_row(
            [
                ("规则总数", str(len(rules))),
                ("启用中", str(enabled_count)),
                ("个股规则", str(int(rules["symbol"].fillna("").astype(str).str.len().gt(0).sum()))),
                ("市场规则", str(int(rules["symbol"].fillna("").astype(str).str.len().eq(0).sum()))),
            ]
        )
        st.dataframe(localize_dataframe(rules), width="stretch", hide_index=True)
        if st.button("立即检查预警", type="primary"):
            try:
                spot = load_spot_for_lookup(ignore_proxy)
                events = evaluate_alert_rules(
                    load_alert_rules(owner_key, enabled_only=True),
                    spot,
                    recommendations,
                    capital_hotspots,
                    sentiment_history,
                    owner_key=owner_key,
                )
                save_alert_events(events)
                if events:
                    st.success(f"触发 {len(events)} 条预警。")
                else:
                    st.info("当前没有触发预警。")
            except Exception as exc:
                st.error(f"预警检查失败：{exc}")
    events = load_alert_events(owner_key, limit=80)
    if not events.empty:
        st.markdown("**最近预警事件**")
        today_text = date.today().strftime("%Y-%m-%d")
        today_events = events[events["trade_date"].astype(str) == today_text] if "trade_date" in events else pd.DataFrame()
        metric_row(
            [
                ("今日触发", str(len(today_events))),
                ("机会提醒", str(int((today_events["severity"] == "机会").sum())) if not today_events.empty else "0"),
                ("风险提醒", str(int((today_events["severity"] == "风险").sum())) if not today_events.empty else "0"),
                ("最近类型", str(events.iloc[0].get("alert_type") or "-")),
            ]
        )
        st.dataframe(localize_dataframe(events), width="stretch", hide_index=True)


def render_commercial_audit_page(
    recommendations: pd.DataFrame,
    outcomes: pd.DataFrame,
    breadth: dict[str, Any],
) -> None:
    st.subheader("P0商业化闭环体检")
    st.caption("先把推荐可信度、数据源稳定、持仓回本、预警中心和风险合规五件事做扎实，再考虑大规模售卖。")
    owner_key = current_owner_key()
    positions = load_watchlist_positions(owner_key)
    plan_snapshots = load_position_plan_snapshots(owner_key, limit=200)
    alert_rules = load_alert_rules(owner_key)
    alert_events = load_alert_events(owner_key, limit=200)
    audit = commercial_audit(recommendations, outcomes, positions, alert_rules, alert_events, breadth)
    selected_run = current_run if isinstance(current_run, dict) else latest_run()
    selected_run_id = int(selected_run["id"]) if selected_run and selected_run.get("id") else None
    capital_hotspots = st.session_state.get("latest_capital_hotspots", pd.DataFrame())
    if capital_hotspots.empty:
        capital_hotspots = load_capital_hotspots(run_id=selected_run_id)
    data_errors = st.session_state.get("latest_errors", [])
    data_health = data_source_health_frame(
        selected_run,
        breadth,
        errors=data_errors if isinstance(data_errors, list) else [],
        capital_hotspots=capital_hotspots,
        recommendations=recommendations,
    )
    readiness = p0_readiness_frame(
        recommendations,
        outcomes,
        positions,
        plan_snapshots,
        alert_rules,
        alert_events,
        data_health,
    )
    credibility = credibility_summary(recommendations, outcomes)
    metric_row(
        [
            ("产品成熟度", f"{audit['score']}分"),
            ("当前阶段", audit["stage"]),
            ("高可信推荐", f"{audit['high_trust_count']}只"),
            ("复盘样本", f"{audit['outcome_count']}条"),
            ("持仓沉淀", f"{audit['position_count']}只"),
            ("启用预警", f"{audit['active_alert_count']}条"),
        ]
    )
    st.info("；".join(audit["gaps"]))

    st.markdown("**P0达标状态**")
    st.dataframe(readiness, width="stretch", hide_index=True)

    st.markdown("**推荐可信度评分体系**")
    metric_row(
        [
            ("样本覆盖率", pct(credibility["coverage_rate"])),
            ("高可信推荐", f"{credibility['high_trust_count']}只"),
            ("重复命中", f"{credibility['repeat_hit_count']}只"),
            ("T+1均值", pct(credibility["avg_t1"])),
            ("T+3均值", pct(credibility["avg_t3"])),
            ("T+5均值", pct(credibility["avg_t5"])),
            ("T+20均值", pct(credibility["avg_t20"])),
            ("最大回撤", pct(credibility["max_drawdown"])),
            ("止损触发率", pct(credibility["stop_rate"])),
            ("目标触达率", pct(credibility["target_rate"])),
        ]
    )
    st.caption("这些指标来自系统保存的历史推荐复盘。样本越多，可信度解释越有参考价值；样本不足时应降低仓位参考权重。")

    st.markdown("**数据源稳定性**")
    st.dataframe(data_health, width="stretch", hide_index=True)

    st.markdown("**持仓回本中心闭环**")
    today_text = date.today().strftime("%Y-%m-%d")
    today_plans = (
        plan_snapshots[plan_snapshots["trade_date"].astype(str) == today_text]
        if not plan_snapshots.empty and "trade_date" in plan_snapshots
        else pd.DataFrame()
    )
    metric_row(
        [
            ("保存持仓", f"{len(positions)}只"),
            ("今日方案", f"{len(today_plans)}条"),
            ("历史方案", f"{len(plan_snapshots)}条"),
            ("方案保存", "已开启" if len(plan_snapshots) else "待生成"),
        ]
    )
    if not plan_snapshots.empty:
        st.dataframe(
            localize_dataframe(
                plan_snapshots[
                    [
                        "trade_date",
                        "symbol",
                        "name",
                        "current_price",
                        "cost_price",
                        "shares",
                        "t_buy_low",
                        "t_buy_high",
                        "t_sell_low",
                        "t_sell_high",
                        "feasibility_score",
                        "feasibility_label",
                        "action_summary",
                        "created_at",
                    ]
                ].head(20)
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.warning("还没有生成持仓方案。用户保存持仓后，建议每天至少生成一次低吸区、高抛区、止损区和回本路径。")

    st.markdown("**预警中心闭环**")
    enabled_count = (
        int(pd.to_numeric(alert_rules.get("enabled"), errors="coerce").fillna(0).sum())
        if not alert_rules.empty and "enabled" in alert_rules
        else 0
    )
    metric_row(
        [
            ("预警规则", f"{len(alert_rules)}条"),
            ("启用规则", f"{enabled_count}条"),
            ("最近事件", f"{len(alert_events)}条"),
            ("闭环状态", "已形成" if enabled_count else "待启用"),
        ]
    )
    if not alert_rules.empty:
        st.dataframe(localize_dataframe(alert_rules.head(30)), width="stretch", hide_index=True)
    else:
        st.warning("暂无预警规则。建议一键生成推荐池预警、持仓止损预警和市场情绪预警。")

    st.markdown("**风险合规表达**")
    st.markdown("\n".join(f"- {item}" for item in compliance_text()))

    st.markdown("**P1付费价值模块**")
    hotspot_history = load_capital_hotspot_timeline()
    ladder = load_limit_up_ladder()
    market_map = build_market_map(recommendations, capital_hotspots, ladder, history=hotspot_history)
    p1_frame = p1_paid_value_frame(
        recommendations,
        capital_hotspots,
        market_map,
        outcomes,
        positions,
        alert_rules,
    )
    st.dataframe(p1_frame, width="stretch", hide_index=True)
    ai_table = build_ai_research_table(
        recommendations,
        global_summary=st.session_state.get("latest_global_summary", {}),
        limit=12,
    )
    if not ai_table.empty:
        with st.expander("查看AI投研解释样例"):
            st.dataframe(
                ai_table[
                    [
                        "代码",
                        "名称",
                        "行业",
                        "策略打法",
                        "为什么入选",
                        "最大风险",
                        "同板块替代股",
                        "失效点",
                    ]
                ],
                width="stretch",
                hide_index=True,
            )

    st.markdown("**优先优化顺序**")
    st.markdown(
        "\n".join(
            [
                "1. 先补可信度：分策略、分行业、分周期展示历史胜率和回撤。",
                "2. 再补持仓粘性：让用户保存持仓后每天自动得到做T、止损、回本路径。",
                "3. 再补预警闭环：网页内提醒先稳定，再接企业微信/短信/邮件。",
                "4. 再补数据可靠性：展示数据时间、失败原因、备用源和覆盖率。",
                "5. 最后补运营商业化：套餐、到期提醒、支付流水、用户行为分析。",
            ]
        )
    )
    plan = commercial_plan_frame()
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    status_order = {"待优化": 0, "部分上线": 1, "已上线": 2}
    plan["_priority_order"] = plan["优先级"].map(priority_order).fillna(9)
    plan["_status_order"] = plan["状态"].map(status_order).fillna(9)
    plan = plan.sort_values(["_priority_order", "_status_order", "模块"]).drop(columns=["_priority_order", "_status_order"])
    st.markdown("**商业化优化清单**")
    st.dataframe(plan, width="stretch", hide_index=True)
    with st.expander("销售与合规提示"):
        st.markdown(
            "\n".join(
                [
                    "- 对外避免确定性收益、保证回本、内部消息类承诺表达。",
                    "- 推荐话术使用“概率候选、买点区间、止损位、失效条件、历史复盘”。",
                    "- 付费页面要说明数据源延迟、策略样本数量和历史结果不代表未来收益。",
                    "- 用户持仓方案必须强调仓位控制，不能替用户承诺一定回本。",
                ]
            )
        )


def render_strategy_backtest_center(
    recommendations: pd.DataFrame,
    outcomes: pd.DataFrame,
    ignore_proxy: bool,
) -> None:
    st.subheader("策略回测中心")
    st.caption("用于建立付费可信度：推荐池复盘、单股买卖点、做T、龙头和情绪周期都要能解释。")
    owner_key = current_owner_key()
    positions = load_watchlist_positions(owner_key)
    sentiment = load_sentiment_history(limit=120)
    matrix = strategy_backtest_matrix(recommendations, outcomes, positions, sentiment)
    st.dataframe(matrix, width="stretch", hide_index=True)

    pool_tab, stock_tab, t_tab, leader_tab, sentiment_tab = st.tabs(
        ["推荐池回测", "单股买卖点", "做T模拟", "龙头策略", "情绪周期"]
    )

    with pool_tab:
        if outcomes.empty:
            st.info("暂无历史推荐复盘数据。可先点击下方按钮刷新一次复盘。")
        else:
            from stock_quant.review import outcome_summary

            summary = outcome_summary(outcomes)
            if not summary.empty:
                st.dataframe(summary, width="stretch", hide_index=True)
        if st.button("刷新推荐池历史复盘", type="primary"):
            from stock_quant.review import refresh_recommendation_outcomes

            progress = st.progress(0.0)

            def callback(done: int, total: int, symbol: str) -> None:
                progress.progress(done / max(total, 1), text=f"复盘 {done}/{total}：{symbol}")

            frame, errors = refresh_recommendation_outcomes(ignore_proxy=ignore_proxy, progress_callback=callback)
            if frame.empty:
                st.warning("没有生成新的复盘结果。")
            else:
                st.success(f"已刷新 {len(frame)} 条复盘结果。")
            if errors:
                st.caption("；".join(errors[:20]))

    with stock_tab:
        st.markdown("**单只股票买卖点回测与参数对比**")
        q_col, years_col, run_col = st.columns([0.45, 0.2, 0.35])
        with q_col:
            query = st.text_input("股票名称或代码", placeholder="例如：平安银行、000001", key="strategy_backtest_query")
        with years_col:
            years = st.selectbox("K线年限", [1, 2, 3, 5], index=1, key="strategy_backtest_years")
        with run_col:
            st.write("")
            st.write("")
            run = st.button("运行参数对比", width="stretch")
        if run:
            try:
                spot = load_spot_for_lookup(ignore_proxy)
                symbol, name = resolve_stock(query, spot)
                history = load_history(symbol, years, ignore_proxy)
                rows = []
                charts = None
                for pct_value in [0.15, 0.3, 0.5]:
                    curve, trades, metrics = run_backtest(history, initial_cash=100_000, max_position_pct=pct_value)
                    rows.append(
                        {
                            "单股最大仓位": f"{pct_value:.0%}",
                            "策略收益": metrics["total_return"],
                            "买入持有": metrics["benchmark_return"],
                            "最大回撤": metrics["max_drawdown"],
                            "胜率": metrics["win_rate"],
                            "交易次数": metrics["closed_trade_count"],
                        }
                    )
                    if charts is None:
                        charts = curve
                st.success(f"{symbol} {name} 参数对比完成。")
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
                if charts is not None:
                    st.plotly_chart(chart_backtest(charts), width="stretch", config=PLOTLY_CONFIG)
            except Exception as exc:
                LOGGER.exception("策略回测中心失败")
                st.error(f"回测失败：{exc}")

    with t_tab:
        if positions.empty:
            st.info("暂无持仓记录。先到“持仓中心”保存股票成本、数量和可用资金后，这里会形成做T模拟池。")
        else:
            display = positions.rename(
                columns={
                    "symbol": "代码",
                    "name": "名称",
                    "cost_price": "持仓成本",
                    "shares": "持股数量",
                    "available_cash": "可用资金",
                    "sellable_shares": "可卖底仓",
                    "t_ratio": "单次做T比例",
                    "updated_at": "更新时间",
                }
            )
            st.dataframe(
                display[[column for column in ["代码", "名称", "持仓成本", "持股数量", "可用资金", "可卖底仓", "单次做T比例", "更新时间"] if column in display]],
                width="stretch",
                hide_index=True,
            )
            st.caption("做T回测按持仓中心的区间方案执行，重点看能否降低理论成本、是否触发止损和滑点风险。")

    with leader_tab:
        leader_rows = (
            recommendations[recommendations.get("strategy_type", pd.Series(index=recommendations.index, dtype=str)).astype(str).str.contains("龙头")]
            if not recommendations.empty
            else pd.DataFrame()
        )
        if leader_rows.empty:
            leader_rows = recommendations.sort_values("score", ascending=False).head(10) if not recommendations.empty else pd.DataFrame()
        if leader_rows.empty:
            st.info("暂无龙头策略样本。生成推荐或进入“龙头追踪”后会自动补充。")
        else:
            st.dataframe(
                leader_rows[["symbol", "name", "industry", "score", "credibility_score", "buy_zone_low", "stop_loss"]]
                .rename(
                    columns={
                        "symbol": "代码",
                        "name": "名称",
                        "industry": "行业",
                        "score": "综合评分",
                        "credibility_score": "可信度评分",
                        "buy_zone_low": "买点下沿",
                        "stop_loss": "止损位",
                    }
                ),
                width="stretch",
                hide_index=True,
            )
            st.caption("龙头策略主要验证相对强弱和回撤，不代表未来一定延续。")

    with sentiment_tab:
        if sentiment.empty:
            st.info("暂无情绪周期历史。刷新情绪周期后，这里会展示阶段切换和风险区间。")
        else:
            display = sentiment.head(60).rename(
                columns={
                    "trade_date": "日期",
                    "emotion_stage": "情绪阶段",
                    "limit_up": "涨停估算",
                    "broken_count": "炸板数",
                    "broken_rate": "炸板率",
                    "temperature": "市场温度",
                    "created_at": "记录时间",
                }
            )
            visible = ["日期", "情绪阶段", "涨停估算", "炸板数", "炸板率", "市场温度", "记录时间"]
            st.dataframe(display[[column for column in visible if column in display]], width="stretch", hide_index=True)


def render_condition_selector_page(recommendations: pd.DataFrame) -> None:
    st.subheader("条件选股器")
    st.caption("把资金面、技术面、基本面和历史可信度拆成可组合条件，适合给不同风险偏好的用户生成专属候选池。")
    if recommendations.empty:
        st.info("暂无推荐池数据，请先刷新生成今日推荐。")
        return

    with st.form("condition_selector_form", border=True):
        selected = st.multiselect(
            "筛选条件",
            CONDITION_LABELS,
            default=[
                "主力资金连续流入",
                "技术评分较高",
                "处于买点附近",
                "市场主线方向",
            ],
        )
        col1, col2, col3 = st.columns([0.25, 0.25, 0.5])
        with col1:
            min_score = st.slider("最低综合评分", 50, 95, 72)
        with col2:
            max_position = st.slider("最高建议仓位", 5, 60, 35) / 100
        with col3:
            keyword = st.text_input("关键词", placeholder="行业、题材、股票名称、策略打法")
        submitted = st.form_submit_button("生成条件选股结果", type="primary", width="stretch")

    filtered = filter_by_conditions(
        recommendations,
        selected,
        min_score=float(min_score),
        max_risk_position=float(max_position),
        keyword=keyword,
    )
    if submitted:
        st.success(f"已筛出 {len(filtered)} 只候选股。")
    metric_row(
        [
            ("推荐池", f"{len(recommendations)}只"),
            ("筛选结果", f"{len(filtered)}只"),
            ("重点", str(int((filtered.get("priority", pd.Series(dtype=str)).astype(str) == "重点").sum()))),
            ("高胜率重复命中", str(int(pd.to_numeric(filtered.get("high_win_repeat"), errors="coerce").fillna(0).sum()))),
        ]
    )
    if filtered.empty:
        st.warning("当前条件过严，没有符合条件的股票。可以降低评分、仓位限制，或减少筛选条件。")
        return
    with st.expander("查看筛选结果评分图", expanded=len(filtered) <= 8):
        st.plotly_chart(chart_scores(filtered), width="stretch", config=PLOTLY_CONFIG)
    display_cols = [
        "symbol",
        "name",
        "horizon",
        "priority",
        "strategy_type",
        "industry",
        "score",
        "credibility_score",
        "buy_zone_low",
        "buy_zone_high",
        "stop_loss",
        "take_profit_1",
    ]
    display = filtered[[col for col in display_cols if col in filtered.columns]].copy()
    st.markdown("**筛选结果明细**")
    st.table(localize_dataframe(display).reset_index(drop=True))


def render_paper_trading_page(recommendations: pd.DataFrame, outcome_frame: pd.DataFrame) -> None:
    st.subheader("模拟交易账户")
    st.caption("用推荐池自动生成模拟买入记录，跟踪账户权益、胜率、回撤和单笔表现。它用于验证策略，不代表真实成交。")
    owner_key = current_owner_key()
    seed_col, cash_col, count_col = st.columns([0.34, 0.33, 0.33])
    with seed_col:
        initial_cash = st.number_input("初始模拟资金", min_value=10_000.0, max_value=10_000_000.0, value=100_000.0, step=10_000.0)
    account = get_or_create_paper_account(owner_key=owner_key, initial_cash=float(initial_cash))
    with cash_col:
        cash_per_trade = st.number_input("单只模拟投入", min_value=1_000.0, max_value=1_000_000.0, value=10_000.0, step=1_000.0)
    with count_col:
        max_count = st.slider("本次最多买入", 1, 30, 10)

    accounts = load_paper_accounts(owner_key)
    if not accounts.empty:
        account_id = int(accounts.iloc[0]["id"])
        initial_cash = float(accounts.iloc[0]["initial_cash"])
    else:
        account_id = int(account["id"])

    trades = load_paper_trades(owner_key, account_id=account_id)
    if st.button("按当前推荐池生成模拟交易", type="primary", width="stretch"):
        pool = recommendations.copy()
        if not trades.empty and "recommendation_id" in trades.columns and "id" in pool.columns:
            used_ids = set(pd.to_numeric(trades["recommendation_id"], errors="coerce").dropna().astype(int).tolist())
            pool = pool[~pd.to_numeric(pool["id"], errors="coerce").fillna(0).astype(int).isin(used_ids)]
        records = build_paper_trade_candidates(
            pool,
            outcome_frame,
            account_id=account_id,
            owner_key=owner_key,
            cash_per_trade=float(cash_per_trade),
            max_count=int(max_count),
        )
        save_paper_trades(records)
        if records:
            st.success(f"已生成 {len(records)} 笔模拟交易。")
        else:
            st.warning("没有新的可模拟交易记录，可能今日推荐已生成过，或单只投入不足以买入100股。")
        trades = load_paper_trades(owner_key, account_id=account_id)

    curve, summary = paper_trade_summary(trades, initial_cash=float(initial_cash))
    metric_row(
        [
            ("模拟交易数", str(summary["trade_count"])),
            ("累计收益", f"{summary['total_return']:.2%}"),
            ("胜率", "-" if summary["win_rate"] is None else f"{summary['win_rate']:.2%}"),
            ("最大回撤", f"{summary['max_drawdown']:.2%}"),
        ]
    )
    if curve.empty:
        st.info("暂无模拟交易。点击上方按钮后，会根据当前推荐池生成模拟账户曲线。")
        return
    st.plotly_chart(chart_paper_equity(curve), width="stretch", config=PLOTLY_CONFIG)
    display = trades.copy()
    rename_map = {
        "trade_date": "交易日期",
        "symbol": "代码",
        "name": "名称",
        "horizon": "周期",
        "strategy_type": "策略分层",
        "price": "买入价",
        "shares": "股数",
        "amount": "金额",
        "fee": "费用",
        "return_pct": "复盘收益率",
        "status": "状态",
        "reason": "说明",
    }
    st.dataframe(display.rename(columns=rename_map), width="stretch", hide_index=True)


def render_daily_review_page(
    recommendations: pd.DataFrame,
    outcome_frame: pd.DataFrame,
    capital_hotspots: pd.DataFrame,
    breadth: dict[str, Any],
    global_summary: dict[str, Any],
    sentiment_history: pd.DataFrame,
) -> None:
    st.subheader("每日收盘复盘报告")
    st.caption("沉淀每天的市场温度、资金热点、推荐数量和历史验证结果，方便后续给付费用户展示系统可信度。")
    owner_key = current_owner_key()
    report_text, metrics = build_daily_review_report(
        recommendations,
        outcome_frame,
        capital_hotspots,
        breadth,
        global_summary,
        sentiment_history,
    )
    summary = (
        f"推荐{metrics.get('recommendation_count', 0)}只，"
        f"重点{metrics.get('focus_count', 0)}只，"
        f"市场温度{metrics.get('temperature', '-')}"
    )
    col1, col2 = st.columns([0.28, 0.72])
    with col1:
        if st.button("生成并保存今日复盘", type="primary", width="stretch"):
            save_daily_review_report(
                owner_key=owner_key,
                trade_date=date.today().strftime("%Y-%m-%d"),
                title=f"A股每日收盘复盘 {date.today().strftime('%Y-%m-%d')}",
                summary=summary,
                report_text=report_text,
                metrics=metrics,
            )
            st.success("今日复盘报告已保存。")
    with col2:
        st.download_button(
            "下载复盘文本",
            data=report_text.encode("utf-8"),
            file_name=f"daily-review-{date.today().strftime('%Y%m%d')}.md",
            mime="text/markdown",
            width="stretch",
        )
    st.text_area("复盘报告内容", value=report_text, height=420)
    reports = load_daily_review_reports(owner_key, limit=20)
    if reports.empty:
        st.info("暂无历史复盘报告。")
        return
    with st.expander("历史复盘报告"):
        st.dataframe(
            reports[["trade_date", "title", "summary", "created_at"]].rename(
                columns={"trade_date": "日期", "title": "标题", "summary": "摘要", "created_at": "保存时间"}
            ),
            width="stretch",
            hide_index=True,
        )
        selected = st.selectbox("查看历史报告", reports["trade_date"].astype(str).tolist())
        row = reports[reports["trade_date"].astype(str) == selected].iloc[0]
        st.markdown(row["report_text"])


def resolve_stock(query: str, spot: pd.DataFrame) -> tuple[str, str]:
    raw = query.strip()
    if not raw:
        raise ValueError("请输入股票代码或名称")
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 6:
        symbol = prefixed_symbol(digits[-6:])
        match = spot[spot["code"] == plain_code(symbol)]
        name = str(match["name"].iloc[0]) if not match.empty else ""
        return symbol, name

    exact = spot[spot["name"].astype(str) == raw]
    if exact.empty:
        exact = spot[spot["name"].astype(str).str.contains(raw, case=False, regex=False)]
    if exact.empty:
        raise ValueError(f"没有找到股票：{query}")
    row = exact.iloc[0]
    return str(row["symbol"]), str(row["name"])


def resolve_stock_with_fallback(
    query: str,
    spot: pd.DataFrame,
    recommendations: pd.DataFrame,
    positions: pd.DataFrame | None = None,
) -> tuple[str, str]:
    """Resolve a stock without making the save flow depend on live spot quotes."""
    raw = str(query or "").strip()
    if not raw:
        raise ValueError("请输入股票代码或名称")

    try:
        if not spot.empty:
            return resolve_stock(raw, spot)
    except Exception:
        pass

    lookup_frames = [recommendations]
    if positions is not None:
        lookup_frames.append(positions)
    for frame in lookup_frames:
        if frame is None or frame.empty:
            continue
        work = frame.copy()
        if "symbol" not in work.columns:
            continue
        work["symbol_text"] = work["symbol"].astype(str).str.upper()
        work["code_text"] = work["symbol_text"].map(plain_code)
        if "name" not in work.columns:
            work["name"] = ""
        work["name_text"] = work["name"].astype(str)
        digits = "".join(ch for ch in raw if ch.isdigit())
        if len(digits) >= 6:
            matched = work[work["code_text"] == plain_code(digits[-6:])]
        else:
            matched = work[
                (work["name_text"] == raw)
                | work["name_text"].str.contains(raw, case=False, regex=False, na=False)
                | work["symbol_text"].str.contains(raw.upper(), case=False, regex=False, na=False)
            ]
        if not matched.empty:
            row = matched.iloc[0]
            return str(row["symbol"]).upper(), str(row.get("name") or "")

    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 6:
        return prefixed_symbol(digits[-6:]).upper(), ""
    raise ValueError(f"没有找到股票：{query}")


def _first_number(row: pd.Series, keys: list[str], default: float | None = None) -> float | None:
    for key in keys:
        if key in row.index:
            value = pd.to_numeric(pd.Series([row.get(key)]), errors="coerce").iloc[0]
            if pd.notna(value):
                return float(value)
    return default


def _recommendation_row(recommendations: pd.DataFrame, symbol: str) -> pd.Series | None:
    if recommendations.empty or "symbol" not in recommendations.columns:
        return None
    matched = recommendations[
        recommendations["symbol"].astype(str).str.upper() == str(symbol).upper()
    ]
    if matched.empty:
        matched = recommendations[
            recommendations["symbol"].astype(str).map(plain_code) == plain_code(symbol)
        ]
    return None if matched.empty else matched.iloc[0]


def fallback_position_history_and_report(
    position: dict[str, Any],
    recommendations: pd.DataFrame,
    reason: str,
) -> tuple[pd.DataFrame, SignalReport]:
    """Build a conservative local plan when external quote/history APIs are unavailable."""
    symbol = str(position.get("symbol") or "").upper()
    name = str(position.get("name") or "")
    rec = _recommendation_row(recommendations, symbol)
    cost_price = max(0.01, float(position.get("cost_price") or 0.01))
    if rec is not None:
        name = name or str(rec.get("name") or "")
        close = _first_number(rec, ["close", "current_price", "收盘价"], cost_price) or cost_price
        score = int(_first_number(rec, ["score", "综合评分", "base_score"], 50) or 50)
        stop_loss = _first_number(rec, ["stop_loss", "止损"], close * 0.94) or close * 0.94
        take_profit_1 = _first_number(rec, ["take_profit_1", "目标一"], close * 1.06) or close * 1.06
        take_profit_2 = _first_number(rec, ["take_profit_2", "目标二"], close * 1.10) or close * 1.10
        buy_low = _first_number(rec, ["buy_zone_low", "买点下沿"], close * 0.98) or close * 0.98
        buy_high = _first_number(rec, ["buy_zone_high", "买点上沿"], close * 1.01) or close * 1.01
        trailing_stop = _first_number(rec, ["trailing_stop", "移动止盈"], stop_loss) or stop_loss
    else:
        close = cost_price
        score = 45
        stop_loss = close * 0.94
        take_profit_1 = close * 1.06
        take_profit_2 = close * 1.10
        buy_low = close * 0.98
        buy_high = close * 1.01
        trailing_stop = stop_loss

    periods = 120
    end = pd.Timestamp(date.today())
    dates = pd.bdate_range(end=end, periods=periods)
    close_values = []
    start_price = max(0.01, close * 0.94)
    for idx in range(periods):
        progress = idx / max(periods - 1, 1)
        wave = 0.018 * math.sin(idx / 5.0) + 0.01 * math.sin(idx / 13.0)
        close_values.append(max(0.01, start_price + (close - start_price) * progress + close * wave))
    close_values[-1] = close
    history = pd.DataFrame(
        {
            "date": dates,
            "open": [v * 0.995 for v in close_values],
            "close": close_values,
            "high": [v * 1.025 for v in close_values],
            "low": [v * 0.975 for v in close_values],
            "volume": [1_000_000 + idx * 3000 for idx in range(periods)],
            "turnover": [max(0.01, v) * 100_000_000 for v in close_values],
        }
    )
    report = SignalReport(
        symbol=symbol,
        name=name,
        date=date.today().strftime("%Y-%m-%d"),
        close=float(close),
        score=max(0, min(100, score)),
        rating="备用数据评估",
        action="外部行情暂不可用，先按备用数据生成观察方案",
        buy_zone_low=float(buy_low),
        buy_zone_high=float(max(buy_low, buy_high)),
        stop_loss=float(min(stop_loss, close * 0.99)),
        take_profit_1=float(max(take_profit_1, close * 1.03)),
        take_profit_2=float(max(take_profit_2, close * 1.06)),
        trailing_stop=float(trailing_stop),
        position_pct=0.0 if score < 52 else 0.15,
        reasons=["使用本地推荐池或持仓成本生成备用方案"],
        sell_triggers=["实时行情恢复后重新生成；跌破备用止损位停止机械做T"],
        warnings=[f"备用数据源：{reason}"],
    )
    return history, report


def render_recommendation_table(frame: pd.DataFrame, horizon: str | None = None) -> None:
    data = frame if horizon is None else frame[frame["horizon"] == horizon]
    if data.empty:
        st.info("当前没有满足条件的推荐。")
        return
    display = display_recommendations(data)
    st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        column_config={
            "评分": st.column_config.ProgressColumn("评分", min_value=0, max_value=100),
            "收盘价": st.column_config.NumberColumn("收盘价", format="%.2f"),
            "止损": st.column_config.NumberColumn("止损", format="%.2f"),
        },
    )


def recommendation_search(frame: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    with st.form("recommendation_search_form", border=False):
        search_col, horizon_col, priority_col, strategy_col, submit_col = st.columns([0.34, 0.16, 0.16, 0.2, 0.14])
        with search_col:
            keyword = st.text_input(
                "搜索推荐股票",
                placeholder="代码、名称、行业、题材、推荐原因",
                key="recommendation_keyword",
            ).strip()
        with horizon_col:
            horizon_label = st.selectbox("周期", ["全部", "短期", "中期", "长期"], key="recommendation_horizon")
        with priority_col:
            priority = st.selectbox("优先级", ["全部", "重点", "关注", "观察"], key="recommendation_priority")
        with strategy_col:
            strategy_type = st.selectbox("策略打法", ["全部", *STRATEGY_TYPES], key="recommendation_strategy_type")
        with submit_col:
            st.write("")
            st.write("")
            st.form_submit_button("搜索", width="stretch")

    filtered = frame.head(30).copy()
    if horizon_label != "全部":
        reverse_horizon = {value: key for key, value in HORIZON_LABELS.items()}
        filtered = filtered[filtered["horizon"] == reverse_horizon[horizon_label]]
    if priority != "全部":
        filtered = filtered[filtered["priority"] == priority]
    if strategy_type != "全部" and "strategy_type" in filtered.columns:
        filtered = filtered[filtered["strategy_type"] == strategy_type]
    if keyword:
        search_columns = ["symbol", "name", "industry", "topic_text", "strategy_type"]
        text = pd.Series("", index=filtered.index, dtype="object")
        for column in search_columns:
            if column in filtered.columns:
                text = text + " " + filtered[column].fillna("").astype(str)
        for column in ("reasons", "sell_triggers"):
            if column in filtered.columns:
                text = text + " " + filtered[column].map(
                    lambda values: " ".join(values) if isinstance(values, list) else str(values)
                )
        filtered = filtered[text.str.contains(keyword, case=False, regex=False)]
    signature = f"{keyword}|{horizon_label}|{priority}|{strategy_type}"
    return filtered.reset_index(drop=True), signature


def render_recommendation_cards(frame: pd.DataFrame) -> None:
    filtered, filter_signature = recommendation_search(frame)
    page_size = 5
    total = len(filtered)
    total_pages = max(1, (total + page_size - 1) // page_size)
    if st.session_state.get("recommendation_filter_signature") != filter_signature:
        st.session_state["recommendation_page"] = 1
        st.session_state["recommendation_filter_signature"] = filter_signature
    st.session_state["recommendation_page"] = min(
        max(int(st.session_state.get("recommendation_page", 1)), 1),
        total_pages,
    )
    info_col, prev_col, page_col, next_col = st.columns([0.58, 0.13, 0.16, 0.13])
    with info_col:
        st.caption(f"共 {total} 只，推荐池最多30只；每页显示5只。")
    page = int(st.session_state["recommendation_page"])
    with prev_col:
        if st.button("上一页", disabled=page <= 1, width="stretch"):
            st.session_state["recommendation_page"] = page - 1
            st.rerun()
    with page_col:
        st.markdown(f"<div style='text-align:center;padding-top:9px'>第 {page}/{total_pages} 页</div>", unsafe_allow_html=True)
    with next_col:
        if st.button("下一页", disabled=page >= total_pages, width="stretch"):
            st.session_state["recommendation_page"] = page + 1
            st.rerun()

    if filtered.empty:
        st.info("没有找到匹配的推荐股票。")
        return

    start = (page - 1) * page_size
    page_data = filtered.iloc[start : start + page_size]
    for offset, (_, row) in enumerate(page_data.iterrows(), start=start + 1):
        with st.container(border=True):
            title_col, score_col, trade_col = st.columns([0.5, 0.16, 0.34])
            with title_col:
                st.markdown(
                    f"### {offset}. {row['symbol']} {row['name']}\n"
                    f"`{HORIZON_LABELS.get(row['horizon'], row['horizon'])}` "
                    f"`{row['priority']}` "
                    f"`{row.get('strategy_type', '主线趋势股')}` "
                    f"`{row.get('industry') or '行业待补充'}`"
                )
                if bool(row.get("high_win_repeat")):
                    st.success("高胜率模型重复命中")
                if row.get("topic_text"):
                    st.caption(f"题材：{row['topic_text']}")
            with score_col:
                st.metric("综合评分", f"{float(row['score']):.1f}")
                st.caption(f"基础技术分 {float(row['base_score']):.0f}")
            with trade_col:
                risk_valid = (
                    float(row.get("position_pct", 0) or 0) > 0
                    and float(row["buy_zone_low"]) > float(row["stop_loss"])
                )
                if risk_valid:
                    st.markdown(
                        f"**买点** `{row['buy_zone_low']:.2f}-{row['buy_zone_high']:.2f}`  "
                        f"**止损** `{row['stop_loss']:.2f}`\n\n"
                        f"**目标** `{row['take_profit_1']:.2f} / {row['take_profit_2']:.2f}`  "
                        f"**仓位** `{float(row['position_pct']):.0%}`"
                    )
                else:
                    st.warning("旧版记录的风控区间无效，请刷新后再考虑开仓。")

            credibility = credibility_display(row)
            metric_row(
                [
                    ("历史胜率", credibility["历史胜率"]),
                    ("T+1/3/5/20均值", credibility["T+1/T+3/T+5/T+20"]),
                    ("最大回撤", credibility["最大回撤"]),
                    ("止损触发率", credibility["止损率"]),
                    ("目标触达率", credibility["目标率"]),
                    ("推荐可信度", credibility["可信度"]),
                ]
            )
            credibility_detail = credibility_explanation(row)
            st.caption(f"可信度解释：{credibility_detail['summary']}")
            with st.expander("查看可信度评分拆解"):
                c1, c2, c3 = st.columns(3)
                c1.metric("参考动作", credibility_detail["action"])
                c2.metric("风险标签", credibility_detail["risk_label"])
                c3.metric("模型复用", "是" if bool(row.get("high_win_repeat")) else "否")
                st.markdown("\n".join(f"- {reason}" for reason in credibility_detail["reasons"]))
            pe_value = row.get("pe_est")
            pb_value = row.get("pb_est")
            cash_value = row.get("operating_cash_flow")
            dividend_ratio = row.get("dividend_year_ratio")
            factor_score = row.get("fundamental_score")
            factor_line = (
                "资金净流入："
                f"3日 {yi(row.get('net_inflow_3d'))} ｜ "
                f"5日 {yi(row.get('net_inflow_5d'))} ｜ "
                f"10日 {yi(row.get('net_inflow_10d'))}"
            )
            if pd.notna(pe_value):
                factor_line += f" ｜ 估算市盈率（PE）{float(pe_value):.1f}"
            st.caption(factor_line)
            if pd.notna(pb_value) or pd.notna(cash_value) or pd.notna(dividend_ratio):
                factor_parts = []
                if pd.notna(pb_value):
                    factor_parts.append(f"估算市净率（PB）{float(pb_value):.2f}")
                if pd.notna(cash_value):
                    factor_parts.append(f"经营现金流 {yi(cash_value)}")
                if pd.notna(dividend_ratio):
                    factor_parts.append(f"历史分红稳定度 {float(dividend_ratio):.0%}")
                if pd.notna(factor_score):
                    factor_parts.append(f"基本面资金分 {float(factor_score):.1f}")
                st.caption(" ｜ ".join(factor_parts))

            reason_col, sell_col = st.columns(2)
            with reason_col:
                st.markdown("**推荐原因**")
                reasons = row["reasons"] if isinstance(row["reasons"], list) else parse_list(row["reasons"])
                factor_keywords = ("主力资金", "估值处于", "经营活动现金流", "历史分红")
                factor_reasons = [reason for reason in reasons if reason.startswith(factor_keywords)]
                visible_reasons = factor_reasons + [reason for reason in reasons if reason not in factor_reasons]
                st.markdown("\n".join(f"- {reason}" for reason in visible_reasons[:10]))
            with sell_col:
                st.markdown("**卖出条件**")
                triggers = (
                    row["sell_triggers"]
                    if isinstance(row["sell_triggers"], list)
                    else parse_list(row["sell_triggers"])
                )
                st.markdown("\n".join(f"- {trigger}" for trigger in triggers[:6]))
            with st.expander("AI投研解释与风险失效点"):
                brief = research_brief(row, global_summary=st.session_state.get("latest_global_summary", {}))
                for label, text in brief.items():
                    st.markdown(f"**{label}**：{text}")


def run_scan(
    scan_size: int,
    top_n: int,
    min_turnover: float,
    ignore_proxy: bool,
    min_net_inflow: float,
    industry_filter: str,
    min_price: float,
    max_price: float,
    random_price_pick: bool,
    require_fund_flow: bool,
    require_valuation: bool,
    require_cashflow: bool,
    require_dividend: bool,
    allow_near_match: bool,
) -> tuple[pd.DataFrame, Any, list[str]]:
    parameters = {
        "K线复核候选数": scan_size,
        "每周期推荐上限": top_n,
        "最低成交额": min_turnover,
        "最低主力净流入": min_net_inflow,
        "行业筛选": industry_filter,
        "最低股价": min_price,
        "最高股价": max_price,
        "股价范围内随机抽样": random_price_pick,
        "要求资金流达标": require_fund_flow,
        "要求同行业估值正常": require_valuation,
        "要求经营现金流良好": require_cashflow,
        "要求历史分红稳定": require_dividend,
        "严格无结果时显示近似匹配": allow_near_match,
        "忽略系统代理": ignore_proxy,
    }
    run_id = create_run(
        trade_date=date.today().strftime("%Y-%m-%d"),
        scan_size=scan_size,
        top_n=top_n,
        min_turnover=min_turnover,
        strategy_version=STRATEGY_VERSION,
        parameters=parameters,
    )
    progress = st.progress(0)
    status = st.empty()

    def on_progress(done: int, total: int, symbol: str, name: str) -> None:
        progress.progress(done / max(total, 1))
        status.write(f"正在分析 {done}/{total}：{symbol} {name}")

    try:
        frame, context, errors = scan_recommendations(
            run_id=run_id,
            scan_size=scan_size,
            top_n=top_n,
            min_turnover=min_turnover,
            ignore_proxy=ignore_proxy,
            progress_callback=on_progress,
            min_net_inflow=min_net_inflow,
            industry_filter=industry_filter,
            min_price=min_price,
            max_price=max_price,
            random_price_pick=random_price_pick,
            require_fund_flow=require_fund_flow,
            require_valuation=require_valuation,
            require_cashflow=require_cashflow,
            require_dividend=require_dividend,
            allow_near_match=allow_near_match,
        )
        if frame.empty:
            diagnostic = next(
                (item for item in reversed(errors) if str(item).startswith("扫描诊断：")),
                "",
            )
            message = "本轮扫描完成，但没有股票同时满足技术趋势、风险仓位和当前筛选条件。"
            if diagnostic:
                message += diagnostic
            raise DataSourceError(message)
        result_dates = pd.to_datetime(frame.get("trade_date"), errors="coerce").dropna()
        newest_result_date = result_dates.max().date() if not result_dates.empty else None
        result_freshness = assess_recommendation_freshness(newest_result_date)
        if result_freshness.level in {"missing", "invalid", "stale"}:
            raise DataSourceError(
                "本轮扫描取得的行情日期未达到可用新鲜度，结果没有保存。"
                + result_freshness.message
            )
        save_recommendations(frame.to_dict("records"))
        save_capital_hotspots(run_id, context.capital_hotspots)
        save_market_snapshot(
            run_id=run_id,
            market=context.breadth,
            global_summary=context.global_summary,
            global_indices=context.global_indices,
        )
        update_run(run_id, "done", f"生成 {len(frame)} 条推荐")
    except Exception as exc:
        update_run(run_id, "failed", str(exc))
        raise
    finally:
        progress.empty()
        status.empty()

    st.session_state["latest_run_id"] = run_id
    st.session_state["latest_context_boards"] = context.boards
    st.session_state["latest_global_indices"] = context.global_indices
    st.session_state["latest_global_summary"] = context.global_summary
    st.session_state["latest_breadth"] = context.breadth
    st.session_state["latest_capital_hotspots"] = context.capital_hotspots
    st.session_state["latest_financial_report_date"] = context.financial_report_date
    st.session_state["latest_fundamental_coverage"] = len(context.fundamental_universe)
    st.session_state["latest_errors"] = errors
    return frame, context, errors


with st.sidebar:
    auth_mode = os.getenv("AUTH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    st.caption("运行模式：" + ("正式会员登录" if auth_mode else "本地免登录调试"))
    if not auth_mode:
        st.info("当前为本地调试模式，不校验会员账号；服务器售卖版本请开启会员登录。")
    st.subheader("刷新参数")
    scan_size = st.slider(
        "K线复核候选数",
        20,
        160,
        80,
        step=10,
        help="全市场基本面与资金面初筛后，再下载日K做技术复核的候选数量。不是最终推荐数量。",
    )
    top_n = 10
    st.text_input("推荐池上限", value="30只（每个周期最多10只）", disabled=True)
    min_turnover_yi = st.slider(
        "最低成交额（亿元）",
        0.1,
        10.0,
        0.5,
        step=0.1,
        help="用于过滤流动性不足的股票。盘中成交额尚未走完全天，早盘可适当降低。",
    )
    min_net_inflow_yi = st.number_input(
        "3/5/10日最低净流入（亿元）",
        min_value=0.0,
        max_value=100.0,
        value=1.0,
        step=0.5,
        help="勾选资金流条件后，3日、5日、10日三个窗口需要分别达到该阈值。",
    )
    st.markdown("**股价范围**")
    price_min_col, price_max_col = st.columns(2)
    with price_min_col:
        min_price = st.number_input(
            "最低股价（元）",
            min_value=0.01,
            max_value=1000.0,
            value=2.0,
            step=0.5,
            format="%.2f",
            help="只让当前价不低于该数值的股票进入候选池。",
        )
    with price_max_col:
        max_price = st.number_input(
            "最高股价（元）",
            min_value=0.01,
            max_value=1000.0,
            value=300.0,
            step=1.0,
            format="%.2f",
            help="只让当前价不高于该数值的股票进入候选池。",
        )
    if max_price < min_price:
        st.warning("最高股价不能低于最低股价，刷新时会自动按最低股价处理。")
    industry_filter = st.selectbox(
        "行业大类",
        ["全部", "电力", "机器人", "半导体", "人工智能", "新能源", "医药", "消费", "金融", "军工", "有色资源"],
    )
    with st.expander("高级筛选与网络设置"):
        random_price_pick = st.checkbox(
            "股价范围内随机抽样",
            value=False,
            help="开启后，会先按股价、成交额、资金面和基本面筛选，再从高分候选中随机抽取一部分进入K线复核，适合扩大发现面。",
        )
        require_fund_flow = st.checkbox(
            "3日、5日、10日净流入均达标",
            value=True,
            help="三个周期分别达到阈值，不是三项相加。",
        )
        require_valuation = st.checkbox(
            "市盈率（PE）、市净率（PB）处于同行业正常区间",
            value=True,
            help="估算市盈率和市净率均需处于同行业中位数的0.45至1.8倍。",
        )
        require_cashflow = st.checkbox(
            "经营活动现金流良好",
            value=True,
            help="最近可用报告期的经营现金流净额和每股经营现金流均为正。",
        )
        require_dividend = st.checkbox(
            "历史分红稳定",
            value=True,
            help="有效统计年份中至少80%有现金分红记录，且通常不少于4次。",
        )
        allow_near_match = st.checkbox(
            "严格条件无结果时显示近似匹配",
            value=True,
            help="严格组合始终优先；仅在零结果时显示满足大部分条件的观察级候选，并逐只标明未满足项。",
        )
        ignore_proxy = st.toggle(
            "忽略系统代理",
            value=False,
            help="开启后，行情接口请求会绕过电脑/服务器系统代理。若出现503、连接被重置、代理拦截等问题，可打开后重试。",
        )
    st.caption("先扫描全部A股的资金面与基本面，再对高分候选进行K线、成交量和买卖点复核。")
    st.page_link("pages/methodology.py", label="查看名词与筛选方法")
    st.page_link("pages/risk_disclosure.py", label="查看风险与合规说明")

st.title("A股每日量化推荐")
st.markdown(
    '<div class="risk-note">系统按热点强度、K线走势、成交量和风险位筛选高概率候选；市场没有“必涨”信号，买卖点和止损必须一起看。</div>',
    unsafe_allow_html=True,
)

current_run, recommendations = latest_recommendation_set()
if "latest_run_id" in st.session_state:
    session_run_id = int(st.session_state["latest_run_id"])
    session_recommendations = hydrate(load_recommendations(run_id=session_run_id))
    if session_recommendations.empty:
        st.session_state.pop("latest_run_id", None)
    else:
        current_run = {**(current_run or {}), "id": session_run_id}
        recommendations = session_recommendations

recommendation_summary(recommendations, current_run)
freshness = assess_recommendation_freshness(
    recommendations["trade_date"].iloc[0]
    if not recommendations.empty and "trade_date" in recommendations.columns
    else None
)
if freshness.level == "stale":
    st.warning(freshness.message)
elif freshness.level == "invalid":
    st.error(freshness.message)
elif freshness.level == "missing":
    st.info(freshness.message)
elif freshness.level == "aging":
    st.info(freshness.message)
if not recommendations.empty:
    invalid_risk = (
        (pd.to_numeric(recommendations.get("position_pct"), errors="coerce").fillna(0) <= 0)
        | (
            pd.to_numeric(recommendations.get("buy_zone_low"), errors="coerce")
            <= pd.to_numeric(recommendations.get("stop_loss"), errors="coerce")
        )
    )
    if invalid_risk.any():
        st.warning("当前历史推荐中含旧版无效风控区间；页面已禁止显示为可开仓，刷新后会按新规则重算。")

spot_dashboard = st.session_state.get("latest_spot_dashboard", pd.DataFrame())
breadth = st.session_state.get("latest_breadth", {})
global_indices = st.session_state.get("latest_global_indices", pd.DataFrame())
global_summary = st.session_state.get("latest_global_summary", {})
if not breadth:
    breadth, global_indices, global_summary = load_latest_market_snapshot()
if not breadth:
    try:
        with st.spinner("正在更新全市场宽度和全球指数"):
            spot_dashboard, breadth, global_indices, global_summary = load_market_dashboard(ignore_proxy)
            st.session_state["latest_spot_dashboard"] = spot_dashboard
            st.session_state["latest_breadth"] = breadth
            st.session_state["latest_global_indices"] = global_indices
            st.session_state["latest_global_summary"] = global_summary
    except Exception:
        breadth, global_indices, global_summary = {}, pd.DataFrame(), {}
if not isinstance(spot_dashboard, pd.DataFrame) or spot_dashboard.empty:
    spot_dashboard = load_cached_spot_snapshot()
market_summary(breadth, global_summary)
render_market_data_status(spot_dashboard)
coverage = int(st.session_state.get("latest_fundamental_coverage", breadth.get("total", 0) if breadth else 0))
report_date = str(st.session_state.get("latest_financial_report_date", ""))
if coverage:
    report_note = f"，财报期 {format_date(report_date)}" if report_date else ""
    st.caption(f"本轮基本面/资金面覆盖 {coverage:,} 只A股{report_note}；K线只复核初筛后的高分候选。")

dashboard_notice = st.session_state.pop("dashboard_notice", "")
if dashboard_notice:
    st.success(dashboard_notice)

market_refresh_col, refresh_col, sync_col, hint_col = st.columns([0.14, 0.2, 0.18, 0.48])
with market_refresh_col:
    market_refresh_clicked = st.button("只更新行情", width="stretch")
with refresh_col:
    refresh_clicked = st.button("刷新并生成今日推荐", type="primary", width="stretch")
with sync_col:
    sync_miniapp_clicked = st.button("同步到小程序", width="stretch")
with hint_col:
    st.write("生成成功后会自动同步；小程序下拉或点击刷新即可读取。")

if sync_miniapp_clicked:
    with st.spinner("正在同步最新行情与推荐到小程序"):
        sync_result = publish_market_feed_if_configured()
    if sync_result is None:
        st.warning("尚未配置小程序云端同步，请检查 data/miniapp_sync.json。")
    elif sync_result[0]:
        st.success(sync_result[1])
    else:
        st.error(sync_result[1])

if market_refresh_clicked:
    st.cache_data.clear()
    try:
        with st.spinner("正在更新全市场行情"):
            spot_dashboard, breadth, global_indices, global_summary = load_market_dashboard(ignore_proxy)
            st.session_state["latest_spot_dashboard"] = spot_dashboard
            st.session_state["latest_breadth"] = breadth
            st.session_state["latest_global_indices"] = global_indices
            st.session_state["latest_global_summary"] = global_summary
            save_market_snapshot(
                run_id=int(current_run["id"]) if current_run and current_run.get("id") else None,
                market=breadth,
                global_summary=global_summary,
                global_indices=global_indices,
            )
            sync_result = publish_market_feed_if_configured()
        notice = "市场行情已更新，推荐列表未重新计算。"
        if sync_result is not None:
            notice += " " + sync_result[1]
        st.session_state["dashboard_notice"] = notice
        st.rerun()
    except Exception as exc:
        LOGGER.exception("更新全市场行情失败")
        st.error(friendly_data_source_error(exc, "更新市场行情"))

if refresh_clicked:
    st.cache_data.clear()
    try:
        with st.spinner("正在生成每日推荐，热点和K线会一起计算"):
            recommendations, context, errors = run_scan(
                scan_size=scan_size,
                top_n=top_n,
                min_turnover=min_turnover_yi * 100_000_000,
                ignore_proxy=ignore_proxy,
                min_net_inflow=min_net_inflow_yi * 100_000_000,
                industry_filter=industry_filter,
                min_price=min_price,
                max_price=max(max_price, min_price),
                random_price_pick=random_price_pick,
                require_fund_flow=require_fund_flow,
                require_valuation=require_valuation,
                require_cashflow=require_cashflow,
                require_dividend=require_dividend,
                allow_near_match=allow_near_match,
            )
            recommendations = hydrate(recommendations)
            if isinstance(context.spot, pd.DataFrame) and not context.spot.empty:
                st.session_state["latest_spot_dashboard"] = context.spot
        st.success(f"今日推荐已刷新并保存，共 {len(recommendations)} 只。")
        with st.spinner("正在把最新结果同步到小程序"):
            sync_result = publish_market_feed_if_configured()
        if sync_result is not None:
            if sync_result[0]:
                st.success(sync_result[1])
            else:
                st.warning(sync_result[1] + "；PC 数据已保存，可稍后点击“同步到小程序”重试。")
        near_match_notes = [item for item in errors if str(item).startswith("严格组合筛选无结果")]
        if near_match_notes:
            st.warning(near_match_notes[-1])
        record_activity(
            "recommendation_refresh",
            request_params={
                "scan_size": scan_size,
                "industry_filter": industry_filter,
                "min_turnover_yi": min_turnover_yi,
                "min_net_inflow_yi": min_net_inflow_yi,
                "min_price": min_price,
                "max_price": max(max_price, min_price),
                "random_price_pick": random_price_pick,
            },
            result_snapshot={"recommendation_count": len(recommendations)},
        )
    except DataSourceError as exc:
        st.error(str(exc))
    except Exception:
        LOGGER.exception("刷新每日推荐失败")
        st.error("刷新失败，系统已记录详细信息，请稍后重试。")

outcome_frame = load_recommendation_outcomes()
sentiment_history = load_sentiment_history(limit=60)
recommendations = enrich_recommendations_for_product(recommendations, outcome_frame, sentiment_history)

st.divider()
workspace = st.radio(
    "工作区",
    tuple(DASHBOARD_WORKSPACES),
    horizontal=True,
    label_visibility="collapsed",
    key="dashboard_workspace",
)
module_col, module_hint_col = st.columns([0.3, 0.7])
with module_col:
    active_module = st.selectbox(
        "当前功能",
        DASHBOARD_WORKSPACES[workspace],
        key=f"dashboard_module_{workspace}",
    )
with module_hint_col:
    st.markdown(
        f'<div class="workspace-hint">{html_lib.escape(DASHBOARD_MODULE_HINTS[active_module])}</div>',
        unsafe_allow_html=True,
    )

if active_module == "短中长期推荐":
    if recommendations.empty:
        st.info("还没有推荐数据，点击上方“刷新并生成今日推荐”。")
    else:
        render_recommendation_cards(recommendations)
        with st.expander("查看推荐评分图"):
            st.plotly_chart(chart_scores(recommendations), width="stretch", config=PLOTLY_CONFIG)

if active_module == "条件选股":
    render_condition_selector_page(recommendations)

if active_module == "商业体检":
    render_commercial_audit_page(recommendations, outcome_frame, breadth)

if active_module == "模拟交易":
    render_paper_trading_page(recommendations, outcome_frame)

if active_module == "每日复盘":
    capital_hotspots = st.session_state.get("latest_capital_hotspots", pd.DataFrame())
    if capital_hotspots.empty:
        selected_run_id = int(current_run["id"]) if current_run and current_run.get("id") else None
        capital_hotspots = load_capital_hotspots(run_id=selected_run_id)
    render_daily_review_page(recommendations, outcome_frame, capital_hotspots, breadth, global_summary, sentiment_history)

if active_module == "市场地图":
    capital_hotspots = st.session_state.get("latest_capital_hotspots", pd.DataFrame())
    if capital_hotspots.empty:
        selected_run_id = int(current_run["id"]) if current_run and current_run.get("id") else None
        capital_hotspots = load_capital_hotspots(run_id=selected_run_id)
    ladder = load_limit_up_ladder()
    hotspot_history = load_capital_hotspot_timeline()
    render_market_map_page(recommendations, capital_hotspots, ladder, hotspot_history)

if active_module == "行业题材":
    boards = st.session_state.get("latest_context_boards", pd.DataFrame())
    if boards.empty:
        st.info("刷新一次推荐后，这里会显示本轮使用的行业和题材热点。")
    else:
        st.plotly_chart(chart_boards(boards), width="stretch", config=PLOTLY_CONFIG)
        board_display = boards.head(30)[
            ["board_type", "board_name", "change_pct", "turnover_rate", "up_count", "down_count", "leader", "heat_score"]
        ].rename(
            columns={
                "board_type": "类型",
                "board_name": "行业/题材",
                "change_pct": "涨跌幅%",
                "turnover_rate": "换手率%",
                "up_count": "上涨家数",
                "down_count": "下跌家数",
                "leader": "领涨股票",
                "heat_score": "热点强度",
            }
        )
        st.dataframe(board_display, width="stretch", hide_index=True)

    errors = st.session_state.get("latest_errors", [])
    if errors:
        with st.expander(f"数据源提示 {len(errors)} 条"):
            st.write("\n".join(errors[:80]))

    st.subheader("全球市场环境")
    if global_indices.empty:
        st.info("全球指数暂时不可用，推荐评分会自动忽略该因子。")
    else:
        global_display = global_indices.rename(
            columns={
                "name": "指数",
                "price": "最新价",
                "change_amount": "涨跌额",
                "change_pct": "涨跌幅%",
                "quote_time": "时间",
            }
        )[["指数", "最新价", "涨跌额", "涨跌幅%", "时间"]]
        global_display["时间"] = global_display["时间"].map(format_datetime)
        st.dataframe(global_display, width="stretch", hide_index=True)
        st.caption(
            f"全球风险偏好：{global_summary.get('label', '中性')}，"
            f"综合分 {float(global_summary.get('score', 50)):.1f}。该因子只占推荐评分的小权重。"
        )

if active_module == "资金热点":
    capital_hotspots = st.session_state.get("latest_capital_hotspots", pd.DataFrame())
    if capital_hotspots.empty:
        selected_run_id = int(current_run["id"]) if current_run and current_run.get("id") else None
        capital_hotspots = load_capital_hotspots(run_id=selected_run_id)
    st.subheader("主力资金热门行业与题材")
    st.caption("每日对应即时资金，每周对应5日排行，每月对应20日排行；单位为亿元。")
    render_capital_hotspots(capital_hotspots)

if active_module == "主线雷达":
    capital_hotspots = st.session_state.get("latest_capital_hotspots", pd.DataFrame())
    if capital_hotspots.empty:
        selected_run_id = int(current_run["id"]) if current_run and current_run.get("id") else None
        capital_hotspots = load_capital_hotspots(run_id=selected_run_id)
    hotspot_history = load_capital_hotspot_timeline()
    render_mainline_radar(capital_hotspots, hotspot_history)

if active_module == "情绪周期":
    try:
        render_sentiment_cycle(breadth, ignore_proxy)
    except Exception:
        LOGGER.exception("市场情绪周期页面初始化失败")
        st.error("市场情绪周期暂时无法加载，其他功能不受影响。请稍后重试或联系管理员查看服务器日志。")

if active_module == "龙头追踪":
    capital_hotspots = st.session_state.get("latest_capital_hotspots", pd.DataFrame())
    if capital_hotspots.empty:
        selected_run_id = int(current_run["id"]) if current_run and current_run.get("id") else None
        capital_hotspots = load_capital_hotspots(run_id=selected_run_id)
    boards = st.session_state.get("latest_context_boards", pd.DataFrame())
    render_leader_tracking(capital_hotspots, boards, ignore_proxy)

if active_module == "持仓中心":
    render_position_center_page(ignore_proxy, recommendations, breadth, global_summary)

if active_module == "预警中心":
    capital_hotspots = st.session_state.get("latest_capital_hotspots", pd.DataFrame())
    if capital_hotspots.empty:
        selected_run_id = int(current_run["id"]) if current_run and current_run.get("id") else None
        capital_hotspots = load_capital_hotspots(run_id=selected_run_id)
    render_alert_center_page(ignore_proxy, recommendations, capital_hotspots, sentiment_history)

if active_module == "策略回测":
    render_strategy_backtest_center(recommendations, outcome_frame, ignore_proxy)

if active_module == "个股买卖点":
    initialize_position_inputs()
    st.subheader("输入股票名称或代码分析当天买卖点")
    query_col, years_col, button_col = st.columns([0.48, 0.2, 0.32])
    with query_col:
        query = st.text_input(
            "股票名称或代码",
            placeholder="例如：平安银行、000001、600519",
            key="position_input_query",
        )
    with years_col:
        years = st.selectbox("K线年限", [1, 2, 3, 5], key="position_input_years")
    with button_col:
        analyze_clicked = st.button("分析买卖点", type="primary", width="stretch")

    with st.expander("我的持仓与做T回本规划", expanded=True):
        st.caption("填写个人持仓后，系统会结合实时价、技术趋势、市场温度、全球风险和资金热点进行情景推演。")
        cost_col, shares_col, cash_col, ratio_col = st.columns(4)
        with cost_col:
            holding_cost = st.number_input(
                "持仓成本价",
                min_value=0.01,
                max_value=10000.0,
                step=0.01,
                format="%.2f",
                key="position_input_holding_cost",
            )
        with shares_col:
            holding_shares = st.number_input(
                "持股数量",
                min_value=100,
                max_value=100_000_000,
                step=100,
                help="A股按100股整数手填写。",
                key="position_input_holding_shares",
            )
        with cash_col:
            available_cash = st.number_input(
                "可动用资金",
                min_value=0.0,
                max_value=100_000_000.0,
                step=1000.0,
                format="%.2f",
                key="position_input_available_cash",
            )
        with ratio_col:
            t_ratio = st.select_slider(
                "单次做T比例",
                options=[10, 15, 20, 25, 30, 40, 50],
                help="趋势或市场偏弱时，系统会自动压低实际参考股数。",
                key="position_input_t_ratio",
            )
        assets_col, sellable_col, mode_col, cost_setting_col = st.columns(4)
        with assets_col:
            account_assets = st.number_input(
                "账户总资产（选填）",
                min_value=0.0,
                max_value=1_000_000_000.0,
                step=10_000.0,
                format="%.2f",
                help="用于计算该股票占账户总资产的比例；填0表示暂不判断集中度。",
                key="position_input_account_assets",
            )
        with sellable_col:
            saved_sellable = min(
                int(st.session_state.get("position_input_sellable_shares", holding_shares)),
                int(holding_shares),
            )
            if saved_sellable != st.session_state.get("position_input_sellable_shares"):
                st.session_state["position_input_sellable_shares"] = saved_sellable // 100 * 100
            sellable_shares = st.number_input(
                "今日可卖底仓",
                min_value=0,
                max_value=int(holding_shares),
                step=100,
                help="不含今天新买入且尚不可卖的股票，必须为100股整数倍。",
                key="position_input_sellable_shares",
            )
        with mode_col:
            t_preference = st.selectbox(
                "做T方式",
                ["自动判断", "先卖后买", "先买后卖"],
                help="先卖后买适合弱势反弹；先买后卖需要可用资金，并用原有可卖底仓完成当日卖出。",
                key="position_input_t_preference",
            )
        with cost_setting_col:
            slippage_bps = st.select_slider(
                "预计滑点（基点）",
                options=[0, 5, 10, 15, 20, 30],
                help="10个基点等于0.10%，用于避免把理论成交价当成实际成交价。",
                key="position_input_slippage_bps",
            )
        plan_requested = st.checkbox("本次同时生成持仓回本与做T方案", value=True)

    if analyze_clicked:
        try:
            spot = load_spot_for_lookup(ignore_proxy)
            symbol, name = resolve_stock(query, spot)
            history = load_history(symbol, years, ignore_proxy)
            spot_match = spot[spot["code"] == plain_code(symbol)]
            if not spot_match.empty:
                history = append_spot_bar(history, spot_match.iloc[0])
            report = analyze_stock(history, symbol=symbol, name=name)
            position_plan = None
            if plan_requested:
                stock_context = recommendations[
                    recommendations["symbol"].astype(str).str.upper() == report.symbol.upper()
                ] if not recommendations.empty and "symbol" in recommendations.columns else pd.DataFrame()
                industry = ""
                topics: list[str] = []
                if not stock_context.empty:
                    industry = str(stock_context.iloc[0].get("industry") or "")
                    topics = parse_list(stock_context.iloc[0].get("topics"))
                capital_context = st.session_state.get("latest_capital_hotspots", pd.DataFrame())
                if capital_context.empty:
                    selected_run_id = int(current_run["id"]) if current_run and current_run.get("id") else None
                    capital_context = load_capital_hotspots(run_id=selected_run_id)
                news_context, news_errors = load_news_context(report.symbol, ignore_proxy)
                position_plan = analyze_position(
                    history,
                    report,
                    cost_price=float(holding_cost),
                    shares=int(holding_shares),
                    available_cash=float(available_cash),
                    t_ratio=float(t_ratio) / 100,
                    account_assets=float(account_assets),
                    sellable_shares=int(sellable_shares),
                    t_preference=t_preference,
                    slippage_rate=float(slippage_bps) / 10_000,
                    breadth=breadth,
                    global_summary=global_summary,
                    capital_hotspots=capital_context,
                    industry=industry,
                    topics=topics,
                    news=news_context,
                )
                st.session_state["position_news_errors"] = news_errors
            save_stock_analysis(query, report)
            position_saved = record_activity(
                "position_plan" if position_plan else "stock_analysis",
                stock_symbol=report.symbol.upper(),
                stock_name=report.name,
                request_params={
                    "query": query,
                    "years": years,
                    "holding_cost": float(holding_cost) if position_plan else None,
                    "holding_shares": int(holding_shares) if position_plan else None,
                    "available_cash": float(available_cash) if position_plan else None,
                    "t_ratio": float(t_ratio) / 100 if position_plan else None,
                    "account_assets": float(account_assets) if position_plan else None,
                    "sellable_shares": int(sellable_shares) if position_plan else None,
                    "t_preference": t_preference if position_plan else None,
                    "slippage_bps": int(slippage_bps) if position_plan else None,
                },
                result_snapshot={
                    "stock_analysis": report.as_dict(),
                    "position_plan": position_plan.as_dict() if position_plan else None,
                },
            )
            st.session_state["stock_report"] = report
            st.session_state["stock_history"] = history
            st.session_state["position_plan"] = position_plan
            if position_saved and position_plan:
                st.toast("本次输入已保存，下次打开页面会自动带出。")
        except (DataSourceError, ValueError) as exc:
            st.error(f"分析失败：{exc}")
        except Exception:
            LOGGER.exception("个股分析失败")
            st.error("分析失败，系统已记录详细信息，请稍后重试。")

    report = st.session_state.get("stock_report")
    history = st.session_state.get("stock_history")
    position_plan: PositionPlan | None = st.session_state.get("position_plan")
    if report and history is not None:
        metric_row(
            [
                ("代码", report.symbol.upper()),
                ("名称", report.name or "-"),
                ("评分", str(report.score)),
                ("级别", report.rating),
                ("建议仓位", f"{report.position_pct:.0%}"),
                ("收盘价", f"{report.close:.2f}"),
            ]
        )
        if report.position_pct > 0:
            st.markdown(
                f"""
                <span class="pill">买点 {report.buy_zone_low:.2f}-{report.buy_zone_high:.2f}</span>
                <span class="pill">止损 {report.stop_loss:.2f}</span>
                <span class="pill">目标 {report.take_profit_1:.2f}/{report.take_profit_2:.2f}</span>
                <span class="pill">移动止盈 {report.trailing_stop:.2f}</span>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.warning(f"当前技术评分为 {report.score}，建议仓位为0%；暂不开仓，等待趋势修复后重新分析。")

        if position_plan:
            st.subheader("我的持仓与做T回本方案")
            metric_row(
                [
                    ("当前价", f"{position_plan.current_price:.2f}"),
                    ("持仓成本", f"{position_plan.cost_price:.2f}"),
                    ("持仓市值", f"{position_plan.market_value:,.0f}"),
                    ("浮动盈亏", f"{position_plan.unrealized_pnl:+,.0f}"),
                    ("收益率", f"{position_plan.unrealized_return:+.2%}"),
                    ("回本距离", f"{position_plan.break_even_gap:+.2%}"),
                    ("参考T股数", f"{position_plan.t_shares:,}"),
                    ("可行性", f"{position_plan.feasibility_score}分"),
                ]
            )
            if position_plan.feasibility_score >= 70:
                st.success(
                    f"{position_plan.feasibility_label}：{position_plan.action_summary} "
                    f"当前判断：{position_plan.trend_label}、{position_plan.market_label}；"
                    f"{position_plan.hotspot_label}。{position_plan.event_label}。"
                )
            elif position_plan.feasibility_score >= 45:
                st.warning(
                    f"{position_plan.feasibility_label}：{position_plan.action_summary} "
                    f"当前判断：{position_plan.trend_label}、{position_plan.market_label}；"
                    f"{position_plan.hotspot_label}。{position_plan.event_label}。"
                )
            else:
                st.error(
                    f"{position_plan.feasibility_label}：{position_plan.action_summary} "
                    f"当前判断：{position_plan.trend_label}、{position_plan.market_label}；"
                    f"{position_plan.hotspot_label}。{position_plan.event_label}。"
                )
            metric_row(
                [
                    ("执行方式", position_plan.t_mode),
                    ("可卖底仓", f"{position_plan.sellable_shares:,}股"),
                    ("保留核心仓", f"{position_plan.core_shares:,}股"),
                    (
                        "持仓集中度",
                        f"{position_plan.position_concentration:.1%}"
                        if position_plan.position_concentration is not None
                        else "未填写",
                    ),
                    ("集中度判断", position_plan.concentration_label),
                    ("最低有效价差", f"{position_plan.break_even_spread_pct:.2%}"),
                    ("失效位", f"{position_plan.invalidation_price:.2f}"),
                ]
            )
            metric_row(
                [
                    ("20日中位振幅", f"{position_plan.median_amplitude_20d:.2%}"),
                    ("可做T机会天数", f"{position_plan.opportunity_days_20d}/20"),
                    ("20日平均成交额", f"{position_plan.average_turnover_20d / 100_000_000:.2f}亿"),
                    ("流动性", position_plan.liquidity_label),
                    ("需弥补浮亏", f"{position_plan.required_recovery_profit:,.0f}元"),
                ]
            )
            st.caption(
                f"若从当前价跌至失效位，持仓市值风险约 {position_plan.risk_to_invalidation:,.0f}元；"
                "该数值用于风险预算，不是保证会在失效位成交。"
            )
            zone_col, recovery_col = st.columns(2)
            with zone_col:
                st.markdown("**本轮观察区间**")
                st.write(f"- 低吸观察：`{position_plan.t_buy_low:.2f}-{position_plan.t_buy_high:.2f}`")
                st.write(f"- 高抛观察：`{position_plan.t_sell_low:.2f}-{position_plan.t_sell_high:.2f}`")
                st.write(f"- 区间中值价差：`{position_plan.expected_spread_pct:.2%}`")
                st.write(f"- 费用盈亏平衡价差：`{position_plan.break_even_spread_pct:.2%}`")
                st.write(f"- 单轮理论净收益：`{position_plan.estimated_round_profit:,.0f}元`")
            with recovery_col:
                st.markdown("**成本改善情景**")
                st.write(f"- 当前回本价：`{position_plan.cost_price:.2f}`")
                st.write(f"- 成功1轮后理论成本：`{position_plan.cost_after_one_round:.2f}`")
                st.write(f"- 成功3轮后理论成本：`{position_plan.cost_after_three_rounds:.2f}`")
                st.write(f"- 当前需弥补浮亏：`{position_plan.required_recovery_profit:,.0f}元`")
                rounds_text = (
                    f"{position_plan.estimated_rounds_to_break_even}轮"
                    if position_plan.estimated_rounds_to_break_even is not None
                    else "当前方案无法估算"
                )
                st.write(f"- 静态回本轮数：`{rounds_text}`")
                st.caption("按区间中值、当前费率估算，不包含无法成交、滑点扩大和卖飞风险。")
            if position_plan.t_shares > 0:
                st.markdown("**三档分批计划**")
                ladder_buy_col, ladder_sell_col = st.columns(2)
                with ladder_buy_col:
                    st.caption("低吸计划")
                    st.dataframe(
                        pd.DataFrame(position_plan.buy_ladder).rename(
                            columns={"price": "参考价格", "shares": "参考股数"}
                        ),
                        width="stretch",
                        hide_index=True,
                    )
                with ladder_sell_col:
                    st.caption("高抛计划")
                    st.dataframe(
                        pd.DataFrame(position_plan.sell_ladder).rename(
                            columns={"price": "参考价格", "shares": "参考股数"}
                        ),
                        width="stretch",
                        hide_index=True,
                    )
            else:
                st.warning("本轮参考T股数为0：条件不满足时保持不交易，也是持仓管理方案的一部分。")
            st.markdown("**执行步骤**")
            st.write("\n".join(f"{index}. {item}" for index, item in enumerate(position_plan.steps, start=1)))
            if position_plan.event_headlines:
                with st.expander("本轮参考的政策、国际事件与个股资讯"):
                    st.write("\n".join(f"- {item}" for item in position_plan.event_headlines))
                    st.caption("仅按标题关键词识别风险方向；新闻真伪、影响范围和持续时间仍需人工核验。")
            news_errors = st.session_state.get("position_news_errors", [])
            if news_errors:
                st.caption("；".join(news_errors))
            with st.expander("重要风险与交易规则"):
                st.warning("\n".join(f"- {item}" for item in position_plan.risks))

        st.plotly_chart(
            chart_price(history, report if report.position_pct > 0 else None),
            width="stretch",
            config=PLOTLY_CONFIG,
        )
        buy_col, sell_col = st.columns(2)
        with buy_col:
            st.write("技术信号依据")
            st.write("\n".join(f"- {item}" for item in report.reasons))
        with sell_col:
            st.write("卖出条件")
            st.write("\n".join(f"- {item}" for item in report.sell_triggers))
            if report.warnings:
                st.warning("\n".join(report.warnings))

        st.divider()
        st.subheader("历史技术策略回测")
        st.caption(
            "用途：检查这套通用技术规则过去用在该股票上是否有效。"
            "它不是今天这次个股分析的准确率，也不是每日推荐业绩。"
        )
        st.caption(
            "规则按收盘后生成信号、下一可交易日开盘成交；"
            "按100股整数手，计入佣金最低5元、卖出印花税和滑点。"
        )
        capital_col, position_col, cost_col, backtest_col = st.columns([0.24, 0.24, 0.24, 0.28])
        with capital_col:
            initial_cash = st.number_input(
                "初始资金",
                min_value=10_000,
                max_value=10_000_000,
                value=100_000,
                step=10_000,
            )
        with position_col:
            max_position_pct = st.slider("单股最大仓位", 10, 100, 30, step=5) / 100
        with cost_col:
            commission_rate = st.number_input(
                "佣金费率",
                min_value=0.0,
                max_value=0.01,
                value=0.0003,
                step=0.0001,
                format="%.4f",
            )
        with backtest_col:
            st.write("")
            st.write("")
            run_backtest_clicked = st.button("运行历史回测", width="stretch")

        if run_backtest_clicked:
            try:
                curve, trades, metrics = run_backtest(
                    history,
                    initial_cash=float(initial_cash),
                    commission_rate=float(commission_rate),
                    max_position_pct=float(max_position_pct),
                )
                st.session_state["backtest_curve"] = curve
                st.session_state["backtest_trades"] = trades
                st.session_state["backtest_metrics"] = metrics
                st.session_state["backtest_symbol"] = report.symbol
            except ValueError as exc:
                st.error(f"回测失败：{exc}")
            except Exception:
                LOGGER.exception("历史回测失败")
                st.error("回测失败，系统已记录详细信息，请稍后重试。")

        if (
            st.session_state.get("backtest_symbol") == report.symbol
            and "backtest_curve" in st.session_state
        ):
            curve = st.session_state["backtest_curve"]
            trades = st.session_state["backtest_trades"]
            metrics = st.session_state["backtest_metrics"]
            metric_row(
                [
                    ("策略收益", pct(metrics["total_return"])),
                    ("买入持有", pct(metrics["benchmark_return"])),
                    ("年化收益", pct(metrics["annual_return"])),
                    ("最大回撤", pct(metrics["max_drawdown"])),
                    ("夏普比率", f"{metrics['sharpe']:.2f}"),
                    ("胜率", pct(metrics["win_rate"])),
                    ("已完成交易", str(int(metrics["closed_trade_count"]))),
                ]
            )
            st.plotly_chart(chart_backtest(curve), width="stretch", config=PLOTLY_CONFIG)
            st.caption(
                "图表上半部：蓝线是按规则模拟交易后的资金，灰色虚线是假设一直持有该股。"
                "下半部红色区域是资金从此前最高点下跌的幅度，越深表示回撤风险越大。"
            )
            if trades.empty:
                st.info("当前样本期没有形成可成交的完整交易。")
            else:
                with st.expander("展开模拟买入卖出流水"):
                    st.caption("每一行是回测程序模拟的一次成交，包括信号日期、成交日期、价格、股数、费用和卖出原因。")
                    st.dataframe(trades, width="stretch", hide_index=True)
            st.caption("该回测只验证技术策略，不还原历史时点的热点、资金流和财报快照，不代表未来收益。")

if active_module == "数据表":
    st.subheader("数据与运行记录")
    runs = load_runs(limit=20)
    latest_status = RUN_STATUS_LABELS.get(str((current_run or {}).get("status", "")), "暂无")
    latest_time = format_datetime((current_run or {}).get("run_time"))
    error_count = len(st.session_state.get("latest_errors", []))
    metric_row(
        [
            ("最近扫描状态", latest_status),
            ("最近扫描时间", latest_time),
            ("策略版本", str((current_run or {}).get("strategy_version") or STRATEGY_VERSION)),
            ("财务资金覆盖", f"{coverage:,}只" if coverage else "暂无"),
            ("数据源提示", f"{error_count}条"),
        ]
    )
    st.caption("扫描参数和策略版本会随每次运行保存，便于后续复盘时区分不同规则。")
    st.write("最近20次扫描")
    if runs.empty:
        st.info("暂无扫描记录。")
    else:
        run_display = pd.DataFrame(
            {
                "扫描编号": runs["id"],
                "数据日期": runs["trade_date"].map(format_date),
                "运行时间": runs["run_time"].map(format_datetime),
                "策略版本": runs.get("strategy_version", pd.Series("-", index=runs.index)).fillna("-"),
                "K线复核数": runs["scan_size"],
                "每周期上限": runs["top_n"],
                "最低成交额（亿元）": pd.to_numeric(runs["min_turnover"], errors="coerce") / 100_000_000,
                "状态": runs["status"].map(RUN_STATUS_LABELS).fillna(runs["status"]),
                "运行说明": runs["note"].fillna(""),
            }
        )
        st.dataframe(run_display, width="stretch", hide_index=True)

    if not recommendations.empty:
        st.write("当前推荐记录")
        st.dataframe(display_recommendations(recommendations), width="stretch", hide_index=True)
        csv = display_recommendations(recommendations).to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button(
            "导出当前推荐表",
            data=csv,
            file_name=f"A股每日推荐_{date.today():%Y%m%d}.csv",
            mime="text/csv",
        )

if active_module == "推送":
    st.subheader("推送今日推荐")
    if recommendations.empty:
        st.info("请先刷新生成今日推荐。")
    else:
        report_text = push_report(recommendations)
        st.text_area("推送内容", value=report_text, height=260)
        provider_col, webhook_col = st.columns([0.24, 0.76])
        with provider_col:
            provider = st.selectbox("通道", ["企业微信", "钉钉", "飞书"])
        with webhook_col:
            webhook_url = st.text_input(
                "机器人地址",
                type="password",
                placeholder="请输入所选平台的官方加密机器人地址",
            )
        if st.button("发送推送", type="primary"):
            ok, message = send_webhook(provider, webhook_url, report_text)
            if ok:
                st.success("推送已发送。")
            else:
                st.error(message)
