from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .indicators import add_indicators


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    drawdown = equity / peak - 1
    return float(drawdown.min()) if not drawdown.empty else 0.0


def _commission(value: float, rate: float, minimum: float) -> float:
    return max(minimum, value * rate) if value > 0 else 0.0


def run_backtest(
    history: pd.DataFrame,
    initial_cash: float = 100_000,
    commission_rate: float = 0.0003,
    minimum_commission: float = 5.0,
    stamp_duty_rate: float = 0.0005,
    slippage: float = 0.0005,
    max_position_pct: float = 0.3,
    lot_size: int = 100,
    annual_risk_free_rate: float = 0.02,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    """Backtest the daily technical strategy with practical A-share constraints.

    Signals are generated after the close and executed at the next tradable
    session's open. Buys use board lots, commission has a minimum, and stamp
    duty is charged on sells only.
    """
    if initial_cash <= 0:
        raise ValueError("初始资金必须大于0")
    if not 0 < max_position_pct <= 1:
        raise ValueError("单股最大仓位必须在0到100%之间")
    if lot_size <= 0:
        raise ValueError("每手股数必须大于0")

    df = add_indicators(history)
    df = df.dropna(subset=["ma20", "ma60", "macd_hist", "rsi14", "atr14"]).reset_index(drop=True)
    if len(df) < 60:
        raise ValueError("可回测数据不足")

    cash = float(initial_cash)
    shares = 0
    entry_price = 0.0
    entry_total_cost = 0.0
    trailing_stop = 0.0
    pending_action: dict[str, str] | None = None
    rows: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    closed_returns: list[float] = []
    benchmark_start = float(df["close"].iloc[0])

    for _, row in df.iterrows():
        close = float(row["close"])
        open_price = float(row["open"]) if pd.notna(row["open"]) and row["open"] > 0 else close
        volume = float(row["volume"]) if pd.notna(row.get("volume")) else 1.0
        tradable = open_price > 0 and volume > 0
        atr14 = float(row["atr14"]) if pd.notna(row["atr14"]) else close * 0.04
        date_value = pd.to_datetime(row["date"]).strftime("%Y-%m-%d")

        if pending_action and tradable:
            signal_date = pending_action["signal_date"]
            reason = pending_action["reason"]
            if pending_action["action"] == "buy" and shares == 0:
                execution_price = open_price * (1 + slippage)
                budget = cash * max_position_pct
                buy_shares = math.floor(budget / execution_price / lot_size) * lot_size
                while buy_shares > 0:
                    gross = buy_shares * execution_price
                    fee = _commission(gross, commission_rate, minimum_commission)
                    if gross + fee <= cash:
                        break
                    buy_shares -= lot_size
                if buy_shares > 0:
                    gross = buy_shares * execution_price
                    fee = _commission(gross, commission_rate, minimum_commission)
                    cash -= gross + fee
                    shares = buy_shares
                    entry_price = execution_price
                    entry_total_cost = gross + fee
                    trailing_stop = max(execution_price - 2.2 * atr14, float(row["ma20"]) * 0.97)
                    trades.append(
                        {
                            "信号日期": signal_date,
                            "成交日期": date_value,
                            "动作": "买入",
                            "价格": round(execution_price, 3),
                            "股数": shares,
                            "费用": round(fee, 2),
                            "原因": reason,
                        }
                    )
                pending_action = None
            elif pending_action["action"] == "sell" and shares > 0:
                execution_price = open_price * (1 - slippage)
                gross = shares * execution_price
                fee = _commission(gross, commission_rate, minimum_commission)
                tax = gross * stamp_duty_rate
                net_proceeds = gross - fee - tax
                net_return = net_proceeds / entry_total_cost - 1 if entry_total_cost else 0.0
                cash += net_proceeds
                closed_returns.append(float(net_return))
                trades.append(
                    {
                        "信号日期": signal_date,
                        "成交日期": date_value,
                        "动作": "卖出",
                        "价格": round(execution_price, 3),
                        "股数": shares,
                        "费用": round(fee + tax, 2),
                        "收益率": f"{net_return:.2%}",
                        "原因": reason,
                    }
                )
                shares = 0
                entry_price = 0.0
                entry_total_cost = 0.0
                trailing_stop = 0.0
                pending_action = None

        trend_ok = row["close"] > row["ma20"] > row["ma60"]
        momentum_ok = row["macd_hist"] > 0 and 45 <= row["rsi14"] <= 75
        volume_ok = row["volume_ratio"] >= 1.05 if pd.notna(row["volume_ratio"]) else True
        buy_signal = bool(trend_ok and momentum_ok and volume_ok)

        sell_reason = ""
        if shares > 0:
            trailing_stop = max(trailing_stop, close - 2.2 * atr14, float(row["ma20"]) * 0.97)
            if close < trailing_stop:
                sell_reason = "跌破移动止盈"
            elif row["close"] < row["ma20"] and row["macd_hist"] < 0:
                sell_reason = "趋势和动量同步转弱"
            elif row["rsi14"] > 82 and row["close"] < row["open"]:
                sell_reason = "过热后转弱"

        if pending_action is None:
            if shares == 0 and buy_signal:
                pending_action = {
                    "action": "buy",
                    "signal_date": date_value,
                    "reason": "趋势、动量和量能同时满足",
                }
            elif shares > 0 and sell_reason:
                pending_action = {
                    "action": "sell",
                    "signal_date": date_value,
                    "reason": sell_reason,
                }

        equity = cash + shares * close
        rows.append(
            {
                "date": row["date"],
                "close": close,
                "equity": equity,
                "benchmark_equity": initial_cash * close / benchmark_start,
                "position": 1 if shares > 0 else 0,
                "position_value": shares * close,
                "cash": cash,
                "trailing_stop": trailing_stop if shares > 0 else np.nan,
            }
        )

    curve = pd.DataFrame(rows)
    curve["drawdown"] = curve["equity"] / curve["equity"].cummax() - 1
    trade_frame = pd.DataFrame(trades)

    total_return = curve["equity"].iloc[-1] / initial_cash - 1
    days = max(1, (pd.to_datetime(curve["date"]).iloc[-1] - pd.to_datetime(curve["date"]).iloc[0]).days)
    annual_return = (1 + total_return) ** (365 / days) - 1
    max_drawdown = _max_drawdown(curve["equity"])
    benchmark_return = df["close"].iloc[-1] / df["close"].iloc[0] - 1
    daily_returns = curve["equity"].pct_change().dropna()
    annual_volatility = float(daily_returns.std(ddof=0) * np.sqrt(252)) if not daily_returns.empty else 0.0
    excess_daily = daily_returns - annual_risk_free_rate / 252
    sharpe = (
        float(excess_daily.mean() / daily_returns.std(ddof=0) * np.sqrt(252))
        if not daily_returns.empty and daily_returns.std(ddof=0) > 0
        else 0.0
    )
    wins = [value for value in closed_returns if value > 0]
    losses = [value for value in closed_returns if value < 0]
    profit_loss_ratio = (
        float(np.mean(wins) / abs(np.mean(losses)))
        if wins and losses and np.mean(losses) != 0
        else 0.0
    )

    metrics = {
        "total_return": float(total_return),
        "annual_return": float(annual_return),
        "annual_volatility": annual_volatility,
        "sharpe": sharpe,
        "max_drawdown": float(max_drawdown),
        "benchmark_return": float(benchmark_return),
        "closed_trade_count": float(len(closed_returns)),
        "order_count": float(len(trade_frame)),
        "win_rate": float(len(wins) / len(closed_returns)) if closed_returns else 0.0,
        "profit_loss_ratio": profit_loss_ratio,
        "exposure": float(curve["position"].mean()),
        "final_equity": float(curve["equity"].iloc[-1]),
    }
    return curve, trade_frame, metrics
