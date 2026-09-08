from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from .auth import auth_enabled, record_user_query
from .commercial import data_source_health_frame
from .data import (
    DataSourceError,
    fetch_daily_history,
    fetch_spot,
    load_cached_spot_snapshot,
    plain_code,
    prefixed_symbol,
)
from .health import assess_market_data_status, assess_recommendation_freshness
from .indicators import add_indicators
from .position import analyze_position
from .product import (
    ALERT_TYPES,
    CONDITION_LABELS,
    build_paper_trade_candidates,
    build_market_map,
    enrich_recommendations_for_product,
    evaluate_alert_rules,
    filter_by_conditions,
    paper_trade_summary,
    research_brief,
)
from .review import outcome_summary
from .storage import (
    deactivate_watchlist_position,
    get_or_create_paper_account,
    load_alert_events,
    load_alert_rules,
    load_capital_hotspots,
    load_capital_hotspot_timeline,
    load_daily_review_reports,
    load_latest_market_snapshot,
    load_limit_up_ladder,
    load_position_plan_snapshots,
    load_paper_accounts,
    load_paper_trades,
    load_recommendation_outcomes,
    load_recommendations,
    load_recommendations_for_review,
    load_runs,
    load_sentiment_history,
    load_watchlist_positions,
    save_alert_events,
    save_alert_rule,
    save_paper_trades,
    save_position_plan_snapshot,
    save_watchlist_position,
    update_alert_rule_enabled,
)
from .strategy import SignalReport, analyze_stock


HORIZON_LABELS = {"short": "短期", "mid": "中期", "long": "长期"}
SYMBOL_PATTERN = re.compile(r"^(?:SH|SZ|BJ)?\d{6}$", re.IGNORECASE)


class MiniappServiceError(RuntimeError):
    def __init__(self, message: str, code: str = "business_error", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or pd.isna(value):
            return default
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    number = _number(value)
    return int(number) if number is not None else default


def _list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None:
        return []
    try:
        if pd.isna(value):
            return []
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    separator = "、" if "、" in text else ";"
    return [item.strip() for item in text.split(separator) if item.strip()]


def json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (date, datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return json_safe(value.item())
        except Exception:
            pass
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _records(frame: pd.DataFrame, limit: int | None = None) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    selected = frame.head(limit) if limit is not None else frame
    return [json_safe(row) for row in selected.to_dict("records")]


def _record_activity(
    user: dict[str, Any],
    query_type: str,
    symbol: str | None = None,
    name: str | None = None,
    params: dict[str, Any] | None = None,
) -> None:
    if not auth_enabled() or int(user.get("id") or 0) <= 0:
        return
    try:
        record_user_query(
            int(user["id"]),
            query_type,
            stock_symbol=symbol,
            stock_name=name,
            request_params=params,
        )
    except Exception:
        pass


def _hydrate(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame.copy()
    for column in ("topics", "reasons", "sell_triggers", "warnings"):
        if column in result.columns:
            result[column] = result[column].map(_list_value)
    result["topic_text"] = (
        result["topics"].map(lambda values: "、".join(values))
        if "topics" in result.columns
        else ""
    )
    return result


def latest_recommendation_context() -> tuple[dict[str, Any] | None, pd.DataFrame]:
    runs = load_runs(limit=30)
    if runs.empty:
        return None, pd.DataFrame()
    selected_run: dict[str, Any] | None = None
    recommendations = pd.DataFrame()
    for _, row in runs.iterrows():
        if str(row.get("status") or "") != "done":
            continue
        candidate = load_recommendations(run_id=int(row["id"]))
        if not candidate.empty:
            selected_run = row.to_dict()
            recommendations = candidate
            break
    if selected_run is None:
        selected_run = runs.iloc[0].to_dict()
        recommendations = load_recommendations(run_id=int(selected_run["id"]))
    recommendations = _hydrate(recommendations)
    if recommendations.empty:
        return selected_run, recommendations
    try:
        outcomes = load_recommendation_outcomes()
    except Exception:
        outcomes = pd.DataFrame()
    sentiment = load_sentiment_history(limit=30)
    return selected_run, enrich_recommendations_for_product(recommendations, outcomes, sentiment)


def _freshness(run: dict[str, Any] | None, recommendations: pd.DataFrame) -> dict[str, Any]:
    trade_date: Any = None
    if not recommendations.empty and "trade_date" in recommendations.columns:
        trade_date = recommendations.iloc[0].get("trade_date")
    elif run:
        trade_date = run.get("trade_date")
    status = assess_recommendation_freshness(trade_date)
    return {
        "level": status.level,
        "data_date": status.data_date.isoformat() if status.data_date else None,
        "calendar_days": status.calendar_days,
        "weekdays_elapsed": status.weekdays_elapsed,
        "message": status.message,
    }


def _recommendation_record(row: pd.Series | dict[str, Any], include_detail: bool = True) -> dict[str, Any]:
    item = dict(row)
    credibility = {
        "score": round(_number(item.get("credibility_score"), 50.0) or 50.0, 1),
        "label": str(item.get("credibility_label") or "样本积累"),
        "sample_count": _integer(item.get("credibility_sample_count")),
        "win_rate": _number(item.get("history_win_rate")),
        "t1_return": _number(item.get("avg_t1_return")),
        "t3_return": _number(item.get("avg_t3_return")),
        "t5_return": _number(item.get("avg_t5_return")),
        "t20_return": _number(item.get("avg_t20_return")),
        "max_drawdown": _number(item.get("worst_max_drawdown")),
        "stop_rate": _number(item.get("stop_trigger_rate")),
        "target_rate": _number(item.get("target_touch_rate")),
    }
    result: dict[str, Any] = {
        "id": _integer(item.get("id")),
        "trade_date": str(item.get("trade_date") or ""),
        "symbol": str(item.get("symbol") or "").upper(),
        "name": str(item.get("name") or ""),
        "horizon": str(item.get("horizon") or ""),
        "horizon_label": HORIZON_LABELS.get(str(item.get("horizon") or ""), "观察"),
        "priority": str(item.get("priority") or "观察"),
        "strategy_type": str(item.get("strategy_type") or "综合观察"),
        "industry": str(item.get("industry") or "待补充"),
        "topics": _list_value(item.get("topics")),
        "score": round(_number(item.get("score"), 0.0) or 0.0, 1),
        "rating": str(item.get("rating") or "观察"),
        "action": str(item.get("action") or "等待条件确认"),
        "close": _number(item.get("close")),
        "buy_zone_low": _number(item.get("buy_zone_low")),
        "buy_zone_high": _number(item.get("buy_zone_high")),
        "stop_loss": _number(item.get("stop_loss")),
        "take_profit_1": _number(item.get("take_profit_1")),
        "take_profit_2": _number(item.get("take_profit_2")),
        "position_pct": _number(item.get("position_pct")),
        "credibility": credibility,
    }
    if include_detail:
        result.update(
            {
                "reasons": _list_value(item.get("reasons")),
                "sell_triggers": _list_value(item.get("sell_triggers")),
                "warnings": _list_value(item.get("warnings")),
                "net_inflow_3d": _number(item.get("net_inflow_3d")),
                "net_inflow_5d": _number(item.get("net_inflow_5d")),
                "net_inflow_10d": _number(item.get("net_inflow_10d")),
                "pe": _number(item.get("pe_est")),
                "pb": _number(item.get("pb_est")),
            }
        )
    return json_safe(result)


def _today_action_items(
    owner_key: str,
    run: dict[str, Any] | None,
    recommendations: pd.DataFrame,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    freshness = _freshness(run, recommendations)
    if freshness["level"] in {"missing", "invalid", "aging", "stale"}:
        actions.append(
            {
                "id": "data-freshness",
                "level": "risk" if freshness["level"] in {"missing", "invalid", "stale"} else "warning",
                "title": "先确认数据时效",
                "detail": freshness["message"],
                "target": "data-status",
            }
        )

    positions = load_watchlist_positions(owner_key)
    plan_snapshots = load_position_plan_snapshots(owner_key, limit=300)
    today_text = date.today().isoformat()
    planned_ids: set[int] = set()
    if not plan_snapshots.empty and "trade_date" in plan_snapshots.columns:
        today_plans = plan_snapshots[plan_snapshots["trade_date"].astype(str) == today_text]
        if "position_id" in today_plans.columns:
            planned_ids = set(
                pd.to_numeric(today_plans["position_id"], errors="coerce")
                .dropna()
                .astype(int)
                .tolist()
            )
    if not positions.empty:
        missing = positions[~pd.to_numeric(positions["id"], errors="coerce").fillna(0).astype(int).isin(planned_ids)]
        if not missing.empty:
            names = "、".join(missing["name"].fillna(missing["symbol"]).astype(str).head(3))
            actions.append(
                {
                    "id": "position-plan",
                    "level": "warning",
                    "title": f"{len(missing)}只持仓待生成今日方案",
                    "detail": f"{names}{'等' if len(missing) > 3 else ''}尚未形成今日低吸、高抛和止损路径。",
                    "target": "positions",
                }
            )

    events = load_alert_events(owner_key, limit=100)
    today_events = (
        events[events["trade_date"].astype(str) == today_text]
        if not events.empty and "trade_date" in events.columns
        else pd.DataFrame()
    )
    risk_events = (
        today_events[today_events["severity"].astype(str) == "风险"]
        if not today_events.empty and "severity" in today_events.columns
        else pd.DataFrame()
    )
    if not risk_events.empty:
        actions.append(
            {
                "id": "risk-alerts",
                "level": "risk",
                "title": f"今日有{len(risk_events)}条风险预警",
                "detail": str(risk_events.iloc[0].get("message") or "请检查止损与情绪退潮信号。"),
                "target": "alerts",
            }
        )

    rules = load_alert_rules(owner_key, enabled_only=True)
    if rules.empty:
        actions.append(
            {
                "id": "setup-alerts",
                "level": "info",
                "title": "尚未启用预警保护",
                "detail": "可一键为推荐池、持仓和市场情绪建立预警规则。",
                "target": "alerts",
            }
        )

    if not recommendations.empty:
        focus = recommendations[
            recommendations.get("priority", pd.Series(index=recommendations.index, dtype=str))
            .fillna("")
            .astype(str)
            .eq("重点")
        ]
        if not focus.empty:
            top = focus.sort_values("score", ascending=False).iloc[0]
            actions.append(
                {
                    "id": "focus-recommendations",
                    "level": "opportunity",
                    "title": f"重点候选{len(focus)}只",
                    "detail": f"优先复核{top.get('name') or top.get('symbol')}的买点、止损和失效条件。",
                    "target": "research",
                }
            )
    if not actions:
        actions.append(
            {
                "id": "all-clear",
                "level": "success",
                "title": "今日检查项已完成",
                "detail": "继续按买点、止损和仓位纪律观察，不因单一信号交易。",
                "target": "home",
            }
        )
    return json_safe(actions[:6])


def get_home(user: dict[str, Any], limit: int = 12) -> dict[str, Any]:
    run, recommendations = latest_recommendation_context()
    user_id = int(user.get("id") or 0)
    owner_key = f"user:{user_id}" if user_id else "local"
    breadth, _, global_summary = load_latest_market_snapshot()
    sorted_frame = recommendations.copy()
    if not sorted_frame.empty:
        sorted_frame["_cred"] = pd.to_numeric(
            sorted_frame.get("credibility_score"), errors="coerce"
        ).fillna(50)
        sorted_frame["_score"] = pd.to_numeric(sorted_frame.get("score"), errors="coerce").fillna(0)
        sorted_frame = sorted_frame.sort_values(["priority", "_cred", "_score"], ascending=[True, False, False])
    summary = {
        "total": len(recommendations),
        "priority": int((recommendations.get("priority") == "重点").sum()) if not recommendations.empty else 0,
        "short": int((recommendations.get("horizon") == "short").sum()) if not recommendations.empty else 0,
        "mid": int((recommendations.get("horizon") == "mid").sum()) if not recommendations.empty else 0,
        "long": int((recommendations.get("horizon") == "long").sum()) if not recommendations.empty else 0,
    }
    sentiment = load_sentiment_history(limit=1)
    sentiment_record = _records(sentiment, 1)
    _record_activity(user, "miniapp_home")
    return {
        "run": {
            "id": _integer((run or {}).get("id")),
            "run_time": json_safe((run or {}).get("run_time")),
            "strategy_version": str((run or {}).get("strategy_version") or ""),
        },
        "freshness": _freshness(run, recommendations),
        "summary": summary,
        "market": json_safe({"breadth": breadth, "global": global_summary}),
        "sentiment": sentiment_record[0] if sentiment_record else None,
        "actions": _today_action_items(owner_key, run, recommendations),
        "recommendations": [
            _recommendation_record(row, include_detail=False)
            for _, row in sorted_frame.head(max(1, min(50, limit))).iterrows()
        ],
    }


def list_recommendations(
    user: dict[str, Any],
    horizon: str = "",
    keyword: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    run, frame = latest_recommendation_context()
    if horizon in HORIZON_LABELS:
        frame = frame[frame["horizon"].astype(str) == horizon]
    query = keyword.strip().lower()
    if query and not frame.empty:
        search = (
            frame.get("symbol", pd.Series(index=frame.index, dtype=str)).fillna("").astype(str)
            + " "
            + frame.get("name", pd.Series(index=frame.index, dtype=str)).fillna("").astype(str)
            + " "
            + frame.get("industry", pd.Series(index=frame.index, dtype=str)).fillna("").astype(str)
            + " "
            + frame.get("topic_text", pd.Series(index=frame.index, dtype=str)).fillna("").astype(str)
        ).str.lower()
        frame = frame[search.str.contains(query, regex=False)]
    if not frame.empty:
        frame = frame.assign(
            _cred=pd.to_numeric(frame.get("credibility_score"), errors="coerce").fillna(50),
            _score=pd.to_numeric(frame.get("score"), errors="coerce").fillna(0),
        ).sort_values(["_cred", "_score"], ascending=False)
    page = max(1, page)
    page_size = max(1, min(50, page_size))
    start = (page - 1) * page_size
    selected = frame.iloc[start : start + page_size]
    _record_activity(user, "miniapp_recommendations", params={"horizon": horizon, "keyword": keyword})
    return {
        "freshness": _freshness(run, frame),
        "page": page,
        "page_size": page_size,
        "total": len(frame),
        "items": [_recommendation_record(row) for _, row in selected.iterrows()],
    }


def screen_recommendations(user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    run, recommendations = latest_recommendation_context()
    requested = payload.get("conditions") or []
    if not isinstance(requested, list):
        raise MiniappServiceError("筛选条件格式无效", "invalid_screener")
    conditions = [str(item) for item in requested if str(item) in CONDITION_LABELS]
    min_score = _number(payload.get("min_score"), 70.0) or 70.0
    max_position = _number(payload.get("max_position"), 0.35) or 0.35
    min_price = _number(payload.get("min_price"))
    max_price = _number(payload.get("max_price"))
    random_pick = payload.get("random_pick") is True
    random_count = max(1, min(50, _integer(payload.get("random_count"), 10)))
    if not 50 <= min_score <= 95:
        raise MiniappServiceError("最低评分应在50至95分之间", "invalid_screener")
    if not 0.05 <= max_position <= 0.6:
        raise MiniappServiceError("最高建议仓位应在5%至60%之间", "invalid_screener")
    if min_price is not None and not 0 < min_price <= 100000:
        raise MiniappServiceError("最低股价必须大于0元", "invalid_screener")
    if max_price is not None and not 0 < max_price <= 100000:
        raise MiniappServiceError("最高股价必须大于0元", "invalid_screener")
    if min_price is not None and max_price is not None and max_price < min_price:
        raise MiniappServiceError("最高股价不能低于最低股价", "invalid_screener")
    filtered = filter_by_conditions(
        recommendations,
        conditions,
        min_score=min_score,
        max_risk_position=max_position,
        keyword=str(payload.get("keyword") or "").strip(),
    )
    if not filtered.empty and (min_price is not None or max_price is not None):
        close = pd.to_numeric(filtered.get("close"), errors="coerce")
        price_mask = close.notna() & (close > 0)
        if min_price is not None:
            price_mask &= close >= min_price
        if max_price is not None:
            price_mask &= close <= max_price
        filtered = filtered[price_mask].reset_index(drop=True)
    if random_pick and not filtered.empty:
        filtered = filtered.sample(
            n=min(random_count, len(filtered)),
            random_state=int(date.today().strftime("%Y%m%d")),
        ).reset_index(drop=True)
    _record_activity(
        user,
        "miniapp_condition_screener",
        params={
            "conditions": conditions,
            "min_score": min_score,
            "max_position": max_position,
            "min_price": min_price,
            "max_price": max_price,
            "random_pick": random_pick,
            "random_count": random_count if random_pick else None,
            "result_count": len(filtered),
        },
    )
    return {
        "conditions": CONDITION_LABELS,
        "freshness": _freshness(run, recommendations),
        "pool_count": len(recommendations),
        "result_count": len(filtered),
        "price_range": {"min": min_price, "max": max_price},
        "random_pick": random_pick,
        "items": [_recommendation_record(row) for _, row in filtered.head(50).iterrows()],
    }


def _lookup_sources(owner_key: str) -> pd.DataFrame:
    _, recommendations = latest_recommendation_context()
    positions = load_watchlist_positions(owner_key)
    cached_spot = load_cached_spot_snapshot()
    frames: list[pd.DataFrame] = []
    for frame in (recommendations, positions, cached_spot):
        if frame is None or frame.empty or "symbol" not in frame.columns:
            continue
        selected = frame.copy()
        if "name" not in selected.columns:
            selected["name"] = ""
        frames.append(selected[["symbol", "name"]])
    if not frames:
        return pd.DataFrame(columns=["symbol", "name"])
    return pd.concat(frames, ignore_index=True).drop_duplicates("symbol")


def resolve_stock(query: str, owner_key: str) -> tuple[str, str]:
    raw = str(query or "").strip()
    if not raw:
        raise MiniappServiceError("请输入股票名称或6位代码", "invalid_stock")
    digits = "".join(character for character in raw if character.isdigit())
    sources = _lookup_sources(owner_key)
    if len(digits) >= 6:
        symbol = prefixed_symbol(digits[-6:]).upper()
        matched = sources[sources["symbol"].astype(str).map(plain_code) == plain_code(symbol)]
        name = str(matched.iloc[0].get("name") or "") if not matched.empty else ""
        return symbol, name
    matched = sources[
        sources["name"].fillna("").astype(str).str.contains(raw, case=False, regex=False)
        | sources["symbol"].fillna("").astype(str).str.contains(raw.upper(), case=False, regex=False)
    ]
    if matched.empty:
        raise MiniappServiceError("没有找到该股票，请改用6位股票代码", "stock_not_found", 404)
    row = matched.iloc[0]
    return str(row["symbol"]).upper(), str(row.get("name") or "")


def search_stocks(user: dict[str, Any], owner_key: str, keyword: str, limit: int = 20) -> dict[str, Any]:
    query = keyword.strip()
    if not query:
        return {"items": []}
    sources = _lookup_sources(owner_key)
    digits = "".join(character for character in query if character.isdigit())
    if len(digits) >= 6:
        mask = sources["symbol"].astype(str).map(plain_code) == digits[-6:]
    else:
        mask = (
            sources["name"].fillna("").astype(str).str.contains(query, case=False, regex=False)
            | sources["symbol"].fillna("").astype(str).str.contains(query.upper(), case=False, regex=False)
        )
    items = sources.loc[mask].head(max(1, min(50, limit))).copy()
    items["code"] = items["symbol"].astype(str).map(plain_code)
    _record_activity(user, "miniapp_stock_search", params={"keyword": query})
    return {"items": _records(items)}


def _find_recommendation(symbol: str) -> tuple[pd.DataFrame, pd.Series | None]:
    _, latest = latest_recommendation_context()
    if not latest.empty:
        matched = latest[latest["symbol"].astype(str).map(plain_code) == plain_code(symbol)]
        if not matched.empty:
            return latest, matched.sort_values("score", ascending=False).iloc[0]
    history = _hydrate(load_recommendations_for_review())
    if not history.empty:
        matched = history[history["symbol"].astype(str).map(plain_code) == plain_code(symbol)]
        if not matched.empty:
            return latest, matched.iloc[0]
    return latest, None


def _kline_records(history: pd.DataFrame, limit: int) -> list[dict[str, Any]]:
    if history.empty:
        return []
    frame = add_indicators(history).tail(limit)
    columns = [
        column
        for column in (
            "date",
            "open",
            "close",
            "high",
            "low",
            "volume",
            "turnover",
            "ma5",
            "ma20",
            "ma60",
            "macd",
            "macd_signal",
            "macd_hist",
        )
        if column in frame.columns
    ]
    return _records(frame[columns])


def get_stock_detail(
    user: dict[str, Any],
    owner_key: str,
    symbol_or_query: str,
    days: int = 120,
) -> dict[str, Any]:
    symbol, resolved_name = resolve_stock(symbol_or_query, owner_key)
    all_recommendations, recommendation = _find_recommendation(symbol)
    name = resolved_name or (str(recommendation.get("name") or "") if recommendation is not None else "")
    start = date.today() - timedelta(days=max(220, min(900, days * 3)))
    try:
        history = fetch_daily_history(symbol, start_date=start, adjust="qfq")
    except DataSourceError as exc:
        raise MiniappServiceError(str(exc), "market_data_unavailable", 503) from exc
    report = analyze_stock(history, symbol=symbol, name=name)
    if recommendation is not None:
        recommendation_data = _recommendation_record(recommendation)
        peers = all_recommendations[
            (all_recommendations.get("industry", pd.Series(index=all_recommendations.index, dtype=str)).astype(str)
             == str(recommendation.get("industry") or ""))
            & (all_recommendations["symbol"].astype(str).map(plain_code) != plain_code(symbol))
        ]
        alternatives = [
            f"{row.get('name')}({row.get('symbol')})"
            for _, row in peers.sort_values("score", ascending=False).head(3).iterrows()
        ]
        brief = research_brief(recommendation, alternatives=alternatives)
    else:
        recommendation_data = None
        brief = {
            "入选逻辑": "该股票不在最新推荐池，本页仅按当前K线生成技术观察结果。",
            "最大风险": "缺少推荐池的资金、估值和复盘可信度验证，不能只依据K线操作。",
            "同板块替代": "暂无同板块替代数据。",
            "操作倾向": report.action,
            "外部环境": "请同时查看市场温度、行业资金和情绪周期。",
            "合规提示": "结果仅用于概率分析，不承诺收益。",
        }
    _record_activity(user, "stock_analysis", symbol=symbol, name=name, params={"source": "miniapp"})
    return {
        "symbol": symbol,
        "name": name or symbol,
        "source_warning": str(history.attrs.get("source_warning") or ""),
        "report": {
            "date": report.date,
            "close": report.close,
            "score": report.score,
            "rating": report.rating,
            "action": report.action,
            "buy_zone_low": report.buy_zone_low,
            "buy_zone_high": report.buy_zone_high,
            "stop_loss": report.stop_loss,
            "take_profit_1": report.take_profit_1,
            "take_profit_2": report.take_profit_2,
            "position_pct": report.position_pct,
            "reasons": report.reasons,
            "sell_triggers": report.sell_triggers,
            "warnings": report.warnings,
        },
        "recommendation": recommendation_data,
        "research": brief,
        "kline": _kline_records(history, max(30, min(240, days))),
    }


def get_market(user: dict[str, Any]) -> dict[str, Any]:
    run, recommendations = latest_recommendation_context()
    breadth, global_indices, global_summary = load_latest_market_snapshot()
    hotspots = load_capital_hotspots(run_id=_integer((run or {}).get("id")) or None)
    sentiment = load_sentiment_history(limit=30)
    ladder = load_limit_up_ladder()
    timeline = load_capital_hotspot_timeline(limit=1000)
    market_map = build_market_map(recommendations, hotspots, ladder, timeline)
    _record_activity(user, "miniapp_market")
    return {
        "freshness": _freshness(run, recommendations),
        "breadth": json_safe(breadth),
        "global": json_safe(global_summary),
        "global_indices": _records(global_indices, 12),
        "sentiment": _records(sentiment, 30),
        "hotspots": _records(hotspots, 50),
        "market_map": _records(market_map, 40),
        "limit_up_ladder": _records(ladder, 30),
    }


def get_data_status(user: dict[str, Any]) -> dict[str, Any]:
    run, recommendations = latest_recommendation_context()
    breadth, _, _ = load_latest_market_snapshot()
    hotspots = load_capital_hotspots(run_id=_integer((run or {}).get("id")) or None)
    run_errors: list[str] = []
    if run and str(run.get("status") or "") not in {"done", "running", "已完成"}:
        run_errors.append(str(run.get("note") or f"最近任务状态：{run.get('status') or '未知'}"))
    board = data_source_health_frame(
        run,
        breadth,
        errors=run_errors,
        capital_hotspots=hotspots,
        recommendations=recommendations,
    )
    items = []
    for _, row in board.iterrows():
        items.append(
            {
                "name": str(row.get("数据模块") or "数据模块"),
                "updated_at": str(row.get("最新时间") or "-"),
                "coverage": str(row.get("覆盖数量") or "-"),
                "status": str(row.get("状态") or "待确认"),
                "failure_reason": str(row.get("失败原因") or "-"),
                "fallback": str(row.get("备用方案") or "-"),
            }
        )

    cached_spot = load_cached_spot_snapshot()
    quote_date: Any = None
    if not cached_spot.empty and "date" in cached_spot.columns:
        parsed_dates = pd.to_datetime(cached_spot["date"], errors="coerce").dropna()
        quote_date = parsed_dates.max() if not parsed_dates.empty else None
    if quote_date is None:
        quote_date = cached_spot.attrs.get("quote_date")
    price_column = next(
        (column for column in ("close", "price", "latest") if column in cached_spot.columns),
        None,
    )
    valid_prices = (
        int(pd.to_numeric(cached_spot[price_column], errors="coerce").gt(0).sum())
        if price_column
        else 0
    )
    market_status = assess_market_data_status(
        quote_date,
        row_count=len(cached_spot),
        valid_price_count=valid_prices,
        source_warning=str(cached_spot.attrs.get("source_warning") or "本地缓存"),
    )
    freshness = _freshness(run, recommendations)
    ready = freshness["level"] in {"fresh"} and market_status.level in {"fresh", "cached", "degraded"}
    _record_activity(user, "miniapp_data_status")
    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "ready_for_decision": ready,
        "recommendation_freshness": freshness,
        "market": {
            "level": market_status.level,
            "quote_date": market_status.quote_date.isoformat() if market_status.quote_date else None,
            "row_count": market_status.row_count,
            "valid_price_count": market_status.valid_price_count,
            "source_label": market_status.source_label,
            "is_cached": market_status.is_cached,
            "message": market_status.message,
        },
        "items": json_safe(items),
    }


def get_review(user: dict[str, Any], owner_key: str) -> dict[str, Any]:
    try:
        outcomes = load_recommendation_outcomes()
    except Exception:
        outcomes = pd.DataFrame()
    reports = load_daily_review_reports(owner_key, limit=30)
    summary = outcome_summary(outcomes) if not outcomes.empty else pd.DataFrame()
    _record_activity(user, "miniapp_review")
    return {
        "summary": _records(summary),
        "recent_outcomes": _records(outcomes, 100),
        "daily_reports": _records(reports, 30),
    }


def get_paper_account(
    user: dict[str, Any],
    owner_key: str,
    initial_cash: float = 100_000.0,
) -> dict[str, Any]:
    initial_cash = max(10_000.0, min(10_000_000.0, float(initial_cash)))
    account = get_or_create_paper_account(owner_key=owner_key, initial_cash=initial_cash)
    account_id = int(account["id"])
    accounts = load_paper_accounts(owner_key)
    if not accounts.empty:
        matched = accounts[accounts["id"] == account_id]
        if not matched.empty:
            account = matched.iloc[0].to_dict()
    trades = load_paper_trades(owner_key, account_id=account_id, limit=300)
    curve, summary = paper_trade_summary(trades, initial_cash=float(account["initial_cash"]))
    _record_activity(user, "miniapp_paper_account")
    return {
        "account": json_safe(account),
        "summary": json_safe(summary),
        "equity_curve": _records(curve, 120),
        "trades": _records(trades, 100),
    }


def generate_paper_trades(
    user: dict[str, Any],
    owner_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    initial_cash = _number(payload.get("initial_cash"), 100_000.0) or 100_000.0
    cash_per_trade = _number(payload.get("cash_per_trade"), 10_000.0) or 10_000.0
    max_count = _integer(payload.get("max_count"), 5)
    if not 10_000 <= initial_cash <= 10_000_000:
        raise MiniappServiceError("初始模拟资金应在1万元至1000万元之间", "invalid_paper_trade")
    if not 1_000 <= cash_per_trade <= 1_000_000:
        raise MiniappServiceError("单只模拟投入应在1000元至100万元之间", "invalid_paper_trade")
    if not 1 <= max_count <= 30:
        raise MiniappServiceError("本次模拟买入数量应在1至30只之间", "invalid_paper_trade")

    account = get_or_create_paper_account(owner_key=owner_key, initial_cash=initial_cash)
    account_id = int(account["id"])
    available_cash = max(0.0, _number(account.get("cash"), 0.0) or 0.0)
    affordable_count = int(available_cash // max(cash_per_trade, 1.0))
    max_count = min(max_count, affordable_count)
    if max_count <= 0:
        raise MiniappServiceError("模拟账户可用资金不足，请先查看已有持仓记录", "paper_cash_insufficient")

    _, recommendations = latest_recommendation_context()
    try:
        outcomes = load_recommendation_outcomes()
    except Exception:
        outcomes = pd.DataFrame()
    existing = load_paper_trades(owner_key, account_id=account_id, limit=5000)
    pool = recommendations.copy()
    if not existing.empty and "recommendation_id" in existing.columns and "id" in pool.columns:
        used = set(
            pd.to_numeric(existing["recommendation_id"], errors="coerce")
            .dropna()
            .astype(int)
            .tolist()
        )
        recommendation_ids = pd.to_numeric(pool["id"], errors="coerce").fillna(0).astype(int)
        pool = pool[~recommendation_ids.isin(used)]
    candidates = build_paper_trade_candidates(
        pool,
        outcomes,
        account_id=account_id,
        owner_key=owner_key,
        cash_per_trade=float(cash_per_trade),
        max_count=max_count,
    )
    accepted: list[dict[str, Any]] = []
    remaining = available_cash
    for item in candidates:
        required = float(item.get("amount") or 0) + float(item.get("fee") or 0)
        if required <= remaining:
            accepted.append(item)
            remaining -= required
    save_paper_trades(accepted)
    _record_activity(
        user,
        "miniapp_paper_generate",
        params={"created": len(accepted), "cash_per_trade": cash_per_trade},
    )
    result = get_paper_account(user, owner_key, initial_cash=float(initial_cash))
    result["created"] = len(accepted)
    result["message"] = (
        f"已生成{len(accepted)}笔模拟交易"
        if accepted
        else "没有新的可模拟候选，可能当前推荐已生成过"
    )
    return result


def list_positions(user: dict[str, Any], owner_key: str) -> dict[str, Any]:
    positions = load_watchlist_positions(owner_key)
    snapshots = load_position_plan_snapshots(owner_key, limit=100)
    latest_by_position: dict[int, dict[str, Any]] = {}
    if not snapshots.empty:
        for _, row in snapshots.iterrows():
            position_id = _integer(row.get("position_id"))
            if position_id and position_id not in latest_by_position:
                latest_by_position[position_id] = json_safe(row.to_dict())
    items = []
    for _, row in positions.iterrows():
        item = json_safe(row.to_dict())
        item["latest_plan"] = latest_by_position.get(_integer(row.get("id")))
        items.append(item)
    _record_activity(user, "miniapp_positions")
    return {"items": items}


def save_position(user: dict[str, Any], owner_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    symbol, resolved_name = resolve_stock(str(payload.get("symbol") or payload.get("query") or ""), owner_key)
    shares = _integer(payload.get("shares"))
    sellable = _integer(payload.get("sellable_shares"), shares)
    cost_price = _number(payload.get("cost_price"), 0.0) or 0.0
    t_ratio = _number(payload.get("t_ratio"), 0.2) or 0.2
    if cost_price <= 0:
        raise MiniappServiceError("持仓成本必须大于0", "invalid_position")
    if shares < 100 or shares % 100 != 0:
        raise MiniappServiceError("持股数量必须是100股的整数倍", "invalid_position")
    if sellable < 0 or sellable > shares or sellable % 100 != 0:
        raise MiniappServiceError("可卖底仓必须是100股的整数倍且不能超过持股数量", "invalid_position")
    if not 0.1 <= t_ratio <= 0.5:
        raise MiniappServiceError("做T比例必须在10%至50%之间", "invalid_position")
    item = {
        "owner_key": owner_key,
        "symbol": symbol,
        "name": str(payload.get("name") or resolved_name or symbol),
        "cost_price": cost_price,
        "shares": shares,
        "available_cash": max(0.0, _number(payload.get("available_cash"), 0.0) or 0.0),
        "account_assets": max(0.0, _number(payload.get("account_assets"), 0.0) or 0.0),
        "sellable_shares": sellable,
        "t_ratio": t_ratio,
        "t_preference": str(payload.get("t_preference") or "自动判断"),
        "target_break_even": cost_price,
        "note": str(payload.get("note") or ""),
    }
    position_id = save_watchlist_position(item)
    _record_activity(user, "watchlist_position_save", symbol=symbol, name=item["name"])
    return {"id": position_id, "symbol": symbol, "name": item["name"]}


def remove_position(user: dict[str, Any], owner_key: str, position_id: int) -> dict[str, Any]:
    positions = load_watchlist_positions(owner_key)
    matched = positions[positions["id"] == position_id] if not positions.empty else pd.DataFrame()
    if matched.empty:
        raise MiniappServiceError("持仓记录不存在", "position_not_found", 404)
    row = matched.iloc[0]
    deactivate_watchlist_position(position_id, owner_key)
    _record_activity(user, "watchlist_position_remove", str(row.get("symbol") or ""), str(row.get("name") or ""))
    return {"removed": True}


def _fallback_position_inputs(
    position: dict[str, Any],
    recommendations: pd.DataFrame,
    reason: str,
) -> tuple[pd.DataFrame, SignalReport]:
    symbol = str(position.get("symbol") or "").upper()
    matched = (
        recommendations[recommendations["symbol"].astype(str).map(plain_code) == plain_code(symbol)]
        if not recommendations.empty
        else pd.DataFrame()
    )
    rec = matched.iloc[0] if not matched.empty else None
    cost = max(0.01, _number(position.get("cost_price"), 0.01) or 0.01)
    close = _number(rec.get("close"), cost) if rec is not None else cost
    close = max(0.01, close or cost)
    score = _integer(rec.get("score"), 45) if rec is not None else 45
    periods = 120
    dates = pd.bdate_range(end=pd.Timestamp(date.today()), periods=periods)
    values = []
    start_price = close * 0.95
    for index in range(periods):
        progress = index / (periods - 1)
        wave = close * (0.015 * math.sin(index / 5) + 0.008 * math.sin(index / 13))
        values.append(max(0.01, start_price + (close - start_price) * progress + wave))
    values[-1] = close
    history = pd.DataFrame(
        {
            "date": dates,
            "open": [value * 0.997 for value in values],
            "close": values,
            "high": [value * 1.023 for value in values],
            "low": [value * 0.977 for value in values],
            "volume": [1_000_000 + index * 3000 for index in range(periods)],
            "turnover": [value * 100_000_000 for value in values],
        }
    )
    report = SignalReport(
        symbol=symbol,
        name=str(position.get("name") or (rec.get("name") if rec is not None else "")),
        date=date.today().isoformat(),
        close=close,
        score=max(0, min(100, score)),
        rating="备用数据评估",
        action="等待实时行情恢复后复核",
        buy_zone_low=_number(rec.get("buy_zone_low"), close * 0.98) if rec is not None else close * 0.98,
        buy_zone_high=_number(rec.get("buy_zone_high"), close * 1.01) if rec is not None else close * 1.01,
        stop_loss=_number(rec.get("stop_loss"), close * 0.94) if rec is not None else close * 0.94,
        take_profit_1=_number(rec.get("take_profit_1"), close * 1.06) if rec is not None else close * 1.06,
        take_profit_2=_number(rec.get("take_profit_2"), close * 1.10) if rec is not None else close * 1.10,
        trailing_stop=_number(rec.get("trailing_stop"), close * 0.96) if rec is not None else close * 0.96,
        position_pct=0.0,
        reasons=["使用本地推荐记录生成备用观察方案"],
        sell_triggers=["实时行情恢复后必须重新生成方案"],
        warnings=[f"当前不是实时行情：{reason}"],
    )
    return history, report


def generate_position_plan(
    user: dict[str, Any],
    owner_key: str,
    position_id: int,
) -> dict[str, Any]:
    positions = load_watchlist_positions(owner_key)
    matched = positions[positions["id"] == position_id] if not positions.empty else pd.DataFrame()
    if matched.empty:
        raise MiniappServiceError("持仓记录不存在", "position_not_found", 404)
    position = matched.iloc[0].to_dict()
    run, recommendations = latest_recommendation_context()
    data_warning = ""
    try:
        history = fetch_daily_history(
            str(position["symbol"]),
            start_date=date.today() - timedelta(days=820),
            adjust="qfq",
        )
        report = analyze_stock(
            history,
            symbol=str(position["symbol"]),
            name=str(position.get("name") or ""),
        )
    except Exception as exc:
        data_warning = str(exc)
        history, report = _fallback_position_inputs(position, recommendations, data_warning)
    stock_context = (
        recommendations[
            recommendations["symbol"].astype(str).map(plain_code) == plain_code(str(position["symbol"]))
        ]
        if not recommendations.empty
        else pd.DataFrame()
    )
    industry = str(stock_context.iloc[0].get("industry") or "") if not stock_context.empty else ""
    topics = _list_value(stock_context.iloc[0].get("topics")) if not stock_context.empty else []
    breadth, _, global_summary = load_latest_market_snapshot()
    hotspots = load_capital_hotspots(run_id=_integer((run or {}).get("id")) or None)
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
        capital_hotspots=hotspots,
        industry=industry,
        topics=topics,
        news=pd.DataFrame(),
    )
    if data_warning:
        plan.risks.insert(0, "当前使用备用数据，实时行情恢复后请重新生成。")
        plan.action_summary = f"{plan.action_summary}（备用数据观察方案）"
    payload = plan.as_dict()
    save_position_plan_snapshot(
        {
            "owner_key": owner_key,
            "position_id": position_id,
            "trade_date": date.today().isoformat(),
            "symbol": position["symbol"],
            "name": position.get("name") or "",
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
            "plan_json": payload,
        }
    )
    _record_activity(user, "miniapp_position_plan", str(position["symbol"]), str(position.get("name") or ""))
    return {"position_id": position_id, "is_fallback": bool(data_warning), "plan": json_safe(payload)}


def create_quick_alerts(
    user: dict[str, Any],
    owner_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    scope = str(payload.get("scope") or "").strip()
    if scope not in {"recommendations", "positions", "market"}:
        raise MiniappServiceError("不支持的一键预警范围", "invalid_alert_scope")
    limit = max(1, min(10, _integer(payload.get("limit"), 5)))
    rules = load_alert_rules(owner_key)
    existing: set[tuple[str, str]] = set()
    if not rules.empty:
        for _, row in rules.iterrows():
            symbol = str(row.get("symbol") or "").upper()
            existing.add((plain_code(symbol) if symbol else "", str(row.get("alert_type") or "")))

    created: list[dict[str, Any]] = []

    def add_rule(item: dict[str, Any]) -> None:
        symbol = str(item.get("symbol") or "").upper()
        alert_type = str(item.get("alert_type") or "")
        key = (plain_code(symbol) if symbol else "", alert_type)
        if key in existing:
            return
        rule_id = save_alert_rule({"owner_key": owner_key, **item})
        existing.add(key)
        created.append({"id": rule_id, "symbol": symbol, "alert_type": alert_type})

    _, recommendations = latest_recommendation_context()
    if scope == "recommendations":
        selected = recommendations.sort_values("score", ascending=False).head(limit)
        for _, row in selected.iterrows():
            common = {
                "symbol": str(row.get("symbol") or "").upper(),
                "name": str(row.get("name") or ""),
                "enabled": True,
            }
            add_rule(
                {
                    **common,
                    "alert_type": "到达买点",
                    "comparator": "进入区间",
                    "threshold_value": _number(row.get("buy_zone_high")),
                    "note": "小程序一键生成：推荐候选进入买点区间",
                }
            )
            add_rule(
                {
                    **common,
                    "alert_type": "跌破止损",
                    "comparator": "<=",
                    "threshold_value": _number(row.get("stop_loss")),
                    "note": "小程序一键生成：推荐候选跌破失效点",
                }
            )
    elif scope == "positions":
        positions = load_watchlist_positions(owner_key).head(limit)
        for _, position in positions.iterrows():
            symbol = str(position.get("symbol") or "").upper()
            matched = (
                recommendations[
                    recommendations["symbol"].astype(str).map(plain_code) == plain_code(symbol)
                ]
                if not recommendations.empty
                else pd.DataFrame()
            )
            stop = (
                _number(matched.iloc[0].get("stop_loss"))
                if not matched.empty
                else (_number(position.get("cost_price"), 0.0) or 0.0) * 0.92
            )
            add_rule(
                {
                    "symbol": symbol,
                    "name": str(position.get("name") or ""),
                    "alert_type": "跌破止损",
                    "comparator": "<=",
                    "threshold_value": stop,
                    "enabled": True,
                    "note": "小程序一键生成：持仓风险保护",
                }
            )
    else:
        for alert_type, note in (
            ("板块主线升温", "市场主线资金升温提醒"),
            ("龙头切换", "评分靠前龙头候选变化提醒"),
            ("情绪退潮", "炸板率或情绪阶段转弱提醒"),
        ):
            add_rule(
                {
                    "symbol": "",
                    "name": "",
                    "alert_type": alert_type,
                    "comparator": ">=",
                    "threshold_value": None,
                    "enabled": True,
                    "note": f"小程序一键生成：{note}",
                }
            )

    _record_activity(user, "miniapp_alert_quick_create", params={"scope": scope, "created": len(created)})
    return {"created": len(created), "items": created}


def _alert_rule_key(row: pd.Series | dict[str, Any]) -> tuple[str, str]:
    symbol = str(row.get("symbol") or "").upper()
    return (plain_code(symbol) if symbol else "", str(row.get("alert_type") or ""))


def _dedupe_alert_rules(rules: pd.DataFrame) -> pd.DataFrame:
    if rules.empty:
        return rules
    frame = rules.copy()
    frame["_miniapp_rule_key"] = [
        "|".join(_alert_rule_key(row)) for _, row in frame.iterrows()
    ]
    return frame.drop_duplicates("_miniapp_rule_key", keep="first").drop(
        columns=["_miniapp_rule_key"]
    )


def list_alerts(user: dict[str, Any], owner_key: str) -> dict[str, Any]:
    rules = _dedupe_alert_rules(load_alert_rules(owner_key))
    events = load_alert_events(owner_key, limit=100)
    _record_activity(user, "miniapp_alerts")
    return {"types": ALERT_TYPES, "rules": _records(rules), "events": _records(events)}


def create_alert(user: dict[str, Any], owner_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    alert_type = str(payload.get("alert_type") or "")
    if alert_type not in ALERT_TYPES:
        raise MiniappServiceError("不支持的预警类型", "invalid_alert")
    symbol = ""
    name = ""
    query = str(payload.get("symbol") or payload.get("query") or "").strip()
    if query:
        symbol, resolved_name = resolve_stock(query, owner_key)
        name = str(payload.get("name") or resolved_name or "")
    if alert_type in {"到达买点", "跌破止损", "主力资金连续流入"} and not symbol:
        raise MiniappServiceError("该预警类型必须选择股票", "invalid_alert")
    existing_rules = _dedupe_alert_rules(load_alert_rules(owner_key))
    duplicate = existing_rules[
        existing_rules.apply(
            lambda row: _alert_rule_key(row) == _alert_rule_key({"symbol": symbol, "alert_type": alert_type}),
            axis=1,
        )
    ] if not existing_rules.empty else pd.DataFrame()
    if not duplicate.empty:
        rule_id = int(duplicate.iloc[0].get("id") or 0)
        _record_activity(
            user,
            "miniapp_alert_duplicate",
            symbol,
            name,
            params={"rule_id": rule_id, "alert_type": alert_type},
        )
        return {"id": rule_id, "created": False, "duplicate": True}
    rule_id = save_alert_rule(
        {
            "owner_key": owner_key,
            "symbol": symbol,
            "name": name,
            "alert_type": alert_type,
            "comparator": str(payload.get("comparator") or ">="),
            "threshold_value": _number(payload.get("threshold_value")),
            "enabled": bool(payload.get("enabled", True)),
            "note": str(payload.get("note") or ""),
        }
    )
    _record_activity(user, "miniapp_alert_create", symbol, name)
    return {"id": rule_id, "created": True, "duplicate": False}


def toggle_alert(
    user: dict[str, Any],
    owner_key: str,
    rule_id: int,
    enabled: bool,
) -> dict[str, Any]:
    rules = load_alert_rules(owner_key)
    if rules.empty or rules[rules["id"] == rule_id].empty:
        raise MiniappServiceError("预警规则不存在", "alert_not_found", 404)
    update_alert_rule_enabled(rule_id, enabled, owner_key)
    _record_activity(user, "miniapp_alert_toggle", params={"rule_id": rule_id, "enabled": enabled})
    return {"id": rule_id, "enabled": enabled}


def evaluate_alerts(user: dict[str, Any], owner_key: str) -> dict[str, Any]:
    rules = _dedupe_alert_rules(load_alert_rules(owner_key, enabled_only=True))
    if rules.empty:
        return {"created": 0, "events": []}
    try:
        spot = fetch_spot()
    except Exception:
        spot = load_cached_spot_snapshot()
    run, recommendations = latest_recommendation_context()
    hotspots = load_capital_hotspots(run_id=_integer((run or {}).get("id")) or None)
    sentiment = load_sentiment_history(limit=30)
    events = evaluate_alert_rules(rules, spot, recommendations, hotspots, sentiment, owner_key)
    save_alert_events(events)
    _record_activity(user, "miniapp_alert_evaluate", params={"event_count": len(events)})
    return {"created": len(events), "events": json_safe(events)}
