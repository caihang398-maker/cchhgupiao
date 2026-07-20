from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import pandas as pd

from .indicators import add_indicators
from .strategy import SignalReport


@dataclass
class PositionPlan:
    cost_price: float
    shares: int
    current_price: float
    market_value: float
    cost_value: float
    unrealized_pnl: float
    unrealized_return: float
    break_even_gap: float
    position_concentration: float | None
    concentration_label: str
    sellable_shares: int
    core_shares: int
    t_shares: int
    t_mode: str
    feasibility_score: int
    feasibility_label: str
    break_even_spread_pct: float
    median_amplitude_20d: float
    opportunity_days_20d: int
    average_turnover_20d: float
    liquidity_label: str
    t_buy_low: float
    t_buy_high: float
    t_sell_low: float
    t_sell_high: float
    expected_spread_pct: float
    estimated_round_profit: float
    required_recovery_profit: float
    estimated_rounds_to_break_even: int | None
    cost_after_one_round: float
    cost_after_three_rounds: float
    recovery_price_after_one_round: float
    recovery_price_after_three_rounds: float
    trend_label: str
    market_label: str
    hotspot_label: str
    event_label: str
    event_headlines: list[str]
    invalidation_price: float
    risk_to_invalidation: float
    buy_ladder: list[dict[str, Any]]
    sell_ladder: list[dict[str, Any]]
    action_summary: str
    steps: list[str]
    risks: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _round_price(value: float) -> float:
    return round(max(0.01, value), 2)


def _board_lot(shares: int) -> int:
    return max(0, int(shares) // 100 * 100)


def _estimated_fees(
    sell_amount: float,
    buy_amount: float,
    commission_rate: float = 0.0003,
    slippage_rate: float = 0.001,
) -> float:
    sell_commission = max(5.0, sell_amount * commission_rate)
    buy_commission = max(5.0, buy_amount * commission_rate)
    stamp_tax = sell_amount * 0.0005
    slippage = (sell_amount + buy_amount) * slippage_rate
    return sell_commission + buy_commission + stamp_tax + slippage


def _price_ladder(low: float, high: float, shares: int, reverse: bool = False) -> list[dict[str, Any]]:
    if shares < 100:
        return []
    lots = shares // 100
    weights = [0.4, 0.35, 0.25]
    allocated = [0, 0, 0]
    remaining = lots
    for index, weight in enumerate(weights[:-1]):
        allocated[index] = min(remaining, max(0, round(lots * weight)))
        remaining -= allocated[index]
    allocated[-1] = remaining
    prices = [low, (low + high) / 2, high]
    if reverse:
        prices.reverse()
    return [
        {"price": _round_price(price), "shares": lot_count * 100}
        for price, lot_count in zip(prices, allocated)
        if lot_count > 0
    ]


def _hotspot_context(
    capital_hotspots: pd.DataFrame | None,
    industry: str = "",
    topics: list[str] | None = None,
) -> tuple[str, bool]:
    if capital_hotspots is None or capital_hotspots.empty:
        return "热点数据暂缺", False

    keywords = [industry.strip()] if industry.strip() else []
    keywords.extend(str(item).strip() for item in (topics or []) if str(item).strip())
    names = capital_hotspots.get("name", pd.Series(dtype=str)).fillna("").astype(str)
    matched = pd.Series(False, index=capital_hotspots.index)
    for keyword in keywords:
        matched |= names.str.contains(keyword, case=False, regex=False)

    selected = capital_hotspots.loc[matched] if matched.any() else capital_hotspots.head(5)
    inflow = pd.to_numeric(selected.get("net_inflow_yi"), errors="coerce").dropna()
    if inflow.empty:
        return "热点资金方向不明确", False
    average = float(inflow.mean())
    if matched.any() and average > 0:
        return f"所属行业/题材资金偏热（样本净流入均值 {average:.1f}亿元）", True
    if matched.any():
        return f"所属行业/题材资金偏弱（样本净流入均值 {average:.1f}亿元）", False
    return "未匹配到所属行业，参考全市场热门方向", average > 0


def _event_context(
    news: pd.DataFrame | None,
    stock_name: str,
    industry: str,
    topics: list[str] | None,
) -> tuple[str, list[str]]:
    if news is None or news.empty:
        return "政策与国际事件资讯暂缺，未纳入本轮判断", []

    risk_keywords = (
        "制裁", "关税", "冲突", "战争", "袭击", "风险", "下调", "调查",
        "处罚", "监管", "退市", "暴跌", "加息", "限制出口", "地缘",
    )
    support_keywords = (
        "支持", "利好", "补贴", "降息", "降准", "增持", "回购", "中标",
        "突破", "增长", "国务院", "央行", "证监会", "产业政策",
    )
    relevance = [stock_name.strip(), industry.strip()]
    relevance.extend(str(item).strip() for item in (topics or []) if str(item).strip())
    scored: list[tuple[int, str]] = []
    risk_hits = 0
    support_hits = 0
    for _, row in news.head(30).iterrows():
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        relevance_score = sum(2 for keyword in relevance if keyword and keyword in title)
        risk_score = sum(1 for keyword in risk_keywords if keyword in title)
        support_score = sum(1 for keyword in support_keywords if keyword in title)
        policy_score = sum(1 for keyword in ("政策", "国务院", "央行", "证监会", "关税", "制裁", "冲突") if keyword in title)
        score = relevance_score + risk_score + support_score + policy_score
        if score <= 0:
            continue
        risk_hits += risk_score
        support_hits += support_score
        published = row.get("published_at")
        time_text = ""
        if pd.notna(published):
            time_text = pd.to_datetime(published).strftime("%m-%d %H:%M")
        source = str(row.get("source") or "财经资讯")
        scored.append((score, f"{time_text} {source}｜{title}".strip()))

    headlines = [text for _, text in sorted(scored, key=lambda item: item[0], reverse=True)[:5]]
    if not headlines:
        return "最新资讯未发现与该股/行业直接相关的高权重事件", []
    if risk_hits > support_hits:
        return "最新政策与国际事件关键词偏风险，执行区间应更保守", headlines
    if support_hits > risk_hits:
        return "最新政策与产业事件关键词偏支持，但仍需价格与成交量确认", headlines
    return "最新事件信号多空混合，暂不单独改变技术结论", headlines


def analyze_position(
    history: pd.DataFrame,
    report: SignalReport,
    cost_price: float,
    shares: int,
    available_cash: float = 0.0,
    t_ratio: float = 0.2,
    account_assets: float = 0.0,
    sellable_shares: int | None = None,
    t_preference: str = "自动判断",
    commission_rate: float = 0.0003,
    slippage_rate: float = 0.001,
    breadth: dict[str, Any] | None = None,
    global_summary: dict[str, Any] | None = None,
    capital_hotspots: pd.DataFrame | None = None,
    industry: str = "",
    topics: list[str] | None = None,
    news: pd.DataFrame | None = None,
) -> PositionPlan:
    if cost_price <= 0:
        raise ValueError("持仓成本价必须大于0")
    if shares < 100 or shares % 100 != 0:
        raise ValueError("A股持股数量应为100股的整数倍，且不少于100股")
    if available_cash < 0:
        raise ValueError("可动用资金不能小于0")
    if account_assets < 0:
        raise ValueError("账户总资产不能小于0")
    if sellable_shares is None:
        sellable_shares = shares
    if sellable_shares < 0 or sellable_shares > shares or sellable_shares % 100 != 0:
        raise ValueError("可卖底仓必须是100股的整数倍，且不能超过持股数量")
    if not 0.1 <= t_ratio <= 0.5:
        raise ValueError("单次做T比例应在10%至50%之间")
    if t_preference not in {"自动判断", "先卖后买", "先买后卖"}:
        raise ValueError("不支持的做T方式")
    if not 0 <= commission_rate <= 0.01 or not 0 <= slippage_rate <= 0.02:
        raise ValueError("费率或滑点参数超出合理范围")
    if len(history) < 60:
        raise ValueError("至少需要60个交易日数据生成持仓规划")

    frame = add_indicators(history)
    last = frame.iloc[-1]
    current = float(report.close)
    atr = float(last["atr14"]) if pd.notna(last["atr14"]) else current * 0.035
    ma5 = float(last["ma5"]) if pd.notna(last["ma5"]) else current
    ma10 = float(last["ma10"]) if pd.notna(last["ma10"]) else current
    ma20 = float(last["ma20"]) if pd.notna(last["ma20"]) else current
    recent_low = float(frame["low"].tail(20).min())
    recent_high = float(frame["high"].tail(20).max())
    recent_frame = frame.tail(20).copy()
    recent_amplitude = (
        (pd.to_numeric(recent_frame["high"], errors="coerce")
        - pd.to_numeric(recent_frame["low"], errors="coerce"))
        / pd.to_numeric(recent_frame["close"], errors="coerce").replace(0, pd.NA)
    ).dropna()
    median_amplitude_20d = float(recent_amplitude.median()) if not recent_amplitude.empty else 0.0
    turnover_20d = pd.to_numeric(recent_frame.get("turnover"), errors="coerce").dropna()
    average_turnover_20d = float(turnover_20d.mean()) if not turnover_20d.empty else 0.0
    if average_turnover_20d >= 500_000_000:
        liquidity_label = "流动性充足"
    elif average_turnover_20d >= 100_000_000:
        liquidity_label = "流动性一般"
    elif average_turnover_20d > 0:
        liquidity_label = "流动性偏低"
    else:
        liquidity_label = "成交额数据暂缺"

    market_value = current * shares
    cost_value = cost_price * shares
    pnl = market_value - cost_value
    pnl_return = current / cost_price - 1
    break_even_gap = cost_price / current - 1
    position_concentration = market_value / account_assets if account_assets > 0 else None
    if position_concentration is None:
        concentration_label = "未填写账户总资产"
    elif position_concentration >= 0.5:
        concentration_label = "持仓高度集中"
    elif position_concentration >= 0.3:
        concentration_label = "持仓偏集中"
    else:
        concentration_label = "持仓集中度适中"

    market_temperature = float((breadth or {}).get("temperature", 50) or 50)
    global_score = float((global_summary or {}).get("score", 50) or 50)
    market_score = market_temperature * 0.7 + global_score * 0.3
    if market_score >= 62:
        market_label = "市场环境偏强"
    elif market_score >= 42:
        market_label = "市场环境中性"
    else:
        market_label = "市场环境偏弱"

    hotspot_label, hotspot_positive = _hotspot_context(capital_hotspots, industry, topics)
    event_label, event_headlines = _event_context(news, report.name, industry, topics)
    trend_positive = report.score >= 68 and current >= ma20
    trend_weak = report.score < 52 or current < ma20
    if trend_positive:
        trend_label = "短线趋势偏强"
    elif trend_weak:
        trend_label = "短线趋势偏弱"
    else:
        trend_label = "短线震荡"

    if t_preference == "自动判断":
        if trend_weak or market_score < 45:
            t_mode = "先卖后买"
        elif available_cash >= current * 100:
            t_mode = "先买后卖"
        else:
            t_mode = "先卖后买"
    else:
        t_mode = t_preference

    core_ratio = 0.7 if trend_positive else 0.8
    core_shares = _board_lot(math.ceil(shares * core_ratio))
    core_shares = min(core_shares, shares)
    tradable_above_core = max(0, shares - core_shares)
    base_t_shares = _board_lot(math.floor(shares * t_ratio))
    t_shares = min(base_t_shares, _board_lot(shares * 0.5), tradable_above_core)
    if trend_weak or market_score < 42:
        t_shares = min(t_shares, _board_lot(shares * 0.2))
    if t_mode == "先卖后买":
        t_shares = min(t_shares, sellable_shares)
    else:
        cash_limit = _board_lot(available_cash / max(current, 0.01))
        t_shares = min(t_shares, cash_limit, sellable_shares)
    if t_shares < 100:
        t_shares = 0

    support = max(recent_low, min(ma10, ma20), report.stop_loss + atr * 0.25)
    t_buy_low = max(report.stop_loss + atr * 0.2, support - atr * 0.35)
    t_buy_high = min(current, support + atr * 0.15)
    if t_buy_low > t_buy_high:
        t_buy_low = min(t_buy_high, current - atr * 0.45)

    resistance = min(recent_high, max(ma5, ma10, current + atr * 0.75))
    t_sell_low = max(current + atr * 0.45, resistance - atr * 0.2)
    t_sell_high = max(t_sell_low, min(recent_high, current + atr * 1.15))

    if trend_positive and market_score >= 55 and hotspot_positive:
        t_sell_low += atr * 0.1
        t_sell_high += atr * 0.15
    if trend_weak:
        t_sell_low = min(t_sell_low, current + atr * 0.55)
        t_sell_high = min(t_sell_high, current + atr * 0.85)

    t_buy_low = _round_price(t_buy_low)
    t_buy_high = _round_price(max(t_buy_low, t_buy_high))
    t_sell_low = _round_price(max(t_buy_high * 1.006, t_sell_low))
    t_sell_high = _round_price(max(t_sell_low, t_sell_high))

    midpoint_buy = (t_buy_low + t_buy_high) / 2
    midpoint_sell = (t_sell_low + t_sell_high) / 2
    spread_pct = midpoint_sell / midpoint_buy - 1
    sell_amount = midpoint_sell * t_shares
    buy_amount = midpoint_buy * t_shares
    fees = _estimated_fees(
        sell_amount,
        buy_amount,
        commission_rate=commission_rate,
        slippage_rate=slippage_rate,
    ) if t_shares else 0.0
    break_even_spread_pct = fees / buy_amount if buy_amount > 0 else 0.0
    opportunity_threshold = break_even_spread_pct + 0.006
    opportunity_days_20d = int((recent_amplitude >= opportunity_threshold).sum())
    round_profit = max(0.0, (midpoint_sell - midpoint_buy) * t_shares - fees)
    required_recovery_profit = max(0.0, (cost_price - current) * shares)
    estimated_rounds = (
        math.ceil(required_recovery_profit / round_profit)
        if required_recovery_profit > 0 and round_profit > 0
        else (0 if required_recovery_profit <= 0 else None)
    )
    cost_after_one = max(0.01, cost_price - round_profit / shares)
    cost_after_three = max(0.01, cost_price - round_profit * 3 / shares)
    invalidation_price = _round_price(max(report.stop_loss, recent_low - atr * 0.2))
    risk_to_invalidation = max(0.0, current - invalidation_price) * shares

    feasibility_score = 100
    if spread_pct < break_even_spread_pct + 0.006:
        feasibility_score -= 45
    if t_shares < 100:
        feasibility_score -= 40
    if trend_weak:
        feasibility_score -= 15
    if market_score < 42:
        feasibility_score -= 15
    if position_concentration is not None and position_concentration >= 0.5:
        feasibility_score -= 10
    if opportunity_days_20d < 5:
        feasibility_score -= 25
    elif opportunity_days_20d < 10:
        feasibility_score -= 10
    if 0 < average_turnover_20d < 100_000_000:
        feasibility_score -= 15
    if t_mode == "先买后卖" and available_cash < midpoint_buy * max(t_shares, 100):
        feasibility_score -= 20
    feasibility_score = max(0, min(100, feasibility_score))
    if feasibility_score >= 70:
        feasibility_label = "可小比例执行"
    elif feasibility_score >= 45:
        feasibility_label = "等待确认"
    else:
        feasibility_label = "本轮不建议做T"

    risks = [
        "A股实行T+1：当天新买入的股票不能当天卖出，只有原有可卖持仓才能做正T或倒T。",
        "做T可能卖飞或越补越跌；跌破止损/关键支撑时应先控制风险，不能把摊低成本当成唯一目标。",
        "区间按当前行情计算，盘中价格、热点、政策与国际事件变化后必须重新刷新。",
    ]
    if spread_pct < break_even_spread_pct + 0.006:
        risks.append(
            f"当前预期价差不足以覆盖费用后再保留0.6%安全垫；"
            f"最低盈亏平衡价差约 {break_even_spread_pct:.2%}。"
        )
        t_shares = 0
        round_profit = 0.0
        cost_after_one = cost_price
        cost_after_three = cost_price
        estimated_rounds = None if required_recovery_profit > 0 else 0
        feasibility_label = "本轮不建议做T"
    if trend_weak:
        risks.append("技术趋势偏弱，不建议用新增资金连续补仓；优先等待企稳或反弹降低风险敞口。")
    if market_score < 42:
        risks.append("市场与全球风险偏弱，单次做T比例已自动压低。")
    if position_concentration is not None and position_concentration >= 0.5:
        risks.append("该股占账户总资产比例过高，首要任务是降低单股集中风险，而不是继续增加投入。")
    if opportunity_days_20d < 5:
        risks.append(
            f"过去20个交易日仅 {opportunity_days_20d} 天的日内振幅覆盖当前最低有效价差，"
            "该股近期不适合频繁做T。"
        )
    if 0 < average_turnover_20d < 100_000_000:
        risks.append("近20日平均成交额偏低，分批委托也可能出现滑点或成交困难。")
    if t_mode == "先买后卖" and available_cash < midpoint_buy * 100:
        risks.append("可动用资金不足以先买100股，已无法执行“先买后卖”。")
    if sellable_shares < 100:
        risks.append("当前没有至少100股可卖底仓，今天无法完成一轮日内T。")

    if pnl_return >= 0:
        action_summary = "当前处于盈利或接近盈利区，重点是保护利润，不以频繁做T扩大风险。"
    elif break_even_gap <= 0.05:
        action_summary = "距离回本较近，可围绕支撑与压力小比例做T，接近成本区分批降风险。"
    elif trend_weak:
        action_summary = "套牢且趋势偏弱，回本优先级应让位于控制回撤；等待企稳后再做小比例T。"
    else:
        action_summary = "仍有回本距离，采用小比例、可重复验证的做T方案，避免一次性重仓补亏。"

    max_cash_buy = _board_lot(available_cash / max(t_buy_high, 0.01))
    if t_mode == "先卖后买":
        sequence = (
            f"优先等反弹进入高抛区 {t_sell_low:.2f}-{t_sell_high:.2f} 卖出可卖底仓，"
            f"随后只在回落至 {t_buy_low:.2f}-{t_buy_high:.2f} 且企稳时买回等量股票。"
        )
    else:
        sequence = (
            f"优先等回落至低吸区 {t_buy_low:.2f}-{t_buy_high:.2f} 买入，"
            f"随后上涨至 {t_sell_low:.2f}-{t_sell_high:.2f} 时卖出等量原有可卖底仓。"
        )
    steps = [
        f"本轮方式：{t_mode}。{sequence}",
        f"单次上限 {t_shares} 股，保留至少 {core_shares} 股核心仓；未满足触发条件则不交易。",
        f"跌破失效位 {invalidation_price:.2f} 或放量破位时停止做T，先评估减仓与风险。",
        "每完成一轮后重新刷新行情、热点和资讯，不按同一价格区间机械重复。",
    ]
    if available_cash > 0:
        steps.append(f"按可动用资金最多可买 {max_cash_buy} 股，但仍以本次参考股数为上限。")
    if cost_price > current:
        steps.append(
            f"若一轮按区间中值完成，理论成本约降至 {cost_after_one:.2f}；"
            f"三轮均成功约降至 {cost_after_three:.2f}，不代表必然成交。"
        )
        if estimated_rounds is not None and estimated_rounds > 0:
            steps.append(
                f"按本轮理论净收益静态估算，完全弥补当前浮亏约需 {estimated_rounds} 轮；"
                "若轮数过多，应重新评估持仓逻辑，不能依赖高频做T硬扛。"
            )
    steps.append("若跌破止损位或热点/政策方向明显转弱，暂停做T并重新评估持仓逻辑。")

    buy_ladder = _price_ladder(t_buy_low, t_buy_high, t_shares)
    sell_ladder = _price_ladder(t_sell_low, t_sell_high, t_shares, reverse=True)

    return PositionPlan(
        cost_price=cost_price,
        shares=shares,
        current_price=current,
        market_value=market_value,
        cost_value=cost_value,
        unrealized_pnl=pnl,
        unrealized_return=pnl_return,
        break_even_gap=break_even_gap,
        position_concentration=position_concentration,
        concentration_label=concentration_label,
        sellable_shares=sellable_shares,
        core_shares=core_shares,
        t_shares=t_shares,
        t_mode=t_mode,
        feasibility_score=feasibility_score,
        feasibility_label=feasibility_label,
        break_even_spread_pct=break_even_spread_pct,
        median_amplitude_20d=median_amplitude_20d,
        opportunity_days_20d=opportunity_days_20d,
        average_turnover_20d=average_turnover_20d,
        liquidity_label=liquidity_label,
        t_buy_low=t_buy_low,
        t_buy_high=t_buy_high,
        t_sell_low=t_sell_low,
        t_sell_high=t_sell_high,
        expected_spread_pct=spread_pct,
        estimated_round_profit=round_profit,
        required_recovery_profit=required_recovery_profit,
        estimated_rounds_to_break_even=estimated_rounds,
        cost_after_one_round=cost_after_one,
        cost_after_three_rounds=cost_after_three,
        recovery_price_after_one_round=cost_after_one,
        recovery_price_after_three_rounds=cost_after_three,
        trend_label=trend_label,
        market_label=market_label,
        hotspot_label=hotspot_label,
        event_label=event_label,
        event_headlines=event_headlines,
        invalidation_price=invalidation_price,
        risk_to_invalidation=risk_to_invalidation,
        buy_ladder=buy_ladder,
        sell_ladder=sell_ladder,
        action_summary=action_summary,
        steps=steps,
        risks=risks,
    )
