from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .data import DataSourceError, _run_with_timeout, get_akshare, prefixed_symbol


@dataclass
class SentimentBundle:
    trade_date: str
    limit_up: pd.DataFrame
    broken: pd.DataFrame
    limit_down: pd.DataFrame
    previous: pd.DataFrame
    snapshot: dict[str, Any]
    errors: list[str]


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return default if np.isnan(result) else result
    except (TypeError, ValueError):
        return default


def _clip_score(value: float) -> float:
    return float(np.clip(value, 0, 100))


def normalize_limit_up_pool(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    frame = raw.rename(
        columns={
            "代码": "code",
            "名称": "name",
            "涨跌幅": "change_pct",
            "最新价": "price",
            "成交额": "turnover",
            "换手率": "turnover_rate",
            "封板资金": "seal_amount",
            "首次封板时间": "first_seal_time",
            "最后封板时间": "last_seal_time",
            "炸板次数": "break_count",
            "涨停统计": "limit_stat",
            "连板数": "streak",
            "所属行业": "industry",
        }
    ).copy()
    required = [
        "code",
        "name",
        "change_pct",
        "price",
        "turnover",
        "turnover_rate",
        "seal_amount",
        "first_seal_time",
        "last_seal_time",
        "break_count",
        "limit_stat",
        "streak",
        "industry",
    ]
    for column in required:
        if column not in frame.columns:
            frame[column] = np.nan
    for column in [
        "change_pct",
        "price",
        "turnover",
        "turnover_rate",
        "seal_amount",
        "break_count",
        "streak",
    ]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["symbol"] = frame["code"].map(prefixed_symbol)
    frame["streak"] = frame["streak"].fillna(1).clip(lower=1).astype(int)
    frame["break_count"] = frame["break_count"].fillna(0).astype(int)
    return frame[required + ["symbol"]].sort_values(
        ["streak", "seal_amount", "first_seal_time"],
        ascending=[False, False, True],
    ).reset_index(drop=True)


def normalize_broken_pool(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    frame = raw.rename(
        columns={
            "代码": "code",
            "名称": "name",
            "涨跌幅": "change_pct",
            "最新价": "price",
            "成交额": "turnover",
            "换手率": "turnover_rate",
            "首次封板时间": "first_seal_time",
            "炸板次数": "break_count",
            "涨停统计": "limit_stat",
            "所属行业": "industry",
        }
    ).copy()
    for column in [
        "code",
        "name",
        "change_pct",
        "price",
        "turnover",
        "turnover_rate",
        "first_seal_time",
        "break_count",
        "limit_stat",
        "industry",
    ]:
        if column not in frame.columns:
            frame[column] = np.nan
    for column in ["change_pct", "price", "turnover", "turnover_rate", "break_count"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["symbol"] = frame["code"].map(prefixed_symbol)
    return frame.reset_index(drop=True)


def normalize_previous_pool(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    frame = raw.rename(
        columns={
            "代码": "code",
            "名称": "name",
            "涨跌幅": "change_pct",
            "最新价": "price",
            "成交额": "turnover",
            "换手率": "turnover_rate",
            "昨日封板时间": "previous_seal_time",
            "昨日连板数": "previous_streak",
            "涨停统计": "limit_stat",
            "所属行业": "industry",
        }
    ).copy()
    for column in [
        "code",
        "name",
        "change_pct",
        "price",
        "turnover",
        "turnover_rate",
        "previous_seal_time",
        "previous_streak",
        "limit_stat",
        "industry",
    ]:
        if column not in frame.columns:
            frame[column] = np.nan
    for column in ["change_pct", "price", "turnover", "turnover_rate", "previous_streak"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["symbol"] = frame["code"].map(prefixed_symbol)
    return frame.reset_index(drop=True)


def classify_emotion_stage(
    score: float,
    broken_rate: float,
    previous_premium: float,
    max_streak: int,
    limit_down_count: int,
    limit_up_count: int,
    previous_score: float | None = None,
) -> str:
    score_change = score - previous_score if previous_score is not None else 0
    if score >= 75 and max_streak >= 4 and broken_rate <= 0.3:
        return "高潮"
    if (
        score < 38
        and (previous_premium < 0 or limit_down_count >= max(limit_up_count * 0.35, 10))
    ) or (previous_score is not None and score_change <= -15):
        return "退潮"
    if broken_rate >= 0.4 or (score >= 50 and previous_premium < -1):
        return "分化"
    if previous_score is not None and score_change >= 10 and score < 60:
        return "修复"
    if score >= 58:
        return "升温"
    if score < 28:
        return "冰点"
    if previous_premium >= 0 and score >= 35:
        return "修复"
    return "震荡"


def build_sentiment_snapshot(
    trade_date: str,
    limit_up: pd.DataFrame,
    broken: pd.DataFrame,
    limit_down: pd.DataFrame,
    previous: pd.DataFrame,
    breadth: dict[str, Any] | None = None,
    previous_score: float | None = None,
) -> dict[str, Any]:
    breadth = breadth or {}
    limit_up_count = len(limit_up)
    broken_count = len(broken)
    limit_down_count = len(limit_down)
    attempts = limit_up_count + broken_count
    seal_rate = limit_up_count / attempts if attempts else 0.0
    broken_rate = broken_count / attempts if attempts else 0.0

    streaks = pd.to_numeric(limit_up.get("streak"), errors="coerce").fillna(1)
    first_board_count = int(streaks.eq(1).sum())
    second_board_count = int(streaks.eq(2).sum())
    third_board_count = int(streaks.eq(3).sum())
    high_board_count = int(streaks.ge(4).sum())
    max_streak = int(streaks.max()) if not streaks.empty else 0

    previous_changes = pd.to_numeric(previous.get("change_pct"), errors="coerce").dropna()
    previous_premium = float(previous_changes.mean()) if not previous_changes.empty else 0.0
    previous_red_rate = float(previous_changes.gt(0).mean()) if not previous_changes.empty else 0.0
    previous_codes = set(previous.get("code", pd.Series(dtype=str)).astype(str))
    current_codes = set(limit_up.get("code", pd.Series(dtype=str)).astype(str))
    promotion_rate = len(previous_codes & current_codes) / len(previous_codes) if previous_codes else 0.0

    up_ratio = _number(breadth.get("up_ratio"), 0.5)
    if up_ratio > 1:
        up_ratio /= 100
    components = {
        "涨停活跃度": _clip_score(limit_up_count / 80 * 100),
        "封板质量": _clip_score(seal_rate * 100),
        "连板高度": _clip_score(max_streak / 7 * 100),
        "晋级反馈": _clip_score(promotion_rate * 100),
        "昨日溢价": _clip_score((previous_premium + 5) / 10 * 100),
        "市场宽度": _clip_score(up_ratio * 100),
        "跌停压力": _clip_score(100 - limit_down_count / 35 * 100),
    }
    score = (
        components["涨停活跃度"] * 0.16
        + components["封板质量"] * 0.20
        + components["连板高度"] * 0.15
        + components["晋级反馈"] * 0.14
        + components["昨日溢价"] * 0.14
        + components["市场宽度"] * 0.11
        + components["跌停压力"] * 0.10
    )
    stage = classify_emotion_stage(
        score,
        broken_rate,
        previous_premium,
        max_streak,
        limit_down_count,
        limit_up_count,
        previous_score,
    )
    return {
        "trade_date": trade_date,
        "limit_up_count": limit_up_count,
        "broken_count": broken_count,
        "limit_down_count": limit_down_count,
        "first_board_count": first_board_count,
        "second_board_count": second_board_count,
        "third_board_count": third_board_count,
        "high_board_count": high_board_count,
        "max_streak": max_streak,
        "seal_rate": round(seal_rate, 4),
        "broken_rate": round(broken_rate, 4),
        "promotion_rate": round(promotion_rate, 4),
        "previous_premium": round(previous_premium, 4),
        "previous_red_rate": round(previous_red_rate, 4),
        "emotion_score": round(score, 1),
        "emotion_stage": stage,
        "components": components,
    }


def _candidate_dates(reference: date | None = None, days: int = 12) -> list[date]:
    current = reference or date.today()
    result = []
    for offset in range(days):
        candidate = current - timedelta(days=offset)
        if candidate.weekday() < 5:
            result.append(candidate)
    return result


def fetch_sentiment_bundle(
    breadth: dict[str, Any] | None = None,
    previous_score: float | None = None,
    ignore_proxy: bool = True,
    timeout: float = 8.0,
) -> SentimentBundle:
    errors: list[str] = []
    selected_date = ""
    raw_limit_up = pd.DataFrame()
    effective_ignore_proxy = ignore_proxy

    # Eastmoney endpoints are frequently delayed by inherited desktop/server
    # proxies, so try a clean direct connection first.
    for bypass_proxy in dict.fromkeys([True, ignore_proxy]):
        ak = get_akshare(ignore_proxy=bypass_proxy)
        for candidate in _candidate_dates():
            date_text = candidate.strftime("%Y%m%d")
            value, error = _run_with_timeout(
                lambda fn=ak.stock_zt_pool_em, value=date_text: fn(date=value),
                timeout,
            )
            if isinstance(value, pd.DataFrame) and not value.empty:
                selected_date = date_text
                raw_limit_up = value
                effective_ignore_proxy = bypass_proxy
                break
            if error and "超时" not in error:
                errors.append(f"{date_text}涨停池：{error}")
        if not raw_limit_up.empty:
            break

    if raw_limit_up.empty or not selected_date:
        raise DataSourceError("最近交易日涨停池暂不可用，请稍后重试或开启“忽略系统代理”。")

    ak = get_akshare(ignore_proxy=effective_ignore_proxy)
    pools: dict[str, pd.DataFrame] = {}
    for key, function in {
        "broken": ak.stock_zt_pool_zbgc_em,
        "limit_down": ak.stock_zt_pool_dtgc_em,
        "previous": ak.stock_zt_pool_previous_em,
    }.items():
        value, error = _run_with_timeout(
            lambda fn=function, value=selected_date: fn(date=value),
            timeout,
        )
        pools[key] = value if isinstance(value, pd.DataFrame) else pd.DataFrame()
        if error:
            errors.append(f"{key}：{error}")

    limit_up = normalize_limit_up_pool(raw_limit_up)
    broken = normalize_broken_pool(pools["broken"])
    limit_down = pools["limit_down"].copy()
    previous = normalize_previous_pool(pools["previous"])
    trade_date = f"{selected_date[:4]}-{selected_date[4:6]}-{selected_date[6:]}"
    snapshot = build_sentiment_snapshot(
        trade_date,
        limit_up,
        broken,
        limit_down,
        previous,
        breadth=breadth,
        previous_score=previous_score,
    )
    return SentimentBundle(
        trade_date=trade_date,
        limit_up=limit_up,
        broken=broken,
        limit_down=limit_down,
        previous=previous,
        snapshot=snapshot,
        errors=errors,
    )
