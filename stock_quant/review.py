from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any, Callable

import pandas as pd

from .data import fetch_daily_history
from .storage import (
    load_recommendations_for_review,
    save_recommendation_outcomes,
)


RETURN_WINDOWS = (1, 3, 5, 20)


def _first_hit_date(frame: pd.DataFrame, column: str, level: float, direction: str) -> str | None:
    if direction == "down":
        hits = frame[pd.to_numeric(frame[column], errors="coerce") <= level]
    else:
        hits = frame[pd.to_numeric(frame[column], errors="coerce") >= level]
    if hits.empty:
        return None
    return pd.to_datetime(hits.iloc[0]["date"]).strftime("%Y-%m-%d")


def evaluate_recommendation(
    recommendation: dict[str, Any] | pd.Series,
    history: pd.DataFrame,
) -> dict[str, Any]:
    item = dict(recommendation)
    recommendation_date = pd.Timestamp(item["trade_date"]).normalize()
    entry_price = float(item["close"])
    if entry_price <= 0:
        raise ValueError("推荐价格必须大于0")

    bars = history.copy()
    bars["date"] = pd.to_datetime(bars["date"], errors="coerce")
    bars = bars.dropna(subset=["date", "close", "high", "low"])
    bars = bars[bars["date"].dt.normalize() > recommendation_date]
    bars = bars.drop_duplicates("date").sort_values("date").head(20).reset_index(drop=True)
    available_days = len(bars)

    returns: dict[str, float | None] = {}
    for window in RETURN_WINDOWS:
        value = None
        if available_days >= window:
            value = float(bars.iloc[window - 1]["close"]) / entry_price - 1
        returns[f"t{window}_return"] = value

    max_gain = None
    max_drawdown = None
    stop_hit_date = None
    target1_hit_date = None
    target2_hit_date = None
    if available_days:
        max_gain = float(pd.to_numeric(bars["high"], errors="coerce").max()) / entry_price - 1
        close_path = pd.concat(
            [
                pd.Series([entry_price], dtype=float),
                pd.to_numeric(bars["close"], errors="coerce").dropna().reset_index(drop=True),
            ],
            ignore_index=True,
        )
        running_peak = close_path.cummax()
        max_drawdown = float((close_path / running_peak - 1).min())
        stop_hit_date = _first_hit_date(bars, "low", float(item["stop_loss"]), "down")
        target1_hit_date = _first_hit_date(bars, "high", float(item["take_profit_1"]), "up")
        target2_hit_date = _first_hit_date(bars, "high", float(item["take_profit_2"]), "up")

    target1_before_stop: int | None = None
    if target1_hit_date and stop_hit_date:
        if target1_hit_date < stop_hit_date:
            target1_before_stop = 1
        elif target1_hit_date > stop_hit_date:
            target1_before_stop = 0
    elif target1_hit_date:
        target1_before_stop = 1
    elif stop_hit_date:
        target1_before_stop = 0

    if available_days >= 20:
        status = "complete"
    elif available_days > 0:
        status = "tracking"
    else:
        status = "pending"

    evaluated_through = (
        pd.to_datetime(bars.iloc[-1]["date"]).strftime("%Y-%m-%d")
        if available_days
        else None
    )
    return {
        "recommendation_id": int(item["id"]),
        "run_id": int(item["run_id"]),
        "trade_date": str(item["trade_date"]),
        "horizon": str(item["horizon"]),
        "symbol": str(item["symbol"]),
        "name": str(item.get("name", "")),
        "entry_price": entry_price,
        "available_days": available_days,
        **returns,
        "max_gain_20": max_gain,
        "max_drawdown_20": max_drawdown,
        "stop_hit": int(stop_hit_date is not None),
        "stop_hit_date": stop_hit_date,
        "target1_hit": int(target1_hit_date is not None),
        "target1_hit_date": target1_hit_date,
        "target2_hit": int(target2_hit_date is not None),
        "target2_hit_date": target2_hit_date,
        "target1_before_stop": target1_before_stop,
        "review_status": status,
        "evaluated_through": evaluated_through,
    }


def refresh_recommendation_outcomes(
    ignore_proxy: bool = False,
    max_workers: int = 4,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    recommendations = load_recommendations_for_review()
    if recommendations.empty:
        return pd.DataFrame(), []

    errors: list[str] = []
    histories: dict[str, pd.DataFrame] = {}
    grouped = recommendations.groupby("symbol")
    work = []
    for symbol, rows in grouped:
        earliest = pd.to_datetime(rows["trade_date"]).min().date() - timedelta(days=10)
        work.append((str(symbol), earliest))

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 6))) as executor:
        futures = {
            executor.submit(
                fetch_daily_history,
                symbol,
                start_date=start,
                end_date=date.today(),
                adjust="",
                ignore_proxy=ignore_proxy,
            ): symbol
            for symbol, start in work
        }
        total = len(futures)
        for done, future in enumerate(as_completed(futures), start=1):
            symbol = futures[future]
            if progress_callback:
                progress_callback(done, total, symbol)
            try:
                histories[symbol] = future.result()
            except Exception as exc:
                errors.append(f"{symbol} 复盘行情获取失败：{exc}")

    outcomes = []
    for _, recommendation in recommendations.iterrows():
        symbol = str(recommendation["symbol"])
        history = histories.get(symbol)
        if history is None or history.empty:
            continue
        try:
            outcomes.append(evaluate_recommendation(recommendation, history))
        except Exception as exc:
            errors.append(f"{symbol} 推荐#{int(recommendation['id'])}复盘失败：{exc}")

    frame = pd.DataFrame(outcomes)
    if not frame.empty:
        save_recommendation_outcomes(frame.to_dict("records"))
    return frame, errors


def outcome_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    labels = {"short": "短期", "mid": "中期", "long": "长期"}
    rows = []
    for horizon in ("short", "mid", "long"):
        data = frame[frame["horizon"] == horizon]
        if data.empty:
            continue
        row: dict[str, Any] = {
            "周期": labels.get(str(horizon), str(horizon)),
            "推荐数": int(len(data)),
        }
        for window in RETURN_WINDOWS:
            column = f"t{window}_return"
            valid = pd.to_numeric(data[column], errors="coerce").dropna()
            row[f"推荐后{window}日样本"] = int(len(valid))
            row[f"推荐后{window}日平均收益"] = float(valid.mean()) if not valid.empty else None
            row[f"推荐后{window}日上涨率"] = float((valid > 0).mean()) if not valid.empty else None
        tracked = data[pd.to_numeric(data["available_days"], errors="coerce") > 0]
        row["止损触发率"] = float(tracked["stop_hit"].mean()) if not tracked.empty else None
        row["目标一命中率"] = float(tracked["target1_hit"].mean()) if not tracked.empty else None
        row["目标二命中率"] = float(tracked["target2_hit"].mean()) if not tracked.empty else None
        drawdowns = pd.to_numeric(tracked["max_drawdown_20"], errors="coerce").dropna()
        row["平均最大回撤"] = float(drawdowns.mean()) if not drawdowns.empty else None
        row["最差最大回撤"] = float(drawdowns.min()) if not drawdowns.empty else None
        rows.append(row)
    return pd.DataFrame(rows)
