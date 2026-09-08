from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd


STRATEGY_TYPES = [
    "超短情绪龙头",
    "主线趋势股",
    "低吸回本股",
    "稳健分红股",
    "资金异动股",
    "机构趋势股",
    "高风险博弈股",
]

ALERT_TYPES = [
    "到达买点",
    "跌破止损",
    "主力资金连续流入",
    "板块主线升温",
    "龙头切换",
    "情绪退潮",
]

CONDITION_LABELS = [
    "主力资金连续流入",
    "技术评分较高",
    "处于买点附近",
    "低估值正常区间",
    "现金流良好",
    "分红稳定",
    "市场主线方向",
    "高可信度推荐",
]


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _pct(value: Any) -> str:
    value = _num(value)
    return f"{value:.2%}"


def build_credibility_metrics(outcomes: pd.DataFrame) -> pd.DataFrame:
    if outcomes.empty:
        return pd.DataFrame()
    frame = outcomes.copy()
    for column in (
        "t1_return",
        "t3_return",
        "t5_return",
        "t20_return",
        "max_drawdown_20",
        "stop_hit",
        "target1_hit",
    ):
        if column not in frame.columns:
            frame[column] = None
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    rows: list[dict[str, Any]] = []
    grouped = frame.groupby(["symbol", "horizon"], dropna=False)
    for (symbol, horizon), data in grouped:
        tracked = data[pd.to_numeric(data["available_days"], errors="coerce") > 0]
        t5 = tracked["t5_return"].dropna()
        t20 = tracked["t20_return"].dropna()
        stop_rate = float(tracked["stop_hit"].mean()) if not tracked.empty else None
        target_rate = float(tracked["target1_hit"].mean()) if not tracked.empty else None
        win_basis = t5 if not t5.empty else tracked["t1_return"].dropna()
        win_rate = float((win_basis > 0).mean()) if not win_basis.empty else None
        max_drawdown = float(tracked["max_drawdown_20"].min()) if not tracked.empty else None
        score = 50.0
        if win_rate is not None:
            score += (win_rate - 0.5) * 60
        if target_rate is not None:
            score += (target_rate - 0.35) * 30
        if stop_rate is not None:
            score -= stop_rate * 25
        if max_drawdown is not None:
            score += max(max_drawdown, -0.25) * 60
        sample_count = int(len(tracked))
        score += min(sample_count, 8) * 1.5
        score = max(0.0, min(100.0, score))
        rows.append(
            {
                "symbol": str(symbol),
                "horizon": str(horizon),
                "credibility_sample_count": sample_count,
                "history_win_rate": win_rate,
                "avg_t1_return": tracked["t1_return"].mean() if not tracked.empty else None,
                "avg_t3_return": tracked["t3_return"].mean() if not tracked.empty else None,
                "avg_t5_return": tracked["t5_return"].mean() if not tracked.empty else None,
                "avg_t20_return": tracked["t20_return"].mean() if not tracked.empty else None,
                "worst_max_drawdown": max_drawdown,
                "stop_trigger_rate": stop_rate,
                "target_touch_rate": target_rate,
                "credibility_score": score,
                "high_win_repeat": bool(sample_count >= 3 and win_rate is not None and win_rate >= 0.6),
            }
        )
    return pd.DataFrame(rows)


def classify_strategy(row: pd.Series | dict[str, Any], market_stage: str = "") -> str:
    item = dict(row)
    horizon = str(item.get("horizon", ""))
    score = _num(item.get("score"))
    base_score = _num(item.get("base_score"))
    flow3 = _num(item.get("net_inflow_3d"))
    flow5 = _num(item.get("net_inflow_5d"))
    flow10 = _num(item.get("net_inflow_10d"))
    dividend = _num(item.get("dividend_year_ratio"))
    cashflow = _num(item.get("operating_cash_flow"))
    position_pct = _num(item.get("position_pct"))
    priority = str(item.get("priority", ""))
    if horizon == "short" and ("升温" in market_stage or score >= 90) and flow3 > 0:
        return "超短情绪龙头"
    if flow3 > 0 and flow5 > 0 and flow10 > 0 and score >= 80:
        return "资金异动股"
    if horizon == "long" and dividend >= 0.5 and cashflow > 0:
        return "稳健分红股"
    if horizon in {"mid", "long"} and base_score >= 78 and position_pct >= 0.18:
        return "机构趋势股"
    if horizon == "mid" and score >= 75:
        return "主线趋势股"
    if score < 72 or priority == "观察":
        return "低吸回本股"
    if horizon == "short" and position_pct <= 0.12:
        return "高风险博弈股"
    return "主线趋势股"


def enrich_recommendations_for_product(
    recommendations: pd.DataFrame,
    outcomes: pd.DataFrame,
    sentiment_history: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if recommendations.empty:
        return recommendations
    frame = recommendations.copy()
    metrics = build_credibility_metrics(outcomes)
    if not metrics.empty:
        frame = frame.merge(metrics, on=["symbol", "horizon"], how="left")
    defaults = {
        "credibility_sample_count": 0,
        "history_win_rate": None,
        "avg_t1_return": None,
        "avg_t3_return": None,
        "avg_t5_return": None,
        "avg_t20_return": None,
        "worst_max_drawdown": None,
        "stop_trigger_rate": None,
        "target_touch_rate": None,
        "credibility_score": 50.0,
        "high_win_repeat": False,
    }
    for column, value in defaults.items():
        if column not in frame.columns:
            frame[column] = value
        else:
            frame[column] = frame[column].fillna(value)
    market_stage = ""
    if sentiment_history is not None and not sentiment_history.empty and "emotion_stage" in sentiment_history.columns:
        market_stage = str(sentiment_history.iloc[0].get("emotion_stage") or "")
    frame["strategy_type"] = frame.apply(lambda row: classify_strategy(row, market_stage), axis=1)
    frame["credibility_label"] = frame["credibility_score"].map(
        lambda value: "高可信" if _num(value) >= 72 else ("可跟踪" if _num(value) >= 55 else "样本积累")
    )
    return frame


def research_brief(
    row: pd.Series | dict[str, Any],
    global_summary: dict[str, Any] | None = None,
    alternatives: list[str] | None = None,
) -> dict[str, str]:
    item = dict(row)
    score = _num(item.get("score"))
    strategy = str(item.get("strategy_type") or classify_strategy(item))
    industry = str(item.get("industry") or "所属行业待补充")
    topics = str(item.get("topic_text") or "")
    flow_text = (
        f"3/5/10日资金分别约{_num(item.get('net_inflow_3d')) / 100000000:.1f}亿、"
        f"{_num(item.get('net_inflow_5d')) / 100000000:.1f}亿、"
        f"{_num(item.get('net_inflow_10d')) / 100000000:.1f}亿"
    )
    risk_label = str((global_summary or {}).get("label") or "中性")
    risk = "若跌破止损位或板块资金转弱，本次逻辑失效；历史胜率样本不足时要降低仓位参考权重。"
    if _num(item.get("stop_trigger_rate")) > 0.35:
        risk = "历史跟踪中止损触发偏高，适合轻仓观察，不适合重仓追涨。"
    chase = "更适合等回踩买点区间低吸，放量突破目标一后再评估是否追随。"
    if strategy in {"超短情绪龙头", "高风险博弈股"}:
        chase = "只适合能接受高波动的短线打法，必须把止损位和仓位上限放在第一位。"
    elif strategy in {"稳健分红股", "机构趋势股"}:
        chase = "更适合分批跟踪趋势和回撤承接，不建议用情绪追涨方式处理。"
    alternative_text = "、".join(alternatives or [])
    if not alternative_text:
        alternative_text = f"可在“行业题材/市场地图”里找同属{industry}且评分接近的标的"
    return {
        "入选逻辑": f"{industry}方向中综合评分{score:.1f}，归类为“{strategy}”；{flow_text}，并结合K线、成交量和风险位过滤。",
        "最大风险": risk,
        "同板块替代": f"{alternative_text}，避免单一股票判断失误。",
        "操作倾向": chase,
        "外部环境": f"全球风险偏好当前为“{risk_label}”。政策、国际市场和行业新闻只作为概率修正，不构成必涨判断。",
        "合规提示": "系统输出的是概率、条件、风险和失效点，不承诺收益，也不替代个人投资决策。",
    }


def build_ai_research_table(
    recommendations: pd.DataFrame,
    global_summary: dict[str, Any] | None = None,
    limit: int = 30,
) -> pd.DataFrame:
    """Create a paid-product research explanation table for every recommendation."""
    if recommendations.empty:
        return pd.DataFrame()
    frame = recommendations.copy()
    if "score" not in frame.columns:
        frame["score"] = 0
    frame["_score"] = pd.to_numeric(frame["score"], errors="coerce").fillna(0)
    rows: list[dict[str, Any]] = []
    for _, row in frame.sort_values("_score", ascending=False).head(limit).iterrows():
        industry = str(row.get("industry") or "")
        peers = frame[
            (frame.get("industry", pd.Series(index=frame.index, dtype=object)).astype(str) == industry)
            & (frame.get("symbol", pd.Series(index=frame.index, dtype=object)).astype(str) != str(row.get("symbol")))
        ].sort_values("_score", ascending=False)
        alternatives = [
            f"{peer.get('name')}({peer.get('symbol')})"
            for _, peer in peers.head(3).iterrows()
            if peer.get("name") and peer.get("symbol")
        ]
        brief = research_brief(row, global_summary=global_summary, alternatives=alternatives)
        rows.append(
            {
                "代码": row.get("symbol", ""),
                "名称": row.get("name", ""),
                "行业": industry or "待补充",
                "题材": row.get("topic_text", ""),
                "策略打法": row.get("strategy_type", classify_strategy(row)),
                "综合评分": round(_num(row.get("score")), 1),
                "可信度": row.get("credibility_label", "样本积累"),
                "为什么入选": brief["入选逻辑"],
                "最大风险": brief["最大风险"],
                "同板块替代股": brief["同板块替代"],
                "操作倾向": brief["操作倾向"],
                "失效点": "跌破止损位、板块资金转弱、市场情绪退潮或历史样本风险升高时，本轮逻辑失效。",
            }
        )
    return pd.DataFrame(rows)


def build_market_map(
    recommendations: pd.DataFrame,
    capital_hotspots: pd.DataFrame,
    limit_ladder: pd.DataFrame,
    history: pd.DataFrame | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    recs = recommendations.copy()
    if not recs.empty and "industry" in recs.columns:
        for industry, data in recs.groupby("industry", dropna=False):
            if not industry:
                continue
            leaders = data.sort_values("score", ascending=False).head(3)
            rows.append(
                {
                    "类型": "行业",
                    "方向": str(industry),
                    "主线持续": int(data["trade_date"].nunique()) if "trade_date" in data.columns else 1,
                    "龙头股": "、".join(leaders["name"].astype(str).head(1)),
                    "跟风股": "、".join(leaders["name"].astype(str).iloc[1:3]),
                    "推荐数量": int(len(data)),
                    "平均评分": float(pd.to_numeric(data["score"], errors="coerce").mean()),
                    "板块资金净流入": None,
                    "涨停数量": 0,
                    "阶段": "主升" if float(pd.to_numeric(data["score"], errors="coerce").mean()) >= 82 else "观察",
                }
            )
    combined_history = pd.DataFrame()
    if history is not None and not history.empty:
        combined_history = pd.concat([history, capital_hotspots], ignore_index=True)
    elif not capital_hotspots.empty:
        combined_history = capital_hotspots.copy()

    sustain_map: dict[tuple[str, str], int] = {}
    if not combined_history.empty and {"category", "name", "trade_date"}.issubset(combined_history.columns):
        latest_dates = sorted(combined_history["trade_date"].dropna().astype(str).unique(), reverse=True)
        recent_dates = set(latest_dates[:20])
        recent = combined_history[combined_history["trade_date"].astype(str).isin(recent_dates)].copy()
        if "net_inflow_yi" in recent.columns:
            recent["_strong"] = pd.to_numeric(recent["net_inflow_yi"], errors="coerce").fillna(0) > 0
            sustain_map = {
                (str(category), str(name)): int(data["_strong"].sum())
                for (category, name), data in recent.groupby(["category", "name"], dropna=False)
            }

    if not capital_hotspots.empty:
        latest = capital_hotspots[capital_hotspots["period"].astype(str) == "daily"].copy()
        if latest.empty:
            latest = capital_hotspots.copy()
        for _, item in latest.head(30).iterrows():
            direction = str(item.get("name") or "")
            rows.append(
                {
                    "类型": str(item.get("category") or "资金"),
                    "方向": direction,
                    "主线持续": sustain_map.get((str(item.get("category") or "资金"), direction), None),
                    "龙头股": str(item.get("leader") or ""),
                    "跟风股": "",
                    "推荐数量": 0,
                    "平均评分": None,
                    "板块资金净流入": _num(item.get("net_inflow_yi")),
                    "涨停数量": 0,
                    "阶段": "高潮" if _num(item.get("net_inflow_yi")) >= 10 else ("启动" if _num(item.get("net_inflow_yi")) > 0 else "退潮"),
                }
            )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    if not limit_ladder.empty and "industry" in limit_ladder.columns:
        limit_counts = limit_ladder.groupby("industry").size().to_dict()
        frame["涨停数量"] = frame.apply(
            lambda row: int(limit_counts.get(row["方向"], row.get("涨停数量") or 0)),
            axis=1,
        )
    frame = frame.sort_values(["板块资金净流入", "平均评分", "推荐数量"], ascending=False, na_position="last")
    return frame.drop_duplicates(["类型", "方向"]).head(40).reset_index(drop=True)


def p1_paid_value_frame(
    recommendations: pd.DataFrame,
    capital_hotspots: pd.DataFrame,
    market_map: pd.DataFrame,
    outcomes: pd.DataFrame,
    positions: pd.DataFrame,
    alert_rules: pd.DataFrame,
) -> pd.DataFrame:
    """Summarize whether P1 paid-value modules are usable."""
    ai_ready = not recommendations.empty
    map_ready = not market_map.empty
    backtest_ready = not outcomes.empty
    member_ready = True
    position_ready = not positions.empty
    alert_ready = not alert_rules.empty
    rows = [
        {
            "P1模块": "AI投研解释",
            "状态": "已上线" if ai_ready else "待数据",
            "付费价值": "解释为什么入选、最大风险、同板块替代股和失效点",
            "当前数据": f"{len(recommendations)}只推荐",
            "下一步": "接入新闻和公告摘要后，增强政策与事件解释",
        },
        {
            "P1模块": "板块地图",
            "状态": "已上线" if map_ready else "待数据",
            "付费价值": "看最强行业、题材、主线持续天数、龙头/跟风和退潮方向",
            "当前数据": f"{len(market_map)}个方向",
            "下一步": "积累更多历史热点，提升主线持续判断",
        },
        {
            "P1模块": "策略回测中心",
            "状态": "已上线" if backtest_ready else "待复盘",
            "付费价值": "验证推荐池、单股买卖点、做T、龙头和情绪周期打法",
            "当前数据": f"{len(outcomes)}条复盘样本",
            "下一步": "增加参数组合保存和用户自定义策略",
        },
        {
            "P1模块": "会员体系",
            "状态": "已上线" if member_ready else "待配置",
            "付费价值": "查看到期提醒、续费记录和用户查询记录",
            "当前数据": "后台管理",
            "下一步": "接入支付回调后自动生成续费订单",
        },
        {
            "P1模块": "持仓与预警粘性",
            "状态": "已上线" if position_ready and alert_ready else "部分上线",
            "付费价值": "用户保存持仓后每天回来看做T、止损、回本路径和预警",
            "当前数据": f"持仓{len(positions)}只 / 预警{len(alert_rules)}条",
            "下一步": "增加微信/短信提醒通道",
        },
    ]
    return pd.DataFrame(rows)


def strategy_backtest_matrix(
    recommendations: pd.DataFrame,
    outcomes: pd.DataFrame,
    positions: pd.DataFrame,
    sentiment_history: pd.DataFrame,
) -> pd.DataFrame:
    """Produce a concise multi-strategy backtest status board."""
    rows: list[dict[str, Any]] = []

    def mean_col(frame: pd.DataFrame, column: str) -> float | None:
        if frame.empty or column not in frame.columns:
            return None
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        return float(values.mean()) if not values.empty else None

    def min_col(frame: pd.DataFrame, column: str) -> float | None:
        if frame.empty or column not in frame.columns:
            return None
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        return float(values.min()) if not values.empty else None

    sample = len(outcomes)
    rows.append(
        {
            "回测类型": "推荐池历史回测",
            "样本数量": sample,
            "核心指标": f"T+5均值 {_pct(mean_col(outcomes, 't5_return'))}，T+20均值 {_pct(mean_col(outcomes, 't20_return'))}",
            "最大回撤": _pct(min_col(outcomes, "max_drawdown_20")),
            "当前结论": "样本充足时可作为推荐可信度依据" if sample else "待刷新推荐复盘",
        }
    )
    rows.append(
        {
            "回测类型": "单股买卖点回测",
            "样本数量": len(recommendations),
            "核心指标": "对用户输入股票运行均线/MACD/风控位参数对比",
            "最大回撤": "按单股回测结果显示",
            "当前结论": "适合验证单股买卖点是否适配当前走势",
        }
    )
    rows.append(
        {
            "回测类型": "做T方案模拟",
            "样本数量": len(positions),
            "核心指标": "按持仓成本、可卖底仓、可用资金和预计滑点估算回本路径",
            "最大回撤": "按持仓止损区控制",
            "当前结论": "有持仓记录后可形成每日复盘闭环" if len(positions) else "待用户保存持仓",
        }
    )
    leader_sample = int((recommendations.get("strategy_type", pd.Series(dtype=str)).astype(str).str.contains("龙头").sum())) if not recommendations.empty else 0
    rows.append(
        {
            "回测类型": "龙头策略回测",
            "样本数量": leader_sample,
            "核心指标": "观察龙一/龙二/龙三相对强弱、切换次数和回撤",
            "最大回撤": "以龙头曲线最大回撤为准",
            "当前结论": "适合短线情绪用户，必须配合止损和情绪周期",
        }
    )
    stage_count = len(sentiment_history)
    latest_stage = str(sentiment_history.iloc[0].get("emotion_stage")) if stage_count and "emotion_stage" in sentiment_history else "待记录"
    rows.append(
        {
            "回测类型": "情绪周期回测",
            "样本数量": stage_count,
            "核心指标": f"当前阶段：{latest_stage}",
            "最大回撤": "退潮期降低仓位",
            "当前结论": "用于判断追涨、低吸或空仓观察的市场环境",
        }
    )
    return pd.DataFrame(rows)


def membership_value_metrics(
    accounts: pd.DataFrame,
    periods: pd.DataFrame,
    queries: pd.DataFrame,
    usage: pd.DataFrame,
) -> dict[str, Any]:
    """Summarize subscription operations for admin dashboards."""
    metrics: dict[str, Any] = {
        "account_count": len(accounts),
        "expiring_7d": 0,
        "renewal_count": len(periods),
        "query_count": len(queries),
        "active_users_7d": 0,
    }
    if not accounts.empty and "remaining_days" in accounts.columns:
        days = pd.to_numeric(accounts["remaining_days"], errors="coerce")
        metrics["expiring_7d"] = int(((days >= 0) & (days <= 7)).sum())
    if not usage.empty and "usage_date" in usage.columns:
        usage_dates = pd.to_datetime(usage["usage_date"], errors="coerce")
        cutoff = pd.Timestamp(date.today()) - pd.Timedelta(days=7)
        if "user_id" in usage.columns:
            metrics["active_users_7d"] = int(usage.loc[usage_dates >= cutoff, "user_id"].nunique())
    return metrics



def evaluate_alert_rules(
    rules: pd.DataFrame,
    spot: pd.DataFrame,
    recommendations: pd.DataFrame,
    capital_hotspots: pd.DataFrame,
    sentiment_history: pd.DataFrame,
    owner_key: str = "local",
) -> list[dict[str, Any]]:
    if rules.empty:
        return []
    events: list[dict[str, Any]] = []
    recs_by_symbol = {}
    if not recommendations.empty:
        recs_by_symbol = {
            str(row["symbol"]).upper(): row
            for _, row in recommendations.iterrows()
            if row.get("symbol")
        }
    sentiment_stage = ""
    broken_rate = 0.0
    if not sentiment_history.empty:
        latest = sentiment_history.iloc[0]
        sentiment_stage = str(latest.get("emotion_stage") or "")
        broken_rate = _num(latest.get("broken_rate"))
    hot_names = set()
    if not capital_hotspots.empty:
        hot = capital_hotspots[capital_hotspots["period"].astype(str).isin(["daily", "weekly"])]
        hot_names = set(hot.loc[pd.to_numeric(hot["net_inflow_yi"], errors="coerce") > 0, "name"].astype(str))
    spot_by_code = {}
    if not spot.empty and "code" in spot.columns:
        spot_by_code = {str(row["code"]): row for _, row in spot.iterrows()}
    for _, rule in rules.iterrows():
        alert_type = str(rule.get("alert_type") or "")
        symbol = str(rule.get("symbol") or "").upper()
        rec = recs_by_symbol.get(symbol)
        observed = None
        message = ""
        severity = "提示"
        if alert_type in {"到达买点", "跌破止损"} and symbol:
            code = symbol[-6:]
            spot_row = spot_by_code.get(code)
            observed = (
                _num(spot_row.get("price"))
                if spot_row is not None
                else (_num(rec.get("close")) if rec is not None else 0.0)
            )
            if alert_type == "到达买点":
                if rec is not None:
                    low = _num(rec.get("buy_zone_low"))
                    high = _num(rec.get("buy_zone_high"))
                    matched = low > 0 and low <= observed <= high
                    target_text = f"买点区间{low:.2f}-{high:.2f}"
                    display_name = rec.get("name", "")
                else:
                    threshold = _num(rule.get("threshold_value"))
                    comparator = str(rule.get("comparator") or "进入区间")
                    low = threshold * 0.995
                    high = threshold * 1.005
                    matched = (
                        threshold > 0
                        and (
                            (comparator == ">=" and observed >= threshold)
                            or (comparator == "<=" and observed <= threshold)
                            or (comparator == "进入区间" and low <= observed <= high)
                        )
                    )
                    target_text = f"自定义价格{threshold:.2f}"
                    display_name = rule.get("name", "")
                if matched:
                    message = f"{symbol} {display_name} 当前价{observed:.2f}到达{target_text}。"
                    severity = "机会"
            else:
                stop = _num(rec.get("stop_loss")) if rec is not None else _num(rule.get("threshold_value"))
                display_name = rec.get("name", "") if rec is not None else rule.get("name", "")
                if stop > 0 and observed > 0 and observed <= stop:
                    message = f"{symbol} {display_name} 当前价{observed:.2f}跌破止损位{stop:.2f}。"
                    severity = "风险"
        elif alert_type == "主力资金连续流入" and rec is not None:
            if _num(rec.get("net_inflow_3d")) > 0 and _num(rec.get("net_inflow_5d")) > 0 and _num(rec.get("net_inflow_10d")) > 0:
                observed = _num(rec.get("net_inflow_10d")) / 100000000
                message = f"{symbol} {rec.get('name', '')} 3/5/10日资金均为正，10日净流入约{observed:.1f}亿。"
                severity = "机会"
        elif alert_type == "板块主线升温":
            if hot_names:
                message = "资金热点中出现升温方向：" + "、".join(list(hot_names)[:5])
                severity = "机会"
        elif alert_type == "龙头切换":
            leaders = recommendations.sort_values("score", ascending=False).head(3) if not recommendations.empty else pd.DataFrame()
            if not leaders.empty:
                message = "当前评分靠前龙头候选：" + "、".join(leaders["name"].astype(str).tolist())
                severity = "提示"
        elif alert_type == "情绪退潮":
            if "退潮" in sentiment_stage or broken_rate >= 0.35:
                observed = broken_rate
                message = f"市场情绪疑似退潮，阶段为{sentiment_stage or '未标记'}，炸板率约{broken_rate:.1%}。"
                severity = "风险"
        if message:
            events.append(
                {
                    "owner_key": owner_key,
                    "rule_id": int(rule.get("id")) if pd.notna(rule.get("id")) else None,
                    "trade_date": date.today().strftime("%Y-%m-%d"),
                    "symbol": symbol,
                    "name": rule.get("name", ""),
                    "alert_type": alert_type,
                    "severity": severity,
                    "observed_value": observed,
                    "message": message,
                }
            )
    return events


def credibility_display(row: pd.Series | dict[str, Any]) -> dict[str, str]:
    item = dict(row)
    sample = int(_num(item.get("credibility_sample_count")))
    if sample <= 0:
        return {
            "历史胜率": "积累中",
            "T+1/T+3/T+5/T+20": "-",
            "最大回撤": "-",
            "止损率": "-",
            "目标率": "-",
            "可信度": str(item.get("credibility_label") or "样本积累"),
        }
    returns = [
        _pct(item.get("avg_t1_return")),
        _pct(item.get("avg_t3_return")),
        _pct(item.get("avg_t5_return")),
        _pct(item.get("avg_t20_return")),
    ]
    return {
        "历史胜率": _pct(item.get("history_win_rate")),
        "T+1/T+3/T+5/T+20": " / ".join(returns),
        "最大回撤": _pct(item.get("worst_max_drawdown")),
        "止损率": _pct(item.get("stop_trigger_rate")),
        "目标率": _pct(item.get("target_touch_rate")),
        "可信度": f"{item.get('credibility_label', '可跟踪')} {int(_num(item.get('credibility_score')))}",
    }


def credibility_explanation(row: pd.Series | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    sample = int(_num(item.get("credibility_sample_count")))
    score = int(_num(item.get("credibility_score")))
    win_rate = _num(item.get("history_win_rate"))
    stop_rate = _num(item.get("stop_trigger_rate"))
    target_rate = _num(item.get("target_touch_rate"))
    drawdown = _num(item.get("worst_max_drawdown"))
    repeat_hit = bool(item.get("high_win_repeat"))

    if sample <= 0:
        return {
            "risk_label": "样本积累",
            "action": "先观察",
            "summary": "这只股票暂无足够历史推荐样本，当前更适合小仓观察或等待下一轮复盘验证。",
            "reasons": ["历史胜率样本仍在积累", "需要结合买点、止损位和市场温度一起判断"],
        }

    reasons: list[str] = []
    if repeat_hit:
        reasons.append("多次被高胜率模型重复命中，说明策略一致性较好")
    if win_rate >= 0.6:
        reasons.append(f"历史推荐胜率约{win_rate:.0%}，高于普通观察池")
    elif win_rate < 0.45:
        reasons.append(f"历史推荐胜率约{win_rate:.0%}，需要降低仓位或等待确认")
    if target_rate >= 0.45:
        reasons.append(f"目标价触达率约{target_rate:.0%}，上涨兑现能力较好")
    if stop_rate >= 0.35:
        reasons.append(f"止损触发率约{stop_rate:.0%}，风险波动偏高")
    if drawdown <= -0.12:
        reasons.append(f"历史最大回撤约{drawdown:.0%}，需要严格止损")
    if not reasons:
        reasons.append("历史表现中性，适合按买点和仓位规则执行")

    if score >= 78 and win_rate >= 0.55 and stop_rate <= 0.28:
        risk_label = "高可信"
        action = "重点跟踪"
    elif score >= 60 and stop_rate <= 0.4:
        risk_label = "可跟踪"
        action = "轻仓验证"
    else:
        risk_label = "高波动"
        action = "谨慎观察"

    summary = f"{risk_label}：建议{action}。参考的是历史复盘、收益分布、止损触发和目标触达，并不代表未来必涨。"
    return {"risk_label": risk_label, "action": action, "summary": summary, "reasons": reasons}


def filter_by_conditions(
    recommendations: pd.DataFrame,
    conditions: list[str],
    min_score: float = 70,
    max_risk_position: float = 0.35,
    keyword: str = "",
) -> pd.DataFrame:
    if recommendations.empty:
        return recommendations
    frame = recommendations.copy()
    mask = pd.Series(True, index=frame.index)
    score = pd.to_numeric(frame.get("score"), errors="coerce").fillna(0)
    base_score = pd.to_numeric(frame.get("base_score"), errors="coerce").fillna(0)
    position_pct = pd.to_numeric(frame.get("position_pct"), errors="coerce").fillna(0)
    close = pd.to_numeric(frame.get("close"), errors="coerce").fillna(0)
    buy_low = pd.to_numeric(frame.get("buy_zone_low"), errors="coerce").fillna(0)
    buy_high = pd.to_numeric(frame.get("buy_zone_high"), errors="coerce").fillna(0)
    if "主力资金连续流入" in conditions:
        mask &= (
            pd.to_numeric(frame.get("net_inflow_3d"), errors="coerce").fillna(0) > 0
        ) & (
            pd.to_numeric(frame.get("net_inflow_5d"), errors="coerce").fillna(0) > 0
        ) & (
            pd.to_numeric(frame.get("net_inflow_10d"), errors="coerce").fillna(0) > 0
        )
    if "技术评分较高" in conditions:
        mask &= base_score >= min_score
    if "处于买点附近" in conditions:
        mask &= close.between(buy_low * 0.98, buy_high * 1.02)
    if "低估值正常区间" in conditions and "valuation_normal" in frame.columns:
        mask &= pd.to_numeric(frame["valuation_normal"], errors="coerce").fillna(0) > 0
    if "现金流良好" in conditions and "cashflow_good" in frame.columns:
        mask &= pd.to_numeric(frame["cashflow_good"], errors="coerce").fillna(0) > 0
    if "分红稳定" in conditions and "dividend_stable" in frame.columns:
        mask &= pd.to_numeric(frame["dividend_stable"], errors="coerce").fillna(0) > 0
    if "市场主线方向" in conditions:
        mask &= frame.get("priority", "").astype(str).isin(["重点", "关注"])
    if "高可信度推荐" in conditions:
        mask &= pd.to_numeric(frame.get("credibility_score"), errors="coerce").fillna(50) >= 70
    mask &= score >= min_score
    mask &= position_pct <= max_risk_position
    if keyword.strip():
        text = pd.Series("", index=frame.index, dtype="object")
        for column in ["symbol", "name", "industry", "topic_text", "strategy_type"]:
            if column in frame.columns:
                text = text + " " + frame[column].fillna("").astype(str)
        mask &= text.str.contains(keyword.strip(), case=False, regex=False)
    return frame[mask].sort_values(["score", "credibility_score"], ascending=False).reset_index(drop=True)


def build_paper_trade_candidates(
    recommendations: pd.DataFrame,
    outcomes: pd.DataFrame,
    account_id: int,
    owner_key: str,
    cash_per_trade: float = 10_000.0,
    max_count: int = 10,
) -> list[dict[str, Any]]:
    if recommendations.empty:
        return []
    outcome_map = {}
    if not outcomes.empty:
        for _, row in outcomes.iterrows():
            outcome_map[int(row.get("recommendation_id") or 0)] = row
    records: list[dict[str, Any]] = []
    for _, row in recommendations.sort_values("score", ascending=False).head(max_count).iterrows():
        price = _num(row.get("buy_zone_high") or row.get("close"))
        if price <= 0:
            continue
        shares = int(cash_per_trade // (price * 100)) * 100
        if shares <= 0:
            continue
        outcome = outcome_map.get(int(row.get("id") or 0))
        return_pct = None
        close_price = None
        status = "持仓中"
        reason = "按推荐买点模拟买入"
        if outcome is not None:
            return_pct = _num(outcome.get("t5_return"), default=None) if pd.notna(outcome.get("t5_return")) else None
            if return_pct is None and pd.notna(outcome.get("t1_return")):
                return_pct = _num(outcome.get("t1_return"))
            if return_pct is not None:
                close_price = price * (1 + return_pct)
                status = "已复盘"
                reason = "按推荐后收益自动复盘"
        amount = price * shares
        records.append(
            {
                "account_id": account_id,
                "owner_key": owner_key,
                "trade_date": str(row.get("trade_date") or date.today().strftime("%Y-%m-%d")),
                "recommendation_id": int(row.get("id") or 0) or None,
                "symbol": row.get("symbol"),
                "name": row.get("name"),
                "horizon": row.get("horizon"),
                "strategy_type": row.get("strategy_type", ""),
                "side": "买入",
                "price": price,
                "shares": shares,
                "amount": amount,
                "fee": max(5.0, amount * 0.0003),
                "close_price": close_price,
                "return_pct": return_pct,
                "status": status,
                "reason": reason,
            }
        )
    return records


def paper_trade_summary(trades: pd.DataFrame, initial_cash: float) -> tuple[pd.DataFrame, dict[str, Any]]:
    if trades.empty:
        return pd.DataFrame(), {
            "total_return": 0.0,
            "win_rate": None,
            "max_drawdown": 0.0,
            "trade_count": 0,
        }
    frame = trades.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["pnl"] = pd.to_numeric(frame["amount"], errors="coerce").fillna(0) * pd.to_numeric(
        frame["return_pct"], errors="coerce"
    ).fillna(0) - pd.to_numeric(frame["fee"], errors="coerce").fillna(0)
    daily = frame.groupby("trade_date", dropna=True)["pnl"].sum().sort_index().reset_index()
    daily["equity"] = float(initial_cash) + daily["pnl"].cumsum()
    peak = daily["equity"].cummax().replace(0, pd.NA)
    daily["drawdown"] = daily["equity"] / peak - 1
    closed = frame[pd.to_numeric(frame["return_pct"], errors="coerce").notna()]
    summary = {
        "total_return": float(daily["equity"].iloc[-1] / float(initial_cash) - 1) if not daily.empty else 0.0,
        "win_rate": float((closed["pnl"] > 0).mean()) if not closed.empty else None,
        "max_drawdown": float(daily["drawdown"].min()) if not daily.empty else 0.0,
        "trade_count": int(len(frame)),
    }
    return daily, summary


def build_daily_review_report(
    recommendations: pd.DataFrame,
    outcomes: pd.DataFrame,
    capital_hotspots: pd.DataFrame,
    breadth: dict[str, Any],
    global_summary: dict[str, Any],
    sentiment_history: pd.DataFrame,
) -> tuple[str, dict[str, Any]]:
    today = date.today().strftime("%Y-%m-%d")
    total = len(recommendations)
    focus = int((recommendations.get("priority", pd.Series(dtype=str)).astype(str) == "重点").sum()) if not recommendations.empty else 0
    top_names = "、".join(recommendations.sort_values("score", ascending=False)["name"].astype(str).head(5)) if not recommendations.empty else "暂无"
    hot = capital_hotspots[capital_hotspots["period"].astype(str) == "daily"].copy() if not capital_hotspots.empty else pd.DataFrame()
    hot_text = "、".join(hot.sort_values("rank").head(5)["name"].astype(str)) if not hot.empty else "暂无"
    win_rate = None
    if not outcomes.empty and "t1_return" in outcomes.columns:
        t1 = pd.to_numeric(outcomes["t1_return"], errors="coerce").dropna()
        if not t1.empty:
            win_rate = float((t1 > 0).mean())
    sentiment = ""
    if not sentiment_history.empty:
        sentiment = str(sentiment_history.iloc[0].get("emotion_stage") or "")
    metrics = {
        "recommendation_count": total,
        "focus_count": focus,
        "t1_win_rate": win_rate,
        "temperature": breadth.get("temperature"),
        "global_label": global_summary.get("label"),
        "sentiment_stage": sentiment,
    }
    lines = [
        f"# A股每日收盘复盘 {today}",
        "",
        f"## 今日市场",
        f"- 全市场股票：{int(breadth.get('total', 0) or 0):,}只，上涨{int(breadth.get('up', 0) or 0):,}只，下跌{int(breadth.get('down', 0) or 0):,}只。",
        f"- 市场温度：{breadth.get('temperature_label', '-')}{breadth.get('temperature', '')}，全球风险：{global_summary.get('label', '中性')}。",
        f"- 情绪周期：{sentiment or '暂无'}。",
        "",
        "## 今日推荐",
        f"- 推荐总数：{total}只，其中重点{focus}只。",
        f"- 评分靠前：{top_names}。",
        f"- 资金热点：{hot_text}。",
        "",
        "## 历史验证",
        f"- 推荐后T+1上涨率：{_pct(win_rate) if win_rate is not None else '样本积累中'}。",
        "- 复盘指标应同时看止损触发率、目标价触达率和最大回撤，不单看上涨率。",
        "",
        "## 明日观察",
        "- 若市场温度继续升高，可优先看主线趋势股和资金异动股。",
        "- 若情绪退潮或涨停炸板率升高，降低超短仓位，优先保护止损。",
        "- 所有结论只描述概率和条件，不承诺收益。",
    ]
    return "\n".join(lines), metrics
