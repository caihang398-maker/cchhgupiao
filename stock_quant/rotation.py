from __future__ import annotations

import numpy as np
import pandas as pd


PERIOD_WEIGHTS = {"daily": 0.45, "weekly": 0.35, "monthly": 0.20}


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _latest_by_period(history: pd.DataFrame) -> pd.DataFrame:
    frame = history.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame = frame.dropna(subset=["trade_date", "name", "period"])
    sort_columns = ["trade_date"]
    if "id" in frame.columns:
        sort_columns.append("id")
    return frame.sort_values(sort_columns).drop_duplicates(
        ["category", "name", "period"],
        keep="last",
    )


def _stage(row: pd.Series) -> str:
    daily = _safe_float(row.get("daily_flow"), 0.0)
    weekly = _safe_float(row.get("weekly_flow"), 0.0)
    monthly = _safe_float(row.get("monthly_flow"), 0.0)
    change = _safe_float(row.get("daily_change"), 0.0)
    rank = _safe_float(row.get("daily_rank"), 99.0)
    rank_change = _safe_float(row.get("rank_change"), 0.0)
    if daily > 0 and weekly <= 0 and monthly <= 0:
        return "启动"
    if daily > 0 and weekly > 0 and rank <= 5 and (change >= 2 or rank_change >= 3):
        return "加速"
    if daily > 0 and weekly > 0 and monthly > 0:
        return "主升"
    if daily < 0 and (weekly > 0 or monthly > 0):
        return "分化"
    if daily < 0 and weekly < 0:
        return "退潮"
    return "观察"


def build_mainline_snapshot(history: pd.DataFrame, category: str) -> pd.DataFrame:
    """Combine daily/weekly/monthly fund flows into a board lifecycle snapshot."""
    if history.empty:
        return pd.DataFrame()
    frame = history[history["category"].astype(str) == str(category)].copy()
    if frame.empty:
        return pd.DataFrame()
    latest = _latest_by_period(frame)

    result = pd.DataFrame({"name": sorted(latest["name"].astype(str).unique())})
    for period in PERIOD_WEIGHTS:
        subset = latest[latest["period"] == period][
            ["name", "net_inflow_yi", "rank", "change_pct", "leader", "leader_change_pct"]
        ].copy()
        subset = subset.rename(
            columns={
                "net_inflow_yi": f"{period}_flow",
                "rank": f"{period}_rank",
                "change_pct": f"{period}_change",
                "leader": f"{period}_leader",
                "leader_change_pct": f"{period}_leader_change",
            }
        )
        result = result.merge(subset, on="name", how="left")

    daily = frame[frame["period"] == "daily"].copy()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"], errors="coerce")
    daily = daily.dropna(subset=["trade_date"]).sort_values(["trade_date", "id"] if "id" in daily else ["trade_date"])
    daily = daily.drop_duplicates(["trade_date", "name"], keep="last")
    dates = sorted(daily["trade_date"].unique())
    latest_date = dates[-1] if dates else None
    previous_date = dates[-2] if len(dates) >= 2 else None
    latest_ranks = (
        daily[daily["trade_date"] == latest_date].set_index("name")["rank"]
        if latest_date is not None
        else pd.Series(dtype=float)
    )
    previous_ranks = (
        daily[daily["trade_date"] == previous_date].set_index("name")["rank"]
        if previous_date is not None
        else pd.Series(dtype=float)
    )
    result["rank_change"] = result["name"].map(previous_ranks) - result["name"].map(latest_ranks)

    recent_dates = dates[-10:]
    recent = daily[daily["trade_date"].isin(recent_dates)].copy()
    recent["is_strong"] = (
        pd.to_numeric(recent["rank"], errors="coerce").le(10)
        & pd.to_numeric(recent["net_inflow_yi"], errors="coerce").gt(0)
    )
    continuity = recent.groupby("name")["is_strong"].sum()
    result["strong_days_10"] = result["name"].map(continuity).fillna(0).astype(int)

    score = pd.Series(0.0, index=result.index)
    available_weight = pd.Series(0.0, index=result.index)
    for period, weight in PERIOD_WEIGHTS.items():
        flow = pd.to_numeric(result.get(f"{period}_flow"), errors="coerce")
        rank = pd.to_numeric(result.get(f"{period}_rank"), errors="coerce")
        flow_score = flow.rank(pct=True).fillna(0.0) * 100
        rank_score = (103.5 - rank.fillna(30) * 3.5).clip(0, 100)
        period_score = flow_score * 0.6 + rank_score * 0.4
        present = flow.notna() | rank.notna()
        score += period_score.where(present, 0) * weight
        available_weight += present.astype(float) * weight
    result["mainline_score"] = (score / available_weight.replace(0, np.nan)).fillna(0)
    result["mainline_score"] += result["strong_days_10"].clip(0, 5) * 2
    result["mainline_score"] += pd.to_numeric(result["rank_change"], errors="coerce").fillna(0).clip(-5, 5) * 1.5
    result["mainline_score"] = result["mainline_score"].clip(0, 100).round(1)
    result["stage"] = result.apply(_stage, axis=1)
    result["leader"] = result.get("daily_leader", pd.Series("", index=result.index)).fillna("")
    result["leader_change_pct"] = pd.to_numeric(
        result.get("daily_leader_change"),
        errors="coerce",
    )
    return result.sort_values(
        ["mainline_score", "daily_rank"],
        ascending=[False, True],
    ).reset_index(drop=True)


def build_rotation_heatmap(
    history: pd.DataFrame,
    category: str,
    top_names: list[str],
    max_dates: int = 20,
) -> pd.DataFrame:
    if history.empty or not top_names:
        return pd.DataFrame()
    frame = history[
        (history["category"].astype(str) == str(category))
        & (history["period"] == "daily")
        & (history["name"].astype(str).isin(top_names))
    ].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["net_inflow_yi"] = pd.to_numeric(frame["net_inflow_yi"], errors="coerce")
    frame = frame.dropna(subset=["trade_date"]).sort_values(["trade_date", "id"] if "id" in frame else ["trade_date"])
    frame = frame.drop_duplicates(["trade_date", "name"], keep="last")
    dates = sorted(frame["trade_date"].unique())[-max_dates:]
    frame = frame[frame["trade_date"].isin(dates)]
    return frame.pivot(index="name", columns="trade_date", values="net_inflow_yi").reindex(top_names)


def board_flow_history(
    history: pd.DataFrame,
    category: str,
    name: str,
) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame()
    frame = history[
        (history["category"].astype(str) == str(category))
        & (history["name"].astype(str) == str(name))
    ].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["net_inflow_yi"] = pd.to_numeric(frame["net_inflow_yi"], errors="coerce")
    frame = frame.dropna(subset=["trade_date"]).sort_values(["trade_date", "id"] if "id" in frame else ["trade_date"])
    return frame.drop_duplicates(["trade_date", "period"], keep="last")
