from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class CommercialItem:
    module: str
    title: str
    status: str
    priority: str
    impact: str
    next_step: str


COMMERCIAL_ITEMS: list[CommercialItem] = [
    CommercialItem(
        "可信度",
        "推荐可信度评分体系",
        "已上线",
        "P0",
        "用户知道为什么可以参考，而不是只看到推荐名单。",
        "继续积累复盘样本，并展示分策略、分行业胜率。",
    ),
    CommercialItem(
        "持仓",
        "持仓回本与做T方案",
        "已上线",
        "P0",
        "提高用户每日打开频率，形成付费粘性。",
        "增加持仓组合风险、仓位集中度和每日方案对比。",
    ),
    CommercialItem(
        "预警",
        "网页内预警中心",
        "已上线",
        "P0",
        "把选股结果转成可执行提醒，减少用户错过买卖点。",
        "接入企业微信、短信或邮件通知，并增加免打扰时段。",
    ),
    CommercialItem(
        "回测",
        "策略回测与模拟交易",
        "已上线",
        "P0",
        "用历史复盘建立付费可信度。",
        "补充分策略胜率、参数稳定性、样本外验证和交易成本敏感性。",
    ),
    CommercialItem(
        "数据",
        "多数据源与数据质量提示",
        "待优化",
        "P0",
        "商业产品必须说明数据是否新鲜、是否缺失、是否降级。",
        "增加数据源健康面板、行情时间戳、失败重试和备用源说明。",
    ),
    CommercialItem(
        "风控",
        "组合级风控",
        "待优化",
        "P0",
        "用户真正亏钱往往来自仓位和相关性，不只是单股判断。",
        "增加单行业暴露、最大单股仓位、账户回撤预警和黑名单。",
    ),
    CommercialItem(
        "投研",
        "AI投研解释与风险失效点",
        "已上线",
        "P1",
        "提高用户理解力，减少盲目追涨。",
        "为每只股票增加同板块替代标的、政策事件、负面风险摘要。",
    ),
    CommercialItem(
        "市场",
        "板块地图、主线雷达、情绪周期",
        "已上线",
        "P1",
        "把单股推荐放到市场环境里解释，更像专业工具。",
        "增加主线持续天数、龙头切换确认、高潮/退潮操作建议。",
    ),
    CommercialItem(
        "账号",
        "会员到期与管理员后台",
        "已上线",
        "P1",
        "支持SaaS售卖和账号续费管理。",
        "增加套餐、支付流水、到期提醒、设备登录限制。",
    ),
    CommercialItem(
        "合规",
        "风险披露与销售话术约束",
        "已上线",
        "P0",
        "减少宣传违规和用户误解。",
        "所有页面继续避免确定性收益、保证回本和内部消息类承诺表达。",
    ),
    CommercialItem(
        "运营",
        "用户行为与留存分析",
        "待优化",
        "P2",
        "知道用户每天看什么、搜什么、在哪里流失。",
        "增加访问日志看板、功能点击、搜索股票排行和续费风险用户。",
    ),
    CommercialItem(
        "部署",
        "HTTPS、备份、监控",
        "部分上线",
        "P0",
        "外部付费用户访问时，稳定性和数据安全是基础。",
        "补齐HTTPS、自动备份校验、服务心跳、异常日志中文化。",
    ),
]


def commercial_plan_frame() -> pd.DataFrame:
    return pd.DataFrame([item.__dict__ for item in COMMERCIAL_ITEMS]).rename(
        columns={
            "module": "模块",
            "title": "优化项",
            "status": "状态",
            "priority": "优先级",
            "impact": "商业价值",
            "next_step": "下一步",
        }
    )


def user_behavior_summary(
    accounts: pd.DataFrame,
    daily_usage: pd.DataFrame,
    query_records: pd.DataFrame,
    login_logs: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Summarize paid-user activity for operations."""
    login_logs = login_logs if login_logs is not None else pd.DataFrame()
    today = pd.Timestamp(date.today())
    result = {
        "active_today": 0,
        "active_7d": 0,
        "silent_7d": 0,
        "query_7d": 0,
        "stock_analysis_7d": 0,
        "top_feature": "-",
        "top_stock": "-",
        "login_success_7d": 0,
        "login_failure_7d": 0,
    }
    if not daily_usage.empty:
        usage = daily_usage.copy()
        usage["usage_date"] = pd.to_datetime(usage.get("usage_date"), errors="coerce")
        recent = usage[usage["usage_date"] >= today - pd.Timedelta(days=6)]
        today_usage = usage[usage["usage_date"] == today]
        result["active_today"] = int(today_usage["user_id"].nunique()) if "user_id" in today_usage else 0
        result["active_7d"] = int(recent["user_id"].nunique()) if "user_id" in recent else 0
        result["query_7d"] = int(pd.to_numeric(recent.get("query_count"), errors="coerce").fillna(0).sum())
        result["stock_analysis_7d"] = int(
            pd.to_numeric(recent.get("stock_analysis_count"), errors="coerce").fillna(0).sum()
        )
    if not accounts.empty and "id" in accounts:
        result["silent_7d"] = max(int(accounts["id"].nunique()) - int(result["active_7d"]), 0)
    if not query_records.empty:
        queries = query_records.copy()
        queries["created_at"] = pd.to_datetime(queries.get("created_at"), errors="coerce")
        recent_queries = queries[queries["created_at"] >= pd.Timestamp.now() - pd.Timedelta(days=7)]
        if not recent_queries.empty and "query_type" in recent_queries:
            top_feature = recent_queries["query_type"].fillna("未知").value_counts().head(1)
            if not top_feature.empty:
                result["top_feature"] = f"{top_feature.index[0]}（{int(top_feature.iloc[0])}次）"
        if not recent_queries.empty and "stock_name" in recent_queries:
            stock_series = recent_queries["stock_name"].fillna("").astype(str)
            stock_series = stock_series[stock_series.str.len() > 0]
            top_stock = stock_series.value_counts().head(1)
            if not top_stock.empty:
                result["top_stock"] = f"{top_stock.index[0]}（{int(top_stock.iloc[0])}次）"
    if not login_logs.empty:
        logs = login_logs.copy()
        logs["created_at"] = pd.to_datetime(logs.get("created_at"), errors="coerce")
        recent_logs = logs[logs["created_at"] >= pd.Timestamp.now() - pd.Timedelta(days=7)]
        success = pd.to_numeric(recent_logs.get("success"), errors="coerce").fillna(0)
        result["login_success_7d"] = int((success == 1).sum())
        result["login_failure_7d"] = int((success == 0).sum())
    return result


def user_behavior_frames(
    daily_usage: pd.DataFrame,
    query_records: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build feature, stock and user activity rankings."""
    feature_rank = pd.DataFrame(columns=["功能", "使用次数"])
    stock_rank = pd.DataFrame(columns=["股票", "查询次数"])
    user_rank = pd.DataFrame(columns=["手机号", "姓名", "查询次数", "登录次数", "个股分析", "最后活跃"])
    if not query_records.empty:
        feature = query_records.get("query_type", pd.Series(dtype=str)).fillna("未知").astype(str)
        feature_rank = (
            feature.value_counts()
            .rename_axis("功能")
            .reset_index(name="使用次数")
            .head(20)
        )
        stock = query_records.get("stock_name", pd.Series(dtype=str)).fillna("").astype(str)
        stock_symbol = query_records.get("stock_symbol", pd.Series(dtype=str)).fillna("").astype(str)
        stock_label = (stock_symbol + " " + stock).str.strip()
        stock_label = stock_label[stock_label.str.len() > 0]
        if not stock_label.empty:
            stock_rank = stock_label.value_counts().rename_axis("股票").reset_index(name="查询次数").head(30)
    if not daily_usage.empty:
        usage = daily_usage.copy()
        for column in ["query_count", "login_count", "stock_analysis_count"]:
            usage[column] = pd.to_numeric(usage.get(column), errors="coerce").fillna(0)
        grouped = (
            usage.groupby(["mobile", "real_name"], dropna=False)
            .agg(
                查询次数=("query_count", "sum"),
                登录次数=("login_count", "sum"),
                个股分析=("stock_analysis_count", "sum"),
                最后活跃=("last_activity_at", "max"),
            )
            .reset_index()
            .rename(columns={"mobile": "手机号", "real_name": "姓名"})
            .sort_values(["查询次数", "登录次数"], ascending=False)
            .head(50)
        )
        user_rank = grouped
    return feature_rank, stock_rank, user_rank


def order_summary(
    plans: pd.DataFrame,
    orders: pd.DataFrame,
    payments: pd.DataFrame,
    accounts: pd.DataFrame,
) -> dict[str, Any]:
    """Summarize paid package and order operations."""
    paid_orders = orders.copy() if not orders.empty else pd.DataFrame()
    if not paid_orders.empty:
        paid_orders = paid_orders[paid_orders.get("order_status").astype(str).eq("paid")]
    paid_amount = (
        float(pd.to_numeric(paid_orders.get("paid_amount"), errors="coerce").fillna(0).sum())
        if not paid_orders.empty
        else 0.0
    )
    trial_count = 0
    if not plans.empty and not orders.empty:
        trial_plan_ids = set(
            plans[
                plans.get("price").pipe(pd.to_numeric, errors="coerce").fillna(0).eq(0)
                | plans.get("plan_name").fillna("").astype(str).str.contains("试用")
            ]["id"].astype(int).tolist()
        )
        if trial_plan_ids:
            trial_count = int(orders["plan_id"].astype(int).isin(trial_plan_ids).sum())
    expiring_7d = 0
    if not accounts.empty and "service_expires_at" in accounts:
        expires = pd.to_datetime(accounts["service_expires_at"], errors="coerce")
        now = pd.Timestamp.now()
        expiring_7d = int(expires.between(now, now + pd.Timedelta(days=7)).sum())
    return {
        "plan_count": int(len(plans)),
        "order_count": int(len(orders)),
        "paid_order_count": int(len(paid_orders)),
        "paid_amount": paid_amount,
        "payment_count": int(len(payments)),
        "trial_count": trial_count,
        "expiring_7d": expiring_7d,
    }


def commercial_audit(
    recommendations: pd.DataFrame,
    outcomes: pd.DataFrame,
    positions: pd.DataFrame,
    alert_rules: pd.DataFrame,
    alert_events: pd.DataFrame,
    breadth: dict[str, Any] | None = None,
) -> dict[str, Any]:
    breadth = breadth or {}
    rec_count = len(recommendations)
    outcome_count = len(outcomes)
    position_count = len(positions)
    alert_count = len(alert_rules)
    active_alert_count = (
        int(pd.to_numeric(alert_rules.get("enabled"), errors="coerce").fillna(0).sum())
        if not alert_rules.empty and "enabled" in alert_rules
        else 0
    )
    high_trust_count = (
        int((pd.to_numeric(recommendations.get("credibility_score"), errors="coerce").fillna(0) >= 70).sum())
        if not recommendations.empty and "credibility_score" in recommendations
        else 0
    )
    repeat_hit_count = (
        int(pd.to_numeric(recommendations.get("high_win_repeat"), errors="coerce").fillna(0).sum())
        if not recommendations.empty and "high_win_repeat" in recommendations
        else 0
    )
    market_total = int(breadth.get("total") or 0)

    score = 0
    score += 15 if rec_count else 0
    score += 15 if outcome_count >= max(rec_count, 1) else 6 if outcome_count else 0
    score += 12 if high_trust_count else 4 if rec_count else 0
    score += 12 if position_count else 0
    score += 12 if active_alert_count else 4 if alert_count else 0
    score += 10 if market_total >= 4000 else 4 if market_total else 0
    score += 12 if repeat_hit_count else 4 if high_trust_count else 0
    score += 12
    score = min(score, 100)

    if score >= 80:
        stage = "可小范围付费试运营"
    elif score >= 60:
        stage = "可内测，需要继续补数据可信度"
    else:
        stage = "产品雏形，需要先补复盘和预警闭环"

    gaps: list[str] = []
    if outcome_count < max(rec_count, 1):
        gaps.append("历史复盘样本不足，可信度解释还需要继续积累。")
    if not position_count:
        gaps.append("用户尚未沉淀持仓，付费粘性不足。")
    if not active_alert_count:
        gaps.append("预警规则未启用，推荐结果还没有形成提醒闭环。")
    if market_total < 4000:
        gaps.append("全市场覆盖不足或行情数据未更新，需要展示数据新鲜度。")
    if not gaps:
        gaps.append("核心闭环已具备，下一步重点是通知渠道、组合风控和数据源稳定性。")

    return {
        "score": score,
        "stage": stage,
        "recommendation_count": rec_count,
        "outcome_count": outcome_count,
        "high_trust_count": high_trust_count,
        "repeat_hit_count": repeat_hit_count,
        "position_count": position_count,
        "active_alert_count": active_alert_count,
        "event_count": len(alert_events),
        "market_total": market_total,
        "gaps": gaps,
    }


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _pct(value: Any) -> str:
    try:
        if value is None or pd.isna(value):
            return "-"
        return f"{float(value):.2%}"
    except Exception:
        return "-"


def credibility_summary(recommendations: pd.DataFrame, outcomes: pd.DataFrame) -> dict[str, Any]:
    """Summarize whether recommendations have enough historical proof to sell as a paid tool."""
    if recommendations.empty:
        return {
            "sample_count": 0,
            "covered_count": 0,
            "coverage_rate": 0.0,
            "high_trust_count": 0,
            "repeat_hit_count": 0,
            "avg_t1": None,
            "avg_t3": None,
            "avg_t5": None,
            "avg_t20": None,
            "max_drawdown": None,
            "stop_rate": None,
            "target_rate": None,
        }
    sample = pd.to_numeric(recommendations.get("credibility_sample_count"), errors="coerce").fillna(0)
    credibility = pd.to_numeric(recommendations.get("credibility_score"), errors="coerce").fillna(50)
    covered_count = int((sample > 0).sum())
    result = {
        "sample_count": int(sample.sum()),
        "covered_count": covered_count,
        "coverage_rate": covered_count / max(len(recommendations), 1),
        "high_trust_count": int((credibility >= 70).sum()),
        "repeat_hit_count": int(pd.to_numeric(recommendations.get("high_win_repeat"), errors="coerce").fillna(0).sum())
        if "high_win_repeat" in recommendations
        else 0,
        "avg_t1": None,
        "avg_t3": None,
        "avg_t5": None,
        "avg_t20": None,
        "max_drawdown": None,
        "stop_rate": None,
        "target_rate": None,
    }
    if not outcomes.empty:
        numeric = outcomes.copy()
        for column in ["t1_return", "t3_return", "t5_return", "t20_return", "max_drawdown_20", "stop_hit", "target1_hit"]:
            if column not in numeric.columns:
                numeric[column] = None
            numeric[column] = pd.to_numeric(numeric[column], errors="coerce")
        tracked = numeric[pd.to_numeric(numeric.get("available_days"), errors="coerce").fillna(0) > 0]
        if tracked.empty:
            tracked = numeric
        result.update(
            {
                "avg_t1": tracked["t1_return"].mean(),
                "avg_t3": tracked["t3_return"].mean(),
                "avg_t5": tracked["t5_return"].mean(),
                "avg_t20": tracked["t20_return"].mean(),
                "max_drawdown": tracked["max_drawdown_20"].min(),
                "stop_rate": tracked["stop_hit"].mean(),
                "target_rate": tracked["target1_hit"].mean(),
            }
        )
    return result


def data_source_health_frame(
    latest_run: dict[str, Any] | None,
    breadth: dict[str, Any] | None,
    errors: list[str] | None = None,
    capital_hotspots: pd.DataFrame | None = None,
    recommendations: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build a user-facing data quality board: time, coverage, failure and fallback."""
    breadth = breadth or {}
    errors = errors or []
    capital_hotspots = capital_hotspots if capital_hotspots is not None else pd.DataFrame()
    recommendations = recommendations if recommendations is not None else pd.DataFrame()
    run_time = "-"
    run_status = "-"
    if latest_run:
        run_time = str(latest_run.get("run_time") or latest_run.get("created_at") or "-")
        run_status = str(latest_run.get("status") or "-")
    market_total = int(breadth.get("total") or 0)
    rows = [
        {
            "数据模块": "全市场行情",
            "最新时间": run_time,
            "覆盖数量": f"{market_total:,}" if market_total else "-",
            "状态": "正常" if market_total >= 4000 and not errors else ("降级可用" if market_total else "异常"),
            "失败原因": "；".join(errors[:2]) if errors else "-",
            "备用方案": "使用最近一次市场快照和本地推荐记录" if errors else "无需备用",
        },
        {
            "数据模块": "推荐池",
            "最新时间": run_time,
            "覆盖数量": f"{len(recommendations):,}",
            "状态": "正常" if not recommendations.empty else "待生成",
            "失败原因": "-" if not recommendations.empty else "尚未生成今日推荐或读取为空",
            "备用方案": "读取最近一次已保存推荐",
        },
        {
            "数据模块": "资金热点",
            "最新时间": run_time,
            "覆盖数量": f"{len(capital_hotspots):,}",
            "状态": "正常" if not capital_hotspots.empty else "降级可用",
            "失败原因": "-" if not capital_hotspots.empty else "板块/题材资金接口暂不可用",
            "备用方案": "使用推荐池行业与题材聚合",
        },
        {
            "数据模块": "任务运行",
            "最新时间": run_time,
            "覆盖数量": "-",
            "状态": "正常" if run_status in {"done", "running", "已完成"} else ("待确认" if run_status != "-" else "无记录"),
            "失败原因": "-" if run_status in {"done", "running", "已完成"} else f"最近状态：{run_status}",
            "备用方案": "手动点击刷新并生成今日推荐",
        },
    ]
    return pd.DataFrame(rows)


def p0_readiness_frame(
    recommendations: pd.DataFrame,
    outcomes: pd.DataFrame,
    positions: pd.DataFrame,
    plan_snapshots: pd.DataFrame,
    alert_rules: pd.DataFrame,
    alert_events: pd.DataFrame,
    data_health: pd.DataFrame,
) -> pd.DataFrame:
    credibility = credibility_summary(recommendations, outcomes)
    active_rules = (
        int(pd.to_numeric(alert_rules.get("enabled"), errors="coerce").fillna(0).sum())
        if not alert_rules.empty and "enabled" in alert_rules
        else 0
    )
    today_text = date.today().strftime("%Y-%m-%d")
    today_plans = 0
    if not plan_snapshots.empty and "trade_date" in plan_snapshots:
        today_plans = int((plan_snapshots["trade_date"].astype(str) == today_text).sum())
    unhealthy = 0
    if not data_health.empty and "状态" in data_health:
        unhealthy = int((~data_health["状态"].astype(str).isin(["正常"])).sum())
    rows = [
        {
            "P0模块": "推荐可信度评分",
            "当前状态": "已形成" if credibility["high_trust_count"] else "样本积累中",
            "核心指标": f"高可信{credibility['high_trust_count']}只；覆盖率{_pct(credibility['coverage_rate'])}",
            "下一步动作": "继续按策略、行业、周期沉淀胜率和回撤。",
        },
        {
            "P0模块": "数据源稳定性",
            "当前状态": "正常" if unhealthy == 0 else "需关注",
            "核心指标": f"{len(data_health) - unhealthy}/{len(data_health)} 个模块正常",
            "下一步动作": "异常时显示失败原因、备用源和最近可用数据时间。",
        },
        {
            "P0模块": "持仓回本中心",
            "当前状态": "已沉淀" if len(positions) else "待录入",
            "核心指标": f"持仓{len(positions)}只；今日方案{today_plans}条",
            "下一步动作": "每日自动生成低吸区、高抛区、止损区和回本路径。",
        },
        {
            "P0模块": "预警中心",
            "当前状态": "已启用" if active_rules else "待启用",
            "核心指标": f"启用规则{active_rules}条；最近事件{len(alert_events)}条",
            "下一步动作": "先稳定网页提醒，再扩展企业微信、短信、邮件。",
        },
        {
            "P0模块": "风险合规",
            "当前状态": "已约束",
            "核心指标": "使用概率、条件、风险、失效点表达",
            "下一步动作": "销售页和推荐页继续避免确定性收益承诺。",
        },
    ]
    return pd.DataFrame(rows)


def compliance_text() -> list[str]:
    return [
        "推荐结果只表达概率候选，不表达确定性结果。",
        "每只股票必须同时展示买点区间、止损位、目标位和失效条件。",
        "历史胜率、目标触达率和最大回撤必须说明样本数量，不能脱离样本谈结论。",
        "持仓做T方案只提供路径规划，最终仍需用户结合自身风险承受能力决策。",
        "外部宣传建议使用：高概率候选、条件触发、风险控制、历史复盘、失效点。",
    ]
