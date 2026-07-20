from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import add_indicators


@dataclass
class SignalReport:
    symbol: str
    name: str
    date: str
    close: float
    score: int
    rating: str
    action: str
    buy_zone_low: float
    buy_zone_high: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    trailing_stop: float
    position_pct: float
    reasons: list[str]
    sell_triggers: list[str]
    warnings: list[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "代码": self.symbol.upper(),
            "名称": self.name,
            "日期": self.date,
            "收盘价": round(self.close, 3),
            "评分": self.score,
            "级别": self.rating,
            "动作": self.action,
            "买点下沿": round(self.buy_zone_low, 3),
            "买点上沿": round(self.buy_zone_high, 3),
            "止损": round(self.stop_loss, 3),
            "目标一": round(self.take_profit_1, 3),
            "目标二": round(self.take_profit_2, 3),
            "移动止盈": round(self.trailing_stop, 3),
            "建议仓位": f"{self.position_pct:.0%}",
            "理由": "；".join(self.reasons),
            "卖出触发": "；".join(self.sell_triggers),
        }


def _is_true(value: object) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except TypeError:
        pass
    return bool(value)


def _latest_date(row: pd.Series) -> str:
    if "date" in row.index:
        try:
            return pd.to_datetime(row["date"]).strftime("%Y-%m-%d")
        except Exception:
            return str(row["date"])
    return ""


def analyze_stock(
    history: pd.DataFrame,
    symbol: str,
    name: str = "",
    risk_per_trade: float = 0.01,
    max_position_pct: float = 0.3,
) -> SignalReport:
    if len(history) < 80:
        raise ValueError("至少需要 80 个交易日数据才能生成稳定信号")

    df = add_indicators(history)
    last = df.iloc[-1]
    prev = df.iloc[-2]

    close = float(last["close"])
    ma20 = float(last["ma20"]) if pd.notna(last["ma20"]) else np.nan
    ma60 = float(last["ma60"]) if pd.notna(last["ma60"]) else np.nan
    ma120 = float(last["ma120"]) if pd.notna(last["ma120"]) else np.nan
    atr14 = float(last["atr14"]) if pd.notna(last["atr14"]) else close * 0.04
    atr_pct = atr14 / close if close else 0.04

    score = 0
    reasons: list[str] = []
    sell_triggers: list[str] = []
    warnings: list[str] = []

    if _is_true(close > ma20 > ma60):
        score += 22
        reasons.append("价格站上20日线且20日线高于60日线")
    elif _is_true(close > ma20):
        score += 12
        reasons.append("价格站上20日线")
    else:
        sell_triggers.append("收盘价未站稳20日线")

    if _is_true(close > ma120):
        score += 8
        reasons.append("价格位于120日线之上")

    if _is_true(last["ma20_slope"] > 0):
        score += 10
        reasons.append("20日均线向上")

    if _is_true(last["macd"] > last["macd_signal"] and last["macd_hist"] > prev["macd_hist"]):
        score += 15
        reasons.append("指数平滑异同移动平均线（MACD）多头且柱体改善")
    elif _is_true(last["macd"] < last["macd_signal"]):
        sell_triggers.append("指数平滑异同移动平均线（MACD）跌破信号线")

    rsi14 = float(last["rsi14"]) if pd.notna(last["rsi14"]) else 50
    if 45 <= rsi14 <= 68:
        score += 12
        reasons.append(f"相对强弱指标（RSI）处于健康区间（{rsi14:.1f}）")
    elif 68 < rsi14 <= 78:
        score += 6
        reasons.append(f"相对强弱指标（RSI）偏强但接近过热（{rsi14:.1f}）")
    elif rsi14 > 78:
        warnings.append(f"相对强弱指标（RSI）过热（{rsi14:.1f}），追高风险加大")
        sell_triggers.append("相对强弱指标（RSI）过热后出现放量回落")

    previous_high_20 = float(df["high"].rolling(20, min_periods=10).max().shift(1).iloc[-1])
    volume_ratio = float(last["volume_ratio"]) if pd.notna(last["volume_ratio"]) else 1.0
    if close > previous_high_20 and volume_ratio >= 1.15:
        score += 18
        reasons.append(f"突破20日高点且量能放大({volume_ratio:.2f}x)")
    elif close > previous_high_20:
        score += 9
        reasons.append("突破20日高点")
    elif volume_ratio >= 1.35 and _is_true(close > prev["close"]):
        score += 8
        reasons.append(f"上涨放量({volume_ratio:.2f}x)")

    if 0.015 <= atr_pct <= 0.065:
        score += 10
        reasons.append(f"平均真实波幅（ATR）适中（{atr_pct:.1%}）")
    elif atr_pct > 0.085:
        warnings.append(f"平均真实波幅（ATR）过高（{atr_pct:.1%}），仓位需压低")
        score -= 8

    if _is_true(last["return_20d"] > 0):
        score += 5
    if _is_true(last["return_5d"] < -0.08):
        warnings.append("近5日跌幅较大，等待企稳")
        score -= 8

    if _is_true(close < ma60):
        sell_triggers.append("跌破60日趋势线")
    if _is_true(last["close"] < last["open"] and volume_ratio > 1.5):
        sell_triggers.append("放量阴线")

    score = int(max(0, min(100, score)))
    if score >= 82:
        rating = "强势候选"
        action = "等待回踩确认或小仓试错"
    elif score >= 68:
        rating = "可关注"
        action = "观察买点，满足风控再进"
    elif score >= 52:
        rating = "中性"
        action = "暂不追入"
    else:
        rating = "弱势"
        action = "回避或等待趋势修复"

    risk_distance = max(atr14 * 2.0, close * 0.035)
    stop_loss = max(0.01, close - risk_distance)
    risk_pct = (close - stop_loss) / close if close else 0.05
    raw_position_pct = min(max_position_pct, risk_per_trade / risk_pct) if risk_pct else 0.0
    if score < 52:
        position_pct = 0.0
    elif score < 68:
        position_pct = min(raw_position_pct, max_position_pct * 0.35)
    elif score < 82:
        position_pct = min(raw_position_pct, max_position_pct * 0.7)
    else:
        position_pct = raw_position_pct
    position_pct = max(0.0, min(max_position_pct, position_pct))

    if pd.notna(ma20):
        risk_buffer = max(atr14 * 0.35, close * 0.01)
        buy_zone_low = min(close * 1.005, max(ma20 * 0.985, stop_loss + risk_buffer))
        buy_zone_high = max(buy_zone_low, min(close * 1.01, ma20 * 1.035))
        trailing_stop = max(stop_loss, ma20 * 0.97)
    else:
        buy_zone_low = close * 0.985
        buy_zone_high = close * 1.01
        trailing_stop = stop_loss

    take_profit_1 = close + risk_distance * 1.5
    take_profit_2 = close + risk_distance * 2.5

    if not sell_triggers:
        sell_triggers.append("跌破移动止盈或评分降至50以下")
    if not reasons:
        reasons.append("暂无明确优势信号")

    return SignalReport(
        symbol=symbol,
        name=name,
        date=_latest_date(last),
        close=close,
        score=score,
        rating=rating,
        action=action,
        buy_zone_low=float(buy_zone_low),
        buy_zone_high=float(buy_zone_high),
        stop_loss=float(stop_loss),
        take_profit_1=float(take_profit_1),
        take_profit_2=float(take_profit_2),
        trailing_stop=float(trailing_stop),
        position_pct=float(position_pct),
        reasons=reasons,
        sell_triggers=sell_triggers,
        warnings=warnings,
    )


def reports_to_frame(reports: list[SignalReport]) -> pd.DataFrame:
    if not reports:
        return pd.DataFrame()
    return pd.DataFrame([report.as_dict() for report in reports]).sort_values(
        ["评分", "收盘价"],
        ascending=[False, True],
    )
