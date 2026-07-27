from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import numpy as np
import pandas as pd

from .data import (
    DataSourceError,
    append_spot_bar,
    fetch_daily_history,
    fetch_global_indices,
    fetch_spot,
    global_risk_summary,
    market_breadth,
    get_akshare,
    plain_code,
    prepare_spot_universe,
)
from .indicators import add_indicators
from .fundamentals import fetch_fundamental_snapshot, industry_matches
from .presentation import HORIZON_LABELS
from .strategy import SignalReport, analyze_stock


@dataclass
class MarketContext:
    boards: pd.DataFrame
    hot_rank: pd.DataFrame
    stock_themes: dict[str, dict[str, Any]]
    breadth: dict[str, Any]
    global_indices: pd.DataFrame
    global_summary: dict[str, Any]
    capital_hotspots: pd.DataFrame
    fundamental_universe: pd.DataFrame
    financial_report_date: str
    errors: list[str]
    spot: pd.DataFrame = field(default_factory=pd.DataFrame)


def _to_number(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = df.copy()
    for column in columns:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    return out


def _normalize_board_frame(raw: pd.DataFrame, board_type: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    rename = {
        "板块名称": "board_name",
        "板块代码": "board_code",
        "涨跌幅": "change_pct",
        "换手率": "turnover_rate",
        "上涨家数": "up_count",
        "下跌家数": "down_count",
        "领涨股票": "leader",
        "领涨股票-涨跌幅": "leader_change_pct",
    }
    df = raw.rename(columns=rename).copy()
    keep = [
        "board_name",
        "board_code",
        "change_pct",
        "turnover_rate",
        "up_count",
        "down_count",
        "leader",
        "leader_change_pct",
    ]
    for column in keep:
        if column not in df.columns:
            df[column] = np.nan
    df = df[keep]
    df["board_type"] = board_type
    df = _to_number(df, ["change_pct", "turnover_rate", "up_count", "down_count", "leader_change_pct"])
    breadth = df["up_count"] / (df["up_count"] + df["down_count"]).replace(0, np.nan)
    df["heat_score"] = (
        df["change_pct"].rank(pct=True).fillna(0) * 45
        + df["turnover_rate"].rank(pct=True).fillna(0) * 25
        + breadth.fillna(0.5) * 30
    ).clip(0, 100)
    return df.sort_values("heat_score", ascending=False).reset_index(drop=True)


def fetch_market_context(
    ignore_proxy: bool = True,
    concept_count: int = 8,
    industry_count: int = 6,
    constituents_per_board: int = 0,
) -> MarketContext:
    ak = get_akshare(ignore_proxy=ignore_proxy)
    errors: list[str] = []
    board_frames: list[pd.DataFrame] = []

    try:
        concepts = _normalize_board_frame(ak.stock_board_concept_name_em(), "题材")
        board_frames.append(concepts.head(concept_count))
    except Exception as exc:
        errors.append(f"题材板块获取失败：{exc}")

    try:
        industries = _normalize_board_frame(ak.stock_board_industry_name_em(), "行业")
        board_frames.append(industries.head(industry_count))
    except Exception as exc:
        errors.append(f"行业板块获取失败：{exc}")

    boards = pd.concat(board_frames, ignore_index=True) if board_frames else pd.DataFrame()

    try:
        hot_rank = ak.stock_hot_rank_em().rename(
            columns={"代码": "symbol", "股票名称": "name", "当前排名": "rank", "涨跌幅": "change_pct", "最新价": "price"}
        )
        hot_rank["symbol"] = hot_rank["symbol"].astype(str).str.upper()
        hot_rank["code"] = hot_rank["symbol"].map(plain_code)
        hot_rank = _to_number(hot_rank, ["rank", "change_pct", "price"])
    except Exception as exc:
        errors.append(f"热度榜获取失败：{exc}")
        hot_rank = pd.DataFrame(columns=["symbol", "name", "rank", "change_pct", "price", "code"])

    stock_themes: dict[str, dict[str, Any]] = {}
    if not hot_rank.empty:
        for _, row in hot_rank.head(100).iterrows():
            code = str(row["code"])
            stock_themes.setdefault(code, {"topics": [], "industry": "", "theme_strength": 0})
            rank = float(row["rank"]) if pd.notna(row["rank"]) else 100
            stock_themes[code]["theme_strength"] = max(stock_themes[code]["theme_strength"], 100 - rank + 1)
            stock_themes[code]["topics"].append("热度榜")

    if not boards.empty and constituents_per_board > 0:
        for _, board in boards.iterrows():
            board_name = str(board["board_name"])
            board_type = str(board["board_type"])
            heat = float(board["heat_score"]) if pd.notna(board["heat_score"]) else 0.0
            try:
                if board_type == "题材":
                    cons = ak.stock_board_concept_cons_em(symbol=board_name)
                else:
                    cons = ak.stock_board_industry_cons_em(symbol=board_name)
            except Exception as exc:
                errors.append(f"{board_type}{board_name}成分股获取失败：{exc}")
                continue

            if cons.empty or "代码" not in cons.columns:
                continue
            for _, stock in cons.head(constituents_per_board).iterrows():
                code = plain_code(str(stock["代码"]))
                stock_themes.setdefault(code, {"topics": [], "industry": "", "theme_strength": 0})
                if board_type == "题材":
                    stock_themes[code]["topics"].append(board_name)
                else:
                    stock_themes[code]["industry"] = board_name
                stock_themes[code]["theme_strength"] = max(stock_themes[code]["theme_strength"], heat)

    for item in stock_themes.values():
        item["topics"] = list(dict.fromkeys(item["topics"]))[:5]

    try:
        global_indices = fetch_global_indices(ignore_proxy=ignore_proxy)
        global_summary = global_risk_summary(global_indices)
    except Exception as exc:
        errors.append(f"全球市场获取失败：{exc}")
        global_indices = pd.DataFrame()
        global_summary = {}

    return MarketContext(
        boards=boards,
        hot_rank=hot_rank,
        stock_themes=stock_themes,
        breadth={},
        global_indices=global_indices,
        global_summary=global_summary,
        capital_hotspots=pd.DataFrame(),
        fundamental_universe=pd.DataFrame(),
        financial_report_date="",
        errors=errors,
    )


def _candidate_pool(
    universe: pd.DataFrame,
    context: MarketContext,
    min_turnover: float,
    scan_size: int,
    industry_filter: str = "全部",
    min_price: float = 2.0,
    max_price: float = 300.0,
    random_price_pick: bool = False,
    require_fund_flow: bool = True,
    require_valuation: bool = True,
    require_cashflow: bool = True,
    require_dividend: bool = True,
    allow_near_match: bool = True,
) -> pd.DataFrame:
    min_price = max(0.01, float(min_price or 0.01))
    max_price = max(min_price, float(max_price or min_price))
    filtered = universe.copy()
    if industry_filter != "全部" and "financial_industry" in filtered.columns:
        filtered = filtered[
            filtered["financial_industry"].map(lambda value: industry_matches(value, industry_filter))
        ].copy()
    filtered, filter_note = _filter_fundamental_candidates(
        filtered,
        require_fund_flow=require_fund_flow,
        require_valuation=require_valuation,
        require_cashflow=require_cashflow,
        require_dividend=require_dividend,
        allow_near_match=allow_near_match,
    )

    base = prepare_spot_universe(
        filtered,
        min_turnover=min_turnover,
        min_price=min_price,
        max_price=max_price,
        min_change_pct=-9.5,
        max_change_pct=19.5,
    ).copy()
    filter_mode = str(filtered["filter_mode"].iloc[0]) if not filtered.empty else "严格匹配"
    base["pool_reason"] = "全市场资金面/基本面预筛" if filter_mode == "严格匹配" else filter_mode

    if base.empty and not filtered.empty:
        base = prepare_spot_universe(
            filtered,
            min_turnover=0,
            min_price=min_price,
            max_price=max_price,
            min_change_pct=-20.0,
            max_change_pct=30.0,
        ).copy()
        base["pool_reason"] = (
            "严格因子合格，流动性字段降级复核"
            if filter_mode == "严格匹配"
            else f"{filter_mode}，流动性字段降级复核"
        )

    hot_codes = set(context.hot_rank.head(80)["code"].astype(str).tolist()) if not context.hot_rank.empty else set()
    theme_codes = set(context.stock_themes.keys())

    if not context.boards.empty:
        for _, board in context.boards.iterrows():
            leader = str(board.get("leader", "")).strip()
            if not leader:
                continue
            matches = universe[universe["name"].astype(str) == leader]
            if matches.empty:
                continue
            code = str(matches.iloc[0]["code"])
            theme_codes.add(code)
            context.stock_themes.setdefault(code, {"topics": [], "industry": "", "theme_strength": 0})
            if board["board_type"] == "题材":
                context.stock_themes[code]["topics"].append(str(board["board_name"]))
            else:
                context.stock_themes[code]["industry"] = str(board["board_name"])
            context.stock_themes[code]["theme_strength"] = max(
                float(context.stock_themes[code].get("theme_strength", 0)),
                float(board.get("heat_score", 0) or 0),
            )

    if hot_codes or theme_codes:
        theme_pick = base[base["code"].isin(hot_codes | theme_codes)].copy()
        if not theme_pick.empty:
            theme_pick["pool_reason"] = "热点/题材"
            base = pd.concat([base.head(scan_size), theme_pick], ignore_index=True)
    else:
        base = base.head(scan_size)

    if base.empty:
        base.attrs["filter_note"] = filter_note
        return base
    base = base.drop_duplicates(subset=["code"]).copy()
    base["theme_strength"] = base["code"].map(lambda code: context.stock_themes.get(str(code), {}).get("theme_strength", 0))
    fundamental_score = (
        base["fundamental_score"].fillna(0)
        if "fundamental_score" in base.columns
        else pd.Series(0, index=base.index)
    )
    base["pool_score"] = (
        base.get("pre_score", 0).fillna(0) * 0.45
        + base["theme_strength"].fillna(0) * 0.15
        + fundamental_score * 0.4
    )
    if random_price_pick and len(base) > scan_size:
        random_state = int(date.today().strftime("%Y%m%d"))
        base = base.head(max(scan_size * 3, scan_size)).sample(
            n=scan_size,
            random_state=random_state,
        )
    result = base.sort_values("pool_score", ascending=False).head(scan_size).reset_index(drop=True)
    result.attrs["filter_note"] = filter_note
    return result


FUNDAMENTAL_FILTER_RULES = (
    ("fund_flow_pass", "3/5/10日主力净流入"),
    ("valuation_normal", "同行业估值"),
    ("cashflow_good", "经营现金流"),
    ("dividend_stable", "历史分红"),
)

FACTOR_SOURCE_COLUMNS = {
    "fund_flow_pass": ("net_inflow_3d", "net_inflow_5d", "net_inflow_10d"),
    "valuation_normal": ("pe_est", "pb_est"),
    "cashflow_good": ("operating_cash_flow", "operating_cash_flow_per_share"),
    "dividend_stable": ("dividend_count", "dividend_year_ratio"),
}


def _factor_source_available(universe: pd.DataFrame, rule_column: str) -> bool:
    source_columns = FACTOR_SOURCE_COLUMNS[rule_column]
    present_columns = [column for column in source_columns if column in universe.columns]
    if not present_columns:
        # Boolean-only frames are used by older snapshots and unit tests.
        return rule_column in universe.columns
    if len(present_columns) != len(source_columns):
        return False
    minimum_rows = min(1_000, max(1, int(np.ceil(len(universe) * 0.2))))
    return all(int(universe[column].notna().sum()) >= minimum_rows for column in source_columns)


def _filter_fundamental_candidates(
    universe: pd.DataFrame,
    *,
    require_fund_flow: bool = True,
    require_valuation: bool = True,
    require_cashflow: bool = True,
    require_dividend: bool = True,
    allow_near_match: bool = True,
) -> tuple[pd.DataFrame, str]:
    """Apply requested fundamentals, with an explicit and auditable near-match fallback."""
    requested = {
        "fund_flow_pass": require_fund_flow,
        "valuation_normal": require_valuation,
        "cashflow_good": require_cashflow,
        "dividend_stable": require_dividend,
    }
    requested_rules = [
        (column, label) for column, label in FUNDAMENTAL_FILTER_RULES if requested[column]
    ]
    unavailable_rules = [
        (column, label)
        for column, label in requested_rules
        if not _factor_source_available(universe, column)
    ]
    unavailable_columns = {column for column, _ in unavailable_rules}
    rules = [item for item in requested_rules if item[0] not in unavailable_columns]
    unavailable_labels = "、".join(label for _, label in unavailable_rules)
    coverage_note = (
        f"{unavailable_labels}数据覆盖不足，本轮未作为硬筛条件；推荐原因会明确标注。"
        if unavailable_labels
        else ""
    )
    if universe.empty or not rules:
        result = universe.copy()
        result["filter_match_count"] = 0
        result["filter_match_total"] = len(rules)
        result["filter_requested_total"] = len(requested_rules)
        result["filter_missing_labels"] = ""
        result["filter_unavailable_labels"] = unavailable_labels
        result["filter_mode"] = (
            "数据覆盖降级筛选" if unavailable_labels else "未启用基本面硬筛"
        )
        return result, coverage_note

    scored = universe.copy()
    matches: dict[str, pd.Series] = {}
    for column, _ in rules:
        if column in scored.columns:
            matches[column] = scored[column].fillna(False).astype(bool)
        else:
            matches[column] = pd.Series(False, index=scored.index, dtype=bool)

    match_frame = pd.DataFrame(matches, index=scored.index)
    scored["filter_match_count"] = match_frame.sum(axis=1).astype(int)
    scored["filter_match_total"] = len(rules)
    scored["filter_requested_total"] = len(requested_rules)
    scored["filter_unavailable_labels"] = unavailable_labels
    scored["filter_missing_labels"] = match_frame.apply(
        lambda row: "、".join(label for column, label in rules if not bool(row[column])),
        axis=1,
    )

    strict = scored[scored["filter_match_count"] == len(rules)].copy()
    if not strict.empty:
        strict["filter_mode"] = "严格匹配"
        return strict, coverage_note
    if not allow_near_match or len(rules) < 2:
        strict["filter_mode"] = "严格匹配"
        return strict, coverage_note

    thresholds = []
    for threshold in (len(rules) - 1, int(np.ceil(len(rules) / 2))):
        if threshold >= 1 and threshold not in thresholds:
            thresholds.append(threshold)
    for threshold in thresholds:
        near = scored[scored["filter_match_count"] >= threshold].copy()
        if near.empty:
            continue
        near["filter_mode"] = f"观察级近似匹配（至少{threshold}/{len(rules)}项）"
        near_note = (
            f"严格组合筛选无结果，已启用观察级近似匹配：候选至少满足{threshold}/{len(rules)}项；"
            "每只股票的未满足项会在推荐原因中标明。"
        )
        note = " ".join(item for item in (coverage_note, near_note) if item)
        return near, note

    strict["filter_mode"] = "严格匹配"
    return strict, coverage_note


def _enrich_records_with_keywords(
    records: list[dict[str, Any]],
    ignore_proxy: bool,
    errors: list[str],
) -> list[dict[str, Any]]:
    if not records:
        return records
    ak = get_akshare(ignore_proxy=ignore_proxy)
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_symbol.setdefault(str(record["symbol"]), []).append(record)

    for symbol, grouped in by_symbol.items():
        try:
            keywords = ak.stock_hot_keyword_em(symbol=symbol)
        except Exception as exc:
            errors.append(f"{symbol} 题材关键词获取失败：{exc}")
            continue
        if keywords.empty or "概念名称" not in keywords.columns:
            continue
        topics = [str(value) for value in keywords["概念名称"].head(4).tolist() if str(value)]
        heat = 0.0
        if "热度" in keywords.columns:
            heat = float(pd.to_numeric(keywords["热度"], errors="coerce").fillna(0).max())
            heat = min(100.0, heat)
        for record in grouped:
            old_topics = record.get("topics", [])
            combined = list(dict.fromkeys([*old_topics, *topics]))[:6]
            record["topics"] = combined
            record["topic_text"] = "、".join(combined)
            record["theme_strength"] = max(float(record.get("theme_strength", 0) or 0), heat)
    return records


def infer_industry(topics: list[str]) -> str:
    text = " ".join(topics)
    rules = [
        ("电子/半导体", ("半导体", "芯片", "光刻", "传感器", "消费电子", "PCB")),
        ("计算机/通信", ("人工智能", "算力", "软件", "数据", "信创", "5G", "通信", "互联网")),
        ("新能源/电力设备", ("新能源", "光伏", "储能", "电池", "风电", "特高压", "电力")),
        ("材料/有色金属", ("新材料", "稀土", "黄金", "有色", "金属", "资源", "有机硅")),
        ("汽车/高端制造", ("汽车", "机器人", "工业", "智能制造", "设备", "机械")),
        ("医药生物", ("医药", "医疗", "生物", "创新药", "中药")),
        ("消费", ("食品", "饮料", "白酒", "家电", "零售", "旅游")),
        ("金融地产", ("银行", "证券", "保险", "地产", "金融")),
        ("国防军工", ("军工", "商业航天", "航空", "卫星")),
        ("环保公用", ("环保", "水务", "燃气", "公用事业")),
    ]
    for industry, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return industry
    return "综合"


def _horizon_scores(
    history: pd.DataFrame,
    report: SignalReport,
    theme_strength: float,
    global_score: float = 50,
    fundamental_score: float = 50,
) -> dict[str, float]:
    df = add_indicators(history)
    last = df.iloc[-1]
    prev = df.iloc[-2]
    close = float(last["close"])
    volume_ratio = float(last["volume_ratio"]) if pd.notna(last["volume_ratio"]) else 1.0
    rsi14 = float(last["rsi14"]) if pd.notna(last["rsi14"]) else 50.0
    atr_pct = float(last["atr_pct"]) if pd.notna(last["atr_pct"]) else 0.04

    ma5 = float(last["ma5"]) if pd.notna(last["ma5"]) else close
    ma20 = float(last["ma20"]) if pd.notna(last["ma20"]) else close
    ma60 = float(last["ma60"]) if pd.notna(last["ma60"]) else close
    ma120 = float(last["ma120"]) if pd.notna(last["ma120"]) else close

    breakout = close > float(df["high"].rolling(20, min_periods=10).max().shift(1).iloc[-1])
    macd_improving = last["macd_hist"] > prev["macd_hist"]
    global_adjustment = float(np.clip((global_score - 50) / 10, -5, 5))

    short = (
        report.score * 0.45
        + min(volume_ratio, 2.5) / 2.5 * 20
        + min(theme_strength, 100) * 0.2
        + (15 if close > ma5 > ma20 else 0)
        + (12 if breakout else 0)
        + (8 if macd_improving else 0)
        - (12 if rsi14 > 78 else 0)
        + global_adjustment
    )
    mid = (
        report.score * 0.62
        + min(theme_strength, 100) * 0.15
        + (14 if close > ma20 > ma60 else 0)
        + (10 if last["ma20_slope"] > 0 else 0)
        + (8 if last["return_20d"] > 0 else 0)
        + (6 if 45 <= rsi14 <= 72 else 0)
        + global_adjustment * 0.7
    )
    long = (
        report.score * 0.55
        + (18 if close > ma60 > ma120 else 0)
        + (12 if last["ma60_slope"] > 0 else 0)
        + (10 if 0.015 <= atr_pct <= 0.055 else 0)
        + (8 if last["return_20d"] > 0 else 0)
        + min(theme_strength, 100) * 0.08
        + global_adjustment * 0.4
    )
    return {
        "short": float(np.clip(short * 0.82 + fundamental_score * 0.18, 0, 100)),
        "mid": float(np.clip(mid * 0.75 + fundamental_score * 0.25, 0, 100)),
        "long": float(np.clip(long * 0.68 + fundamental_score * 0.32, 0, 100)),
    }


def _priority(score: float) -> str:
    if score >= 82:
        return "重点"
    if score >= 70:
        return "关注"
    return "观察"


def _record(
    run_id: int,
    trade_date: str,
    horizon: str,
    score: float,
    report: SignalReport,
    theme: dict[str, Any],
    global_summary: dict[str, Any],
    factors: dict[str, Any],
) -> dict[str, Any]:
    topics = theme.get("topics", [])
    reasons = list(report.reasons)
    global_label = str(global_summary.get("label", ""))
    if global_label:
        reasons.append(f"全球主要指数风险偏好：{global_label}")
    flow_values = [
        factors.get("net_inflow_3d"),
        factors.get("net_inflow_5d"),
        factors.get("net_inflow_10d"),
    ]
    if all(pd.notna(value) for value in flow_values):
        reasons.append(
            "主力资金3日/5日/10日净流入："
            + "/".join(f"{float(value) / 100_000_000:.2f}亿" for value in flow_values)
        )
    if bool(factors.get("valuation_normal", False)):
        reasons.append(
            f"估值处于同行业常态区间：市盈率（PE）约{float(factors.get('pe_est', 0)):.1f}，"
            f"市净率（PB）约{float(factors.get('pb_est', 0)):.2f}"
        )
    if bool(factors.get("cashflow_good", False)):
        reasons.append("经营活动现金流量净额及每股经营现金流均为正")
    if bool(factors.get("dividend_stable", False)):
        reasons.append(
            f"历史分红稳定度{float(factors.get('dividend_year_ratio', 0)):.0%}，"
            f"累计分红{int(float(factors.get('dividend_count', 0) or 0))}次"
        )
    match_total = int(float(factors.get("filter_match_total", 0) or 0))
    match_count = int(float(factors.get("filter_match_count", 0) or 0))
    if match_total and match_count < match_total:
        missing = str(factors.get("filter_missing_labels", "") or "").strip()
        reasons.append(
            f"观察级近似匹配：满足{match_count}/{match_total}项"
            + (f"；未满足{missing}，需重点复核" if missing else "，需重点复核未达标条件")
        )
    unavailable = str(factors.get("filter_unavailable_labels", "") or "").strip()
    if unavailable:
        reasons.append(f"数据覆盖提示：{unavailable}本轮未作为硬筛条件，需结合原始财报复核")
    factor_keywords = ("主力资金", "估值处于", "经营活动现金流", "历史分红")
    factor_reasons = [reason for reason in reasons if reason.startswith(factor_keywords)]
    reasons = factor_reasons + [reason for reason in reasons if reason not in factor_reasons]
    return {
        "run_id": run_id,
        "trade_date": trade_date,
        "horizon": horizon,
        "horizon_label": HORIZON_LABELS[horizon],
        "symbol": report.symbol.upper(),
        "name": report.name,
        "score": round(score, 2),
        "base_score": report.score,
        "rating": report.rating,
        "priority": _priority(score),
        "action": report.action,
        "close": report.close,
        "buy_zone_low": report.buy_zone_low,
        "buy_zone_high": report.buy_zone_high,
        "stop_loss": report.stop_loss,
        "take_profit_1": report.take_profit_1,
        "take_profit_2": report.take_profit_2,
        "trailing_stop": report.trailing_stop,
        "position_pct": report.position_pct,
        "industry": theme.get("industry", "") or factors.get("financial_industry", ""),
        "topics": topics,
        "topic_text": "、".join(topics),
        "theme_strength": float(theme.get("theme_strength", 0)),
        "fundamental_score": float(factors.get("fundamental_score", 0) or 0),
        "net_inflow_3d": factors.get("net_inflow_3d"),
        "net_inflow_5d": factors.get("net_inflow_5d"),
        "net_inflow_10d": factors.get("net_inflow_10d"),
        "pe_est": factors.get("pe_est"),
        "pb_est": factors.get("pb_est"),
        "industry_pe_median": factors.get("industry_pe_median"),
        "industry_pb_median": factors.get("industry_pb_median"),
        "operating_cash_flow": factors.get("operating_cash_flow"),
        "operating_cash_flow_per_share": factors.get("operating_cash_flow_per_share"),
        "dividend_count": factors.get("dividend_count"),
        "dividend_year_ratio": factors.get("dividend_year_ratio"),
        "valuation_normal": bool(factors.get("valuation_normal", False)),
        "cashflow_good": bool(factors.get("cashflow_good", False)),
        "dividend_stable": bool(factors.get("dividend_stable", False)),
        "reasons": reasons,
        "sell_triggers": report.sell_triggers,
        "warnings": report.warnings,
    }


def scan_recommendations(
    run_id: int,
    scan_size: int = 80,
    top_n: int = 8,
    min_turnover: float = 50_000_000,
    ignore_proxy: bool = True,
    max_workers: int = 4,
    progress_callback: Any | None = None,
    min_net_inflow: float = 100_000_000,
    industry_filter: str = "全部",
    min_price: float = 2.0,
    max_price: float = 300.0,
    random_price_pick: bool = False,
    require_fund_flow: bool = True,
    require_valuation: bool = True,
    require_cashflow: bool = True,
    require_dividend: bool = True,
    allow_near_match: bool = True,
) -> tuple[pd.DataFrame, MarketContext, list[str]]:
    errors: list[str] = []
    spot = fetch_spot(ignore_proxy=ignore_proxy)
    spot_warning = str(spot.attrs.get("source_warning") or "").strip()
    if spot_warning:
        errors.append(f"行情数据提示：{spot_warning}")
    context = fetch_market_context(ignore_proxy=ignore_proxy)
    context.spot = spot
    context.breadth = market_breadth(spot)
    errors.extend(context.errors)
    fundamental_snapshot = fetch_fundamental_snapshot(
        spot,
        min_inflow=min_net_inflow,
        ignore_proxy=ignore_proxy,
    )
    context.capital_hotspots = fundamental_snapshot.hotspots
    context.fundamental_universe = fundamental_snapshot.universe
    context.financial_report_date = fundamental_snapshot.report_date
    errors.extend(fundamental_snapshot.errors)
    pool = _candidate_pool(
        fundamental_snapshot.universe,
        context,
        min_turnover=min_turnover,
        scan_size=scan_size,
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
    filter_note = str(pool.attrs.get("filter_note", "") or "")
    if filter_note:
        errors.append(filter_note)
    if pool.empty:
        raise DataSourceError(
            "当前行业、股价和综合条件组合没有可用候选。请扩大股价范围、降低净流入阈值，"
            "或取消一项基本面条件后重试。"
        )

    start = date.today() - timedelta(days=760)
    total = len(pool)
    records_by_horizon: dict[str, list[dict[str, Any]]] = {"short": [], "mid": [], "long": []}

    def analyze_row(row_data: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
        symbol = str(row_data["symbol"])
        name = str(row_data["name"])
        history = fetch_daily_history(symbol, start_date=start, adjust="qfq", ignore_proxy=ignore_proxy)
        history = append_spot_bar(history, row_data)
        report = analyze_stock(history, symbol=symbol, name=name)
        theme = context.stock_themes.get(str(row_data["code"]), {"topics": [], "industry": "", "theme_strength": 0})
        if not theme.get("industry"):
            theme["industry"] = str(row_data.get("financial_industry", "") or "")
        scores = _horizon_scores(
            history,
            report,
            float(theme.get("theme_strength", 0)),
            float(context.global_summary.get("score", 50)),
            float(row_data.get("fundamental_score", 50) or 50),
        )
        records: list[dict[str, Any]] = []
        for horizon, score in scores.items():
            if score >= 58 and report.position_pct > 0:
                records.append(
                    _record(
                        run_id,
                        report.date,
                        horizon,
                        score,
                        report,
                        theme,
                        context.global_summary,
                        row_data,
                    )
                )
        return symbol, name, records

    rows = [row.to_dict() for _, row in pool.iterrows()]
    done = 0
    analyzed_success = 0
    analyzed_failure = 0
    signal_record_count = 0
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 6))) as executor:
        futures = {executor.submit(analyze_row, row): row for row in rows}
        for future in as_completed(futures):
            done += 1
            row_data = futures[future]
            symbol = str(row_data["symbol"])
            name = str(row_data["name"])
            if progress_callback:
                progress_callback(done, total, symbol.upper(), name)
            try:
                _, _, records = future.result()
                analyzed_success += 1
                signal_record_count += len(records)
                for record in records:
                    records_by_horizon[record["horizon"]].append(record)
            except Exception as exc:
                analyzed_failure += 1
                errors.append(f"{symbol.upper()} {name} 分析失败：{exc}")

    sorted_by_horizon = {
        horizon: sorted(records, key=lambda item: item["score"], reverse=True)
        for horizon, records in records_by_horizon.items()
    }
    selected: list[dict[str, Any]] = []
    used_symbols: set[str] = set()
    per_horizon_count = {"short": 0, "mid": 0, "long": 0}
    per_horizon_limit = min(top_n, 10)
    for _round in range(per_horizon_limit):
        for horizon in ("short", "mid", "long"):
            if per_horizon_count[horizon] >= per_horizon_limit:
                continue
            candidate = next(
                (
                    item
                    for item in sorted_by_horizon[horizon]
                    if str(item["symbol"]) not in used_symbols
                ),
                None,
            )
            if candidate is None:
                continue
            selected.append(candidate)
            used_symbols.add(str(candidate["symbol"]))
            per_horizon_count[horizon] += 1
    selected = sorted(selected, key=lambda item: item["score"], reverse=True)[:30]

    selected = _enrich_records_with_keywords(selected, ignore_proxy=ignore_proxy, errors=errors)
    for record in selected:
        if not record.get("industry"):
            record["industry"] = infer_industry(record.get("topics", []))

    frame = pd.DataFrame(selected)
    if not frame.empty:
        horizon_order = {"short": 0, "mid": 1, "long": 2}
        frame["_order"] = frame["horizon"].map(horizon_order)
        frame = frame.sort_values(["_order", "score"], ascending=[True, False]).drop(columns=["_order"])
    errors.append(
        "扫描诊断："
        f"基本面候选{total}只，K线分析成功{analyzed_success}只、失败{analyzed_failure}只，"
        f"达到技术与仓位阈值{signal_record_count}条，最终推荐{len(frame)}只"
    )
    return frame.reset_index(drop=True), context, errors


def display_recommendations(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = pd.DataFrame(
        {
            "周期": frame["horizon"].map(HORIZON_LABELS).fillna(frame["horizon"]),
            "优先级": frame["priority"],
            "代码": frame["symbol"],
            "名称": frame["name"],
            "评分": frame["score"],
            "收盘价": frame["close"].round(2),
            "买点": frame.apply(lambda r: f"{r['buy_zone_low']:.2f}-{r['buy_zone_high']:.2f}", axis=1),
            "止损": frame["stop_loss"].round(2),
            "目标": frame.apply(lambda r: f"{r['take_profit_1']:.2f}/{r['take_profit_2']:.2f}", axis=1),
            "仓位": frame["position_pct"].map(lambda v: f"{v:.0%}"),
            "行业": frame["industry"].fillna(""),
            "题材": frame["topic_text"].fillna(""),
            "3日净流入(亿)": frame.get("net_inflow_3d", pd.Series(index=frame.index, dtype=float)) / 100_000_000,
            "5日净流入(亿)": frame.get("net_inflow_5d", pd.Series(index=frame.index, dtype=float)) / 100_000_000,
            "10日净流入(亿)": frame.get("net_inflow_10d", pd.Series(index=frame.index, dtype=float)) / 100_000_000,
            "估算市盈率（PE）": frame.get("pe_est", pd.Series(index=frame.index, dtype=float)),
            "估算市净率（PB）": frame.get("pb_est", pd.Series(index=frame.index, dtype=float)),
            "基本面分": frame.get("fundamental_score", pd.Series(index=frame.index, dtype=float)),
            "买入逻辑": frame["reasons"].map(lambda values: "；".join(values) if isinstance(values, list) else str(values)),
            "卖出条件": frame["sell_triggers"].map(lambda values: "；".join(values) if isinstance(values, list) else str(values)),
        }
    )
    return out
