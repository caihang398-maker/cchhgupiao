from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .data import DataSourceError, fetch_daily_history, get_akshare, plain_code, prefixed_symbol
from .settings import CACHE_DIR


LEADER_CACHE_DIR = CACHE_DIR / "leaders"
LOCAL_BOARD_ALIASES = {
    "证券": ("证券", "券商"),
    "银行": ("银行",),
    "保险": ("保险",),
    "电力": ("电力", "电网", "能源"),
    "煤炭": ("煤", "能源"),
    "石油": ("石油", "油气"),
    "医药": ("医药", "制药", "医疗", "生物"),
    "半导体": ("半导体", "芯片", "微电子"),
}


@dataclass
class LeaderBundle:
    board_type: str
    board_name: str
    leaders: pd.DataFrame
    curve: pd.DataFrame
    metrics: pd.DataFrame
    benchmark_name: str
    errors: list[str]
    source_note: str = ""


def _leader_cache_path(board_type: str, board_name: str, lookback_days: int) -> Path:
    digest = hashlib.sha256(f"{board_type}|{board_name}|{lookback_days}".encode("utf-8")).hexdigest()[:20]
    return LEADER_CACHE_DIR / f"{digest}.pkl"


def _save_leader_bundle(bundle: LeaderBundle, lookback_days: int) -> None:
    if bundle.leaders.empty or bundle.curve.empty:
        return
    try:
        path = _leader_cache_path(bundle.board_type, bundle.board_name, lookback_days)
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.to_pickle(bundle, path)
    except Exception:
        pass


def _load_leader_bundle_cache(
    board_type: str,
    board_name: str,
    lookback_days: int,
) -> LeaderBundle | None:
    path = _leader_cache_path(board_type, board_name, lookback_days)
    if not path.exists():
        return None
    try:
        bundle = pd.read_pickle(path)
        if not isinstance(bundle, LeaderBundle) or bundle.leaders.empty or bundle.curve.empty:
            return None
        bundle.errors = list(bundle.errors) + ["在线行情暂不可用，本次显示最近一次成功生成的龙头结果。"]
        bundle.source_note = "数据来源：最近一次成功生成的本地龙头缓存；请以页面时间和最新行情复核。"
        return bundle
    except Exception:
        return None


def _numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = frame.copy()
    for column in columns:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    return out


def normalize_constituents(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize industry/concept constituents returned by AkShare."""
    if raw.empty:
        return pd.DataFrame()
    rename = {
        "代码": "code",
        "名称": "name",
        "最新价": "price",
        "涨跌幅": "change_pct",
        "成交额": "turnover",
        "换手率": "turnover_rate",
        "量比": "volume_ratio",
        "市盈率-动态": "pe",
        "市净率": "pb",
    }
    frame = raw.rename(columns=rename).copy()
    required = [
        "code",
        "name",
        "price",
        "change_pct",
        "turnover",
        "turnover_rate",
        "volume_ratio",
        "pe",
        "pb",
    ]
    for column in required:
        if column not in frame.columns:
            frame[column] = np.nan
    frame = frame[required]
    frame["code"] = frame["code"].map(plain_code)
    frame["symbol"] = frame["code"].map(prefixed_symbol)
    frame = _numeric(
        frame,
        ["price", "change_pct", "turnover", "turnover_rate", "volume_ratio", "pe", "pb"],
    )
    frame = frame[frame["code"].str.len().eq(6)].copy()
    frame = frame[~frame["name"].fillna("").astype(str).str.contains("ST|退", case=False, regex=True)]
    return frame.reset_index(drop=True)


def rank_leaders(constituents: pd.DataFrame, limit: int = 3) -> pd.DataFrame:
    """Rank board leaders using price strength, liquidity and participation."""
    if constituents.empty:
        return pd.DataFrame()
    frame = constituents.copy()
    factors = {
        "change_pct": 0.38,
        "turnover": 0.30,
        "turnover_rate": 0.20,
        "volume_ratio": 0.12,
    }
    score = pd.Series(0.0, index=frame.index)
    for column, weight in factors.items():
        values = pd.to_numeric(frame[column], errors="coerce")
        score += values.rank(pct=True).fillna(0.5) * weight * 100
    frame["leader_score"] = score.clip(0, 100)
    frame = frame.sort_values(
        ["leader_score", "change_pct", "turnover"],
        ascending=False,
    ).head(limit)
    frame["leader_rank"] = range(1, len(frame) + 1)
    frame["leader_label"] = frame["leader_rank"].map({1: "龙一", 2: "龙二", 3: "龙三"})
    return frame.reset_index(drop=True)


def _clean_index_history(raw: pd.DataFrame) -> pd.DataFrame:
    rename = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "turnover",
    }
    frame = raw.rename(columns=rename).copy()
    for column in ["date", "open", "close", "high", "low", "volume", "turnover"]:
        if column not in frame.columns:
            frame[column] = np.nan
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = _numeric(frame, ["open", "close", "high", "low", "volume", "turnover"])
    return frame.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)


def fetch_benchmark_history(
    start_date: date,
    benchmark_code: str = "000300",
    ignore_proxy: bool = True,
) -> pd.DataFrame:
    ak = get_akshare(ignore_proxy=ignore_proxy)
    try:
        raw = ak.stock_zh_index_daily_tx(symbol=f"sh{benchmark_code}")
        frame = _clean_index_history(raw)
        frame = frame[frame["date"].dt.date >= start_date]
        if not frame.empty:
            return frame.reset_index(drop=True)
    except Exception:
        pass
    try:
        raw = ak.index_zh_a_hist(
            symbol=benchmark_code,
            period="daily",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=date.today().strftime("%Y%m%d"),
        )
        frame = _clean_index_history(raw)
        if not frame.empty:
            return frame
    except Exception as exc:
        raise DataSourceError(f"无法获取沪深300历史走势：{exc}") from exc
    raise DataSourceError("沪深300历史走势返回为空")


def fallback_leaders_from_recommendations(
    recommendations: pd.DataFrame,
    board_type: str,
    board_name: str,
    limit: int = 3,
) -> pd.DataFrame:
    """Use the latest verified recommendation pool when board constituents are unavailable."""
    if recommendations.empty:
        return pd.DataFrame()
    frame = recommendations.copy()
    for column in ["symbol", "name", "industry", "topics", "score", "close"]:
        if column not in frame.columns:
            frame[column] = "" if column in {"symbol", "name", "industry", "topics"} else np.nan

    board_text = str(board_name).strip()
    if board_type == "题材":
        matched = frame["topics"].fillna("").astype(str).str.contains(
            board_text,
            regex=False,
        )
    else:
        matched = frame["industry"].fillna("").astype(str).str.contains(
            board_text,
            regex=False,
        )
    frame = frame[matched].copy()
    if frame.empty:
        return pd.DataFrame()

    frame["leader_score"] = pd.to_numeric(frame["score"], errors="coerce").fillna(0).clip(0, 100)
    frame["price"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["code"] = frame["symbol"].map(plain_code)
    frame["symbol"] = frame["symbol"].map(prefixed_symbol)
    for column in ["change_pct", "turnover", "turnover_rate", "volume_ratio", "pe", "pb"]:
        frame[column] = np.nan
    frame = frame.sort_values(["leader_score", "name"], ascending=[False, True])
    frame = frame.drop_duplicates("symbol").head(limit).reset_index(drop=True)
    frame["leader_rank"] = range(1, len(frame) + 1)
    frame["leader_label"] = frame["leader_rank"].map({1: "龙一", 2: "龙二", 3: "龙三"})
    return frame[
        [
            "code",
            "symbol",
            "name",
            "price",
            "change_pct",
            "turnover",
            "turnover_rate",
            "volume_ratio",
            "pe",
            "pb",
            "leader_score",
            "leader_rank",
            "leader_label",
        ]
    ]


def fallback_leaders_from_local_snapshot(board_name: str, limit: int = 3) -> pd.DataFrame:
    """Best-effort local fallback for boards whose names also identify listed companies."""
    spot_path = CACHE_DIR / "spot_latest.pkl"
    if not spot_path.exists():
        return pd.DataFrame()
    try:
        spot = pd.read_pickle(spot_path)
    except Exception:
        return pd.DataFrame()
    if not isinstance(spot, pd.DataFrame) or spot.empty:
        return pd.DataFrame()
    keywords = LOCAL_BOARD_ALIASES.get(str(board_name).strip(), (str(board_name).strip(),))
    names = spot.get("name", pd.Series("", index=spot.index)).fillna("").astype(str)
    matched = pd.Series(False, index=spot.index)
    for keyword in keywords:
        if keyword:
            matched |= names.str.contains(keyword, regex=False)
    frame = spot[matched].copy()
    if frame.empty:
        return pd.DataFrame()
    frame["symbol"] = frame["symbol"].map(prefixed_symbol)
    frame["code"] = frame["symbol"].map(plain_code)
    for column in ["price", "change_pct", "turnover"]:
        frame[column] = pd.to_numeric(frame.get(column), errors="coerce")
    frame["turnover_rate"] = np.nan
    frame["volume_ratio"] = np.nan
    frame["pe"] = np.nan
    frame["pb"] = np.nan
    return rank_leaders(
        frame[
            [
                "code",
                "symbol",
                "name",
                "price",
                "change_pct",
                "turnover",
                "turnover_rate",
                "volume_ratio",
                "pe",
                "pb",
            ]
        ],
        limit=limit,
    )


def normalized_curve(
    histories: dict[str, pd.DataFrame],
    benchmark: pd.DataFrame | None = None,
    benchmark_name: str = "沪深300",
) -> pd.DataFrame:
    """Build a 100-base comparison curve and an equal-weight leader index."""
    series: list[pd.Series] = []
    for label, history in histories.items():
        if history.empty:
            continue
        data = history[["date", "close"]].dropna().drop_duplicates("date").sort_values("date")
        if data.empty or float(data["close"].iloc[0]) <= 0:
            continue
        indexed = data.set_index("date")["close"] / float(data["close"].iloc[0]) * 100
        indexed.name = label
        series.append(indexed)
    if benchmark is not None and not benchmark.empty:
        data = benchmark[["date", "close"]].dropna().drop_duplicates("date").sort_values("date")
        if not data.empty and float(data["close"].iloc[0]) > 0:
            indexed = data.set_index("date")["close"] / float(data["close"].iloc[0]) * 100
            indexed.name = benchmark_name
            series.append(indexed)
    if not series:
        return pd.DataFrame()
    curve = pd.concat(series, axis=1).sort_index().ffill().dropna(how="all")
    leader_columns = [column for column in histories if column in curve.columns]
    if leader_columns:
        curve["龙头等权指数"] = curve[leader_columns].mean(axis=1)
    return curve.reset_index()


def _period_return(close: pd.Series, days: int) -> float | None:
    clean = pd.to_numeric(close, errors="coerce").dropna()
    if len(clean) <= days or float(clean.iloc[-days - 1]) == 0:
        return None
    return (float(clean.iloc[-1]) / float(clean.iloc[-days - 1]) - 1) * 100


def calculate_leader_metrics(
    leaders: pd.DataFrame,
    histories: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, leader in leaders.iterrows():
        label = str(leader["leader_label"])
        name = str(leader["name"])
        history = histories.get(f"{label} {name}", pd.DataFrame())
        close = pd.to_numeric(history.get("close"), errors="coerce").dropna()
        if close.empty:
            continue
        returns = close.pct_change().dropna()
        rolling_peak = close.cummax().replace(0, np.nan)
        drawdown = close / rolling_peak - 1
        ma5 = close.rolling(5).mean().iloc[-1] if len(close) >= 5 else close.iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else close.iloc[-1]
        rows.append(
            {
                "排名": label,
                "代码": str(leader["symbol"]).upper(),
                "名称": name,
                "龙头分": round(float(leader["leader_score"]), 1),
                "当日涨跌%": round(float(leader.get("change_pct", 0) or 0), 2),
                "5日涨幅%": _period_return(close, 5),
                "10日涨幅%": _period_return(close, 10),
                "20日涨幅%": _period_return(close, 20),
                "20日最大回撤%": round(float(drawdown.tail(20).min() * 100), 2),
                "20日波动率%": round(float(returns.tail(20).std() * np.sqrt(20) * 100), 2),
                "趋势状态": "强势" if float(close.iloc[-1]) >= float(ma5) >= float(ma20) else "震荡/转弱",
                "成交额(亿)": round(float(leader.get("turnover", 0) or 0) / 100_000_000, 2),
                "换手率%": round(float(leader.get("turnover_rate", 0) or 0), 2),
                "量比": round(float(leader.get("volume_ratio", 0) or 0), 2),
            }
        )
    return pd.DataFrame(rows)


def leader_switches(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty or "leader" not in history.columns:
        return pd.DataFrame()
    frame = history.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame = frame.dropna(subset=["trade_date"]).sort_values(["trade_date", "id"] if "id" in frame.columns else ["trade_date"])
    frame["leader"] = frame["leader"].fillna("").astype(str).str.strip()
    frame = frame[frame["leader"].ne("")].drop_duplicates("trade_date", keep="last")
    if frame.empty:
        return pd.DataFrame()
    frame["previous_leader"] = frame["leader"].shift()
    switched = frame[frame["previous_leader"].notna() & frame["leader"].ne(frame["previous_leader"])].copy()
    if switched.empty:
        return pd.DataFrame(columns=["日期", "原龙头", "新龙头", "新龙头涨幅%"])
    return switched.rename(
        columns={
            "trade_date": "日期",
            "previous_leader": "原龙头",
            "leader": "新龙头",
            "leader_change_pct": "新龙头涨幅%",
        }
    )[["日期", "原龙头", "新龙头", "新龙头涨幅%"]].sort_values("日期", ascending=False)


def fetch_leader_bundle(
    board_type: str,
    board_name: str,
    lookback_days: int = 60,
    ignore_proxy: bool = True,
    fallback_recommendations: pd.DataFrame | None = None,
) -> LeaderBundle:
    errors: list[str] = []
    raw = pd.DataFrame()
    effective_ignore_proxy = ignore_proxy
    constituent_errors: list[str] = []
    source_note = "数据来源：板块成分接口。"
    for bypass_proxy in dict.fromkeys([ignore_proxy, not ignore_proxy]):
        try:
            ak = get_akshare(ignore_proxy=bypass_proxy)
            if board_type == "题材":
                raw = ak.stock_board_concept_cons_em(symbol=board_name)
            else:
                raw = ak.stock_board_industry_cons_em(symbol=board_name)
            if not raw.empty:
                effective_ignore_proxy = bypass_proxy
                break
        except Exception as exc:
            constituent_errors.append(str(exc))
    leaders = rank_leaders(normalize_constituents(raw), limit=3) if not raw.empty else pd.DataFrame()
    if leaders.empty and fallback_recommendations is not None:
        leaders = fallback_leaders_from_recommendations(
            fallback_recommendations,
            board_type,
            board_name,
            limit=3,
        )
        if not leaders.empty:
            source_note = "数据来源：板块成分接口暂不可用，本次已自动使用当日推荐池中的同板块股票生成龙头曲线。"
    if leaders.empty:
        leaders = fallback_leaders_from_local_snapshot(board_name, limit=3)
        if not leaders.empty:
            source_note = "数据来源：板块成分接口暂不可用，本次已使用最近完整行情快照中的名称匹配股票生成备用龙头曲线。"
    if leaders.empty:
        cached_bundle = _load_leader_bundle_cache(board_type, board_name, lookback_days)
        if cached_bundle is not None:
            return cached_bundle
        detail = "；".join(constituent_errors[-2:]) or "返回为空"
        raise DataSourceError(
            f"无法获取{board_type}{board_name}成分股，且当日推荐池没有足够的同板块候选：{detail}"
        )

    start = date.today() - timedelta(days=max(45, lookback_days * 2))
    histories: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for _, leader in leaders.iterrows():
            label = f"{leader['leader_label']} {leader['name']}"
            future = executor.submit(
                fetch_daily_history,
                str(leader["symbol"]),
                start,
                None,
                "qfq",
                effective_ignore_proxy,
            )
            futures[future] = label
        benchmark_future = executor.submit(
            fetch_benchmark_history,
            start,
            "000300",
            effective_ignore_proxy,
        )

        for future in as_completed(futures):
            label = futures[future]
            try:
                histories[label] = future.result().tail(lookback_days)
            except Exception as exc:
                errors.append(f"{label}历史走势获取失败：{exc}")
        try:
            benchmark = benchmark_future.result().tail(lookback_days)
        except Exception as exc:
            benchmark = pd.DataFrame()
            errors.append(str(exc))

    curve = normalized_curve(histories, benchmark=benchmark, benchmark_name="沪深300")
    metrics = calculate_leader_metrics(leaders, histories)
    if curve.empty or metrics.empty:
        cached_bundle = _load_leader_bundle_cache(board_type, board_name, lookback_days)
        if cached_bundle is not None:
            cached_bundle.errors.extend(errors)
            return cached_bundle
    bundle = LeaderBundle(
        board_type=board_type,
        board_name=board_name,
        leaders=leaders,
        curve=curve,
        metrics=metrics,
        benchmark_name="沪深300",
        errors=errors,
        source_note=source_note,
    )
    _save_leader_bundle(bundle, lookback_days)
    return bundle
