from __future__ import annotations

import numpy as np
import pandas as pd


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = denominator.replace(0, np.nan)
    return numerator / denominator


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = _safe_divide(avg_gain, avg_loss)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(window, min_periods=window).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out = out.sort_values("date" if "date" in out.columns else out.columns[0]).reset_index(drop=True)
    close = out["close"]
    volume = out["volume"].fillna(0)

    for window in (5, 10, 20, 60, 120):
        out[f"ma{window}"] = close.rolling(window, min_periods=max(3, window // 2)).mean()

    out["ema12"] = close.ewm(span=12, adjust=False).mean()
    out["ema26"] = close.ewm(span=26, adjust=False).mean()
    out["macd"] = out["ema12"] - out["ema26"]
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]

    out["rsi14"] = rsi(close, 14)
    out["atr14"] = atr(out, 14)
    out["atr_pct"] = out["atr14"] / close

    out["volume_ma20"] = volume.rolling(20, min_periods=5).mean()
    out["volume_ratio"] = _safe_divide(volume, out["volume_ma20"]).replace([np.inf, -np.inf], np.nan)

    out["high_20"] = out["high"].rolling(20, min_periods=10).max()
    out["low_20"] = out["low"].rolling(20, min_periods=10).min()
    out["high_60"] = out["high"].rolling(60, min_periods=30).max()
    out["low_60"] = out["low"].rolling(60, min_periods=30).min()

    middle = out["ma20"]
    std20 = close.rolling(20, min_periods=10).std()
    out["boll_mid"] = middle
    out["boll_upper"] = middle + 2 * std20
    out["boll_lower"] = middle - 2 * std20

    out["return_1d"] = close.pct_change()
    out["return_5d"] = close.pct_change(5)
    out["return_20d"] = close.pct_change(20)
    out["ma20_slope"] = out["ma20"].pct_change(5)
    out["ma60_slope"] = out["ma60"].pct_change(10)
    return out


def crossed_above(series: pd.Series, baseline: pd.Series) -> pd.Series:
    return (series > baseline) & (series.shift(1) <= baseline.shift(1))


def crossed_below(series: pd.Series, baseline: pd.Series) -> pd.Series:
    return (series < baseline) & (series.shift(1) >= baseline.shift(1))
