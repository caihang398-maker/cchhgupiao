from __future__ import annotations

import os
import queue
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .settings import CACHE_DIR


PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
ORIGINAL_PROXY_ENV = {key: os.environ.get(key) for key in PROXY_ENV_KEYS}
ORIGINAL_REQUESTS_INIT: Any | None = None
SPOT_CACHE_PATH = CACHE_DIR / "spot_latest.pkl"
DAILY_CACHE_DIR = CACHE_DIR / "daily"
MINUTE_CACHE_DIR = CACHE_DIR / "minute"


class DataSourceError(RuntimeError):
    """Raised when all configured market data sources fail."""


def _run_with_timeout(callback: Any, timeout: float) -> tuple[Any | None, str]:
    result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def runner() -> None:
        try:
            result_queue.put((True, callback()))
        except Exception as exc:
            result_queue.put((False, exc))

    threading.Thread(target=runner, daemon=True).start()
    try:
        succeeded, value = result_queue.get(timeout=timeout)
    except queue.Empty:
        return None, "数据源响应超时"
    if succeeded:
        return value, ""
    return None, str(value)


def configure_network(ignore_proxy: bool = False) -> None:
    """Optionally keep Python requests from inheriting a broken local proxy."""
    global ORIGINAL_REQUESTS_INIT

    if not ignore_proxy:
        for key, value in ORIGINAL_PROXY_ENV.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        os.environ.pop("NO_PROXY", None)
        os.environ.pop("no_proxy", None)
        if ORIGINAL_REQUESTS_INIT is not None:
            requests.Session.__init__ = ORIGINAL_REQUESTS_INIT
            requests.Session._stock_quant_no_proxy_patch = False
        return

    for key in PROXY_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"

    if getattr(requests.Session, "_stock_quant_no_proxy_patch", False):
        return

    if ORIGINAL_REQUESTS_INIT is None:
        ORIGINAL_REQUESTS_INIT = requests.Session.__init__
    original_init = ORIGINAL_REQUESTS_INIT

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.trust_env = False

    requests.Session.__init__ = patched_init
    requests.Session._stock_quant_no_proxy_patch = True


def get_akshare(ignore_proxy: bool = False):
    configure_network(ignore_proxy=ignore_proxy)
    import akshare as ak

    return ak


def today_yyyymmdd() -> str:
    return date.today().strftime("%Y%m%d")


def yyyymmdd(value: date | datetime | str | None) -> str:
    if value is None:
        return today_yyyymmdd()
    if isinstance(value, datetime):
        return value.date().strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    return value.replace("-", "")


def plain_code(symbol: str) -> str:
    raw = str(symbol).strip().lower()
    raw = raw.replace(".", "")
    for prefix in ("sh", "sz", "bj"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
    return "".join(ch for ch in raw if ch.isdigit())[-6:]


def infer_exchange(symbol: str) -> str:
    raw = str(symbol).strip().lower()
    if raw.startswith(("sh", "sz", "bj")):
        return raw[:2]

    code = plain_code(symbol)
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith(("4", "8")):
        return "bj"
    return "sz"


def prefixed_symbol(symbol: str) -> str:
    code = plain_code(symbol)
    if len(code) != 6:
        raise ValueError(f"无效的A股代码：{symbol}")
    return f"{infer_exchange(symbol)}{code}"


def _numeric(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def _normalize_spot_volume_lots(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize spot volume to lots so it matches the daily history sources."""
    out = df.copy()
    volume = pd.to_numeric(out.get("volume"), errors="coerce")
    price = pd.to_numeric(out.get("price"), errors="coerce")
    turnover = pd.to_numeric(out.get("turnover"), errors="coerce")
    implied_unit = (turnover / (price * volume)).replace([float("inf"), -float("inf")], pd.NA)
    median_unit = pd.to_numeric(implied_unit, errors="coerce").dropna().median()
    if pd.notna(median_unit) and 0.2 <= float(median_unit) <= 5:
        out["volume"] = volume / 100
    else:
        out["volume"] = volume
    out.attrs.update(df.attrs)
    out.attrs["volume_unit"] = "lot"
    return out


def _fetch_spot_once(ignore_proxy: bool = False, allow_cache: bool = True) -> pd.DataFrame:
    """Fetch A-share spot quotes with Sina retries and an Eastmoney fallback."""
    ak = get_akshare(ignore_proxy=ignore_proxy)
    errors: list[str] = []
    raw = pd.DataFrame()
    fallback_warning = ""
    for attempt in range(3):
        try:
            raw = ak.stock_zh_a_spot()
            if len(raw) >= 1000:
                break
            errors.append(f"新浪源第{attempt + 1}次返回不完整：{len(raw)}只")
            raw = pd.DataFrame()
        except Exception as exc:
            errors.append(f"新浪源第{attempt + 1}次失败：{exc}")
        time.sleep(1.5 * (attempt + 1))

    if raw.empty:
        try:
            raw = ak.stock_zh_a_spot_em()
            if len(raw) < 1000:
                errors.append(f"东方财富备用源返回不完整：{len(raw)}只")
                raw = pd.DataFrame()
        except Exception as exc:
            errors.append(f"东方财富备用源失败：{exc}")
        if raw.empty:
            try:
                flow_spot = ak.stock_fund_flow_individual(symbol="即时")
                if len(flow_spot) >= 1000:
                    raw = flow_spot.rename(
                        columns={
                            "股票代码": "代码",
                            "股票简称": "名称",
                            "最新价": "最新价",
                            "涨跌幅": "涨跌幅",
                        }
                    ).copy()
                    raw["昨收"] = pd.to_numeric(raw["最新价"], errors="coerce") / (
                        1 + pd.to_numeric(raw["涨跌幅"], errors="coerce").fillna(0) / 100
                    )
                    raw["今开"] = raw["昨收"]
                    raw["最高"] = raw[["最新价", "昨收"]].max(axis=1)
                    raw["最低"] = raw[["最新价", "昨收"]].min(axis=1)
                    raw["成交额"] = 100_000_000.0
                    raw["成交量"] = pd.NA
                    fallback_warning = "实时行情源暂不可用，使用同花顺即时资金流行情完成扫描"
                else:
                    errors.append(f"同花顺即时行情返回不完整：{len(flow_spot)}只")
            except Exception as exc:
                errors.append(f"同花顺即时行情备用源失败：{exc}")
        if raw.empty and allow_cache and SPOT_CACHE_PATH.exists():
            try:
                cached = pd.read_pickle(SPOT_CACHE_PATH)
                if len(cached) >= 1000:
                    cached = _normalize_spot_volume_lots(cached)
                    cached.attrs["source_warning"] = "实时行情源暂不可用，已使用最近一次完整行情快照"
                    return cached
            except Exception as exc:
                errors.append(f"本地行情快照读取失败：{exc}")
        if raw.empty:
            raise DataSourceError("无法获取实时行情：" + "；".join(errors))

    if raw.empty:
        raise DataSourceError("实时行情返回为空")

    rename_map = {
        "代码": "symbol",
        "名称": "name",
        "最新价": "price",
        "涨跌额": "change_amount",
        "涨跌幅": "change_pct",
        "买入": "bid",
        "卖出": "ask",
        "昨收": "prev_close",
        "今开": "open",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "turnover",
        "时间戳": "quote_time",
        "今开": "open",
        "最高": "high",
        "最低": "low",
        "昨收": "prev_close",
    }
    df = raw.rename(columns=rename_map).copy()
    expected = [
        "symbol",
        "name",
        "price",
        "change_amount",
        "change_pct",
        "bid",
        "ask",
        "prev_close",
        "open",
        "high",
        "low",
        "volume",
        "turnover",
        "quote_time",
    ]
    for column in expected:
        if column not in df.columns:
            df[column] = pd.NA

    df = df[expected]
    df["symbol"] = df["symbol"].astype(str).str.lower()
    short_code = df["symbol"].str.replace(r"\D", "", regex=True).str.zfill(6)
    missing_prefix = ~df["symbol"].str.startswith(("sh", "sz", "bj"))
    df.loc[missing_prefix, "symbol"] = short_code[missing_prefix].map(prefixed_symbol)
    df["code"] = df["symbol"].map(plain_code)
    df["exchange"] = df["symbol"].map(infer_exchange)
    df = _numeric(
        df,
        [
            "price",
            "change_amount",
            "change_pct",
            "bid",
            "ask",
            "prev_close",
            "open",
            "high",
            "low",
            "volume",
            "turnover",
        ],
    )
    df = _normalize_spot_volume_lots(df)
    df["turnover_yi"] = df["turnover"] / 100_000_000
    df["display"] = df["symbol"].str.upper() + " " + df["name"].fillna("")
    if len(df) >= 1000:
        try:
            SPOT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            df.to_pickle(SPOT_CACHE_PATH)
        except Exception:
            pass
    if fallback_warning:
        df.attrs["source_warning"] = fallback_warning
    return df


def fetch_spot(ignore_proxy: bool = False) -> pd.DataFrame:
    """Fetch spot quotes and automatically retry with the opposite proxy mode."""
    errors: list[str] = []
    for bypass_proxy in dict.fromkeys([ignore_proxy, not ignore_proxy]):
        try:
            frame = _fetch_spot_once(ignore_proxy=bypass_proxy, allow_cache=False)
            if bypass_proxy != ignore_proxy:
                frame.attrs["source_warning"] = (
                    str(frame.attrs.get("source_warning") or "")
                    + "；系统已自动切换网络连接方式"
                ).strip("；")
            return frame
        except Exception as exc:
            errors.append(f"{'直连' if bypass_proxy else '系统代理'}：{exc}")
    if SPOT_CACHE_PATH.exists():
        try:
            cached = pd.read_pickle(SPOT_CACHE_PATH)
            if len(cached) >= 1000:
                cached = _normalize_spot_volume_lots(cached)
                cached.attrs["source_warning"] = "实时行情源暂不可用，已使用最近一次完整行情快照"
                return cached
        except Exception as exc:
            errors.append(f"本地行情快照：{exc}")
    raise DataSourceError("无法获取实时行情：" + "；".join(errors[-2:]))


def _clean_daily_history(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return raw

    rename_map = {
        "date": "date",
        "open": "open",
        "close": "close",
        "high": "high",
        "low": "low",
        "amount": "volume",
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "turnover",
        "振幅": "amplitude",
        "涨跌幅": "change_pct",
        "涨跌额": "change_amount",
        "换手率": "turnover_rate",
    }
    df = raw.rename(columns=rename_map).copy()
    if "volume" not in df.columns:
        df["volume"] = pd.NA
    if "turnover" not in df.columns:
        df["turnover"] = pd.NA

    columns = ["date", "open", "close", "high", "low", "volume", "turnover"]
    for column in columns:
        if column not in df.columns:
            df[column] = pd.NA

    df = df[columns]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = _numeric(df, ["open", "close", "high", "low", "volume", "turnover"])
    df = df.dropna(subset=["date", "open", "close", "high", "low"])
    df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df


def _fetch_daily_history_once(
    symbol: str,
    start_date: date | datetime | str | None = None,
    end_date: date | datetime | str | None = None,
    adjust: str = "qfq",
    ignore_proxy: bool = False,
) -> pd.DataFrame:
    """Fetch daily bars, preferring Tencent and falling back to Eastmoney."""
    ak = get_akshare(ignore_proxy=ignore_proxy)
    end = yyyymmdd(end_date)
    if start_date is None:
        start = (date.today() - timedelta(days=520)).strftime("%Y%m%d")
    else:
        start = yyyymmdd(start_date)

    errors: list[str] = []
    tx_adjust = "" if adjust in ("", "none", None) else adjust
    try:
        raw = ak.stock_zh_a_hist_tx(
            symbol=prefixed_symbol(symbol),
            start_date=start,
            end_date=end,
            adjust=tx_adjust,
        )
        cleaned = _clean_daily_history(raw)
        if not cleaned.empty:
            return cleaned
        errors.append("腾讯源返回为空")
    except Exception as exc:
        errors.append(f"腾讯源失败：{exc}")

    em_adjust = "" if adjust in ("", "none", None) else adjust
    try:
        raw = ak.stock_zh_a_hist(
            symbol=plain_code(symbol),
            period="daily",
            start_date=start,
            end_date=end,
            adjust=em_adjust,
        )
        cleaned = _clean_daily_history(raw)
        if not cleaned.empty:
            return cleaned
        errors.append("东方财富源返回为空")
    except Exception as exc:
        errors.append(f"东方财富源失败：{exc}")

    raise DataSourceError(f"无法获取 {symbol} 日K；" + "；".join(errors))


def fetch_daily_history(
    symbol: str,
    start_date: date | datetime | str | None = None,
    end_date: date | datetime | str | None = None,
    adjust: str = "qfq",
    ignore_proxy: bool = False,
) -> pd.DataFrame:
    """Fetch daily bars with network failover and an exact-request cache."""
    code = plain_code(symbol)
    start_key = yyyymmdd(start_date) if start_date is not None else "default"
    end_key = yyyymmdd(end_date) if end_date is not None else "today"
    adjust_key = str(adjust or "none").lower()
    cache_path = DAILY_CACHE_DIR / f"{code}_{start_key}_{end_key}_{adjust_key}.pkl"
    errors: list[str] = []
    for bypass_proxy in dict.fromkeys([ignore_proxy, not ignore_proxy]):
        try:
            frame = _fetch_daily_history_once(
                symbol,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
                ignore_proxy=bypass_proxy,
            )
            if bypass_proxy != ignore_proxy:
                frame.attrs["source_warning"] = "日K已自动切换网络连接方式"
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                frame.to_pickle(cache_path)
            except Exception:
                pass
            return frame
        except Exception as exc:
            errors.append(f"{'直连' if bypass_proxy else '系统代理'}：{exc}")
    if cache_path.exists():
        try:
            cached = pd.read_pickle(cache_path)
            if isinstance(cached, pd.DataFrame) and not cached.empty:
                cached.attrs["source_warning"] = "实时日K暂不可用，显示最近一次成功缓存"
                return cached
        except Exception as exc:
            errors.append(f"本地缓存：{exc}")
    raise DataSourceError(f"无法获取 {symbol} 日K；" + "；".join(errors[-2:]))


def _fetch_minute_history_once(
    symbol: str,
    period: str = "5",
    adjust: str = "",
    ignore_proxy: bool = False,
) -> pd.DataFrame:
    ak = get_akshare(ignore_proxy=ignore_proxy)
    try:
        raw = ak.stock_zh_a_hist_min_em(
            symbol=plain_code(symbol),
            period=period,
            adjust=adjust,
        )
    except Exception as exc:
        raise DataSourceError(f"无法获取 {symbol} 分钟行情：{exc}") from exc

    if raw.empty:
        raise DataSourceError(f"{symbol} 分钟行情返回为空")

    rename_map = {
        "时间": "time",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "turnover",
        "涨跌幅": "change_pct",
        "涨跌额": "change_amount",
        "振幅": "amplitude",
        "换手率": "turnover_rate",
    }
    df = raw.rename(columns=rename_map).copy()
    keep = ["time", "open", "close", "high", "low", "volume", "turnover"]
    for column in keep:
        if column not in df.columns:
            df[column] = pd.NA
    df = df[keep]
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = _numeric(df, ["open", "close", "high", "low", "volume", "turnover"])
    df = df.dropna(subset=["time", "open", "close", "high", "low"])
    return df.sort_values("time").reset_index(drop=True)


def fetch_minute_history(
    symbol: str,
    period: str = "5",
    adjust: str = "",
    ignore_proxy: bool = False,
) -> pd.DataFrame:
    """Fetch minute bars with network failover and a last-good local fallback."""
    code = plain_code(symbol)
    cache_path = MINUTE_CACHE_DIR / f"{code}_{period}_{adjust or 'none'}.pkl"
    errors: list[str] = []
    for bypass_proxy in dict.fromkeys([ignore_proxy, not ignore_proxy]):
        try:
            frame = _fetch_minute_history_once(
                symbol,
                period=period,
                adjust=adjust,
                ignore_proxy=bypass_proxy,
            )
            if not frame.empty:
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    frame.to_pickle(cache_path)
                except Exception:
                    pass
                if bypass_proxy != ignore_proxy:
                    frame.attrs["source_warning"] = "分钟行情已自动切换网络连接方式"
                return frame
        except Exception as exc:
            errors.append(f"{'直连' if bypass_proxy else '系统代理'}：{exc}")
    if cache_path.exists():
        try:
            cached = pd.read_pickle(cache_path)
            if isinstance(cached, pd.DataFrame) and not cached.empty:
                cached.attrs["source_warning"] = "实时分钟行情暂不可用，显示最近一次成功缓存"
                return cached
        except Exception as exc:
            errors.append(f"本地缓存：{exc}")
    raise DataSourceError(f"无法获取 {symbol} 分钟行情：" + "；".join(errors[-3:]))


def prepare_spot_universe(
    spot: pd.DataFrame,
    min_turnover: float = 50_000_000,
    min_price: float = 3.0,
    max_price: float = 120.0,
    min_change_pct: float = -3.0,
    max_change_pct: float = 7.5,
) -> pd.DataFrame:
    df = spot.copy()
    if df.empty:
        return df

    for column in ("price", "prev_close", "open", "turnover", "change_pct"):
        if column not in df.columns:
            df[column] = pd.NA
        df[column] = pd.to_numeric(df[column], errors="coerce")

    derived_change_pct = (
        (df["price"] / df["prev_close"] - 1) * 100
    ).where(df["prev_close"] > 0)
    df["change_pct"] = df["change_pct"].fillna(derived_change_pct)
    df["open"] = df["open"].where(df["open"] > 0, df["prev_close"])
    df["open"] = df["open"].where(df["open"] > 0, df["price"])

    name = df["name"].fillna("").astype(str)
    valid = ~name.str.contains("ST|退|N|C", case=False, regex=True)
    valid &= df["price"].between(min_price, max_price, inclusive="both")
    valid &= df["turnover"].fillna(0) >= min_turnover
    valid &= df["change_pct"].between(min_change_pct, max_change_pct, inclusive="both")
    valid &= df["code"].astype(str).str.len().eq(6)

    df = df.loc[valid].copy()
    if df.empty:
        return df

    df["liquidity_rank"] = df["turnover"].rank(pct=True)
    df["momentum_rank"] = df["change_pct"].rank(pct=True)
    df["open_strength"] = ((df["price"] - df["open"]) / df["open"]).replace([float("inf"), -float("inf")], 0)
    df["open_strength"] = df["open_strength"].fillna(0).clip(-0.08, 0.08)
    df["pre_score"] = (
        df["liquidity_rank"] * 45
        + df["momentum_rank"] * 35
        + ((df["open_strength"] + 0.08) / 0.16) * 20
    )
    return df.sort_values(["pre_score", "turnover"], ascending=False).reset_index(drop=True)


def append_spot_bar(history: pd.DataFrame, spot_row: pd.Series | dict[str, Any] | None) -> pd.DataFrame:
    """Append or update today's partial daily bar from a spot quote."""
    if spot_row is None:
        return history
    if date.today().weekday() >= 5:
        return history
    row = pd.Series(spot_row)
    price = pd.to_numeric(row.get("price"), errors="coerce")
    if pd.isna(price) or float(price) <= 0:
        return history

    today = pd.Timestamp(date.today())
    open_price = pd.to_numeric(row.get("open"), errors="coerce")
    high_price = pd.to_numeric(row.get("high"), errors="coerce")
    low_price = pd.to_numeric(row.get("low"), errors="coerce")
    volume = pd.to_numeric(row.get("volume"), errors="coerce")
    turnover = pd.to_numeric(row.get("turnover"), errors="coerce")

    bar = {
        "date": today,
        "open": float(open_price) if pd.notna(open_price) and open_price > 0 else float(price),
        "close": float(price),
        "high": float(high_price) if pd.notna(high_price) and high_price > 0 else float(price),
        "low": float(low_price) if pd.notna(low_price) and low_price > 0 else float(price),
        "volume": float(volume) if pd.notna(volume) else pd.NA,
        "turnover": float(turnover) if pd.notna(turnover) else pd.NA,
    }

    out = history.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    if not out.empty and pd.Timestamp(out["date"].iloc[-1]).normalize() == today:
        for key, value in bar.items():
            out.loc[out.index[-1], key] = value
    else:
        out = pd.concat([out, pd.DataFrame([bar])], ignore_index=True)
    return out.sort_values("date").reset_index(drop=True)


def market_breadth(spot: pd.DataFrame) -> dict[str, float | int | str]:
    valid = spot.dropna(subset=["price", "change_pct"]).copy()
    if valid.empty:
        return {}

    names = valid["name"].fillna("").astype(str)
    codes = valid["code"].fillna("").astype(str)
    change = valid["change_pct"].astype(float)
    limit_threshold = pd.Series(9.5, index=valid.index)
    limit_threshold.loc[names.str.contains("ST", case=False, regex=False)] = 4.8
    limit_threshold.loc[codes.str.startswith(("300", "301", "688"))] = 19.5
    limit_threshold.loc[codes.str.startswith(("4", "8", "92"))] = 29.5

    total = int(len(valid))
    up = int((change > 0).sum())
    down = int((change < 0).sum())
    flat = int((change == 0).sum())
    up_ratio = up / total if total else 0.0
    median_change = float(change.median())
    turnover = float(valid["turnover"].fillna(0).sum())
    limit_up = int((change >= limit_threshold).sum())
    limit_down = int((change <= -limit_threshold).sum())
    strong = int((change >= 5).sum())
    weak = int((change <= -5).sum())
    adv_dec_ratio = up / max(down, 1)
    temperature = max(0.0, min(100.0, up_ratio * 70 + (median_change + 3) / 6 * 30))
    if temperature >= 68:
        temperature_label = "偏强"
    elif temperature >= 45:
        temperature_label = "震荡"
    else:
        temperature_label = "偏弱"

    return {
        "total": total,
        "up": up,
        "down": down,
        "flat": flat,
        "up_ratio": up_ratio,
        "median_change": median_change,
        "turnover": turnover,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "strong": strong,
        "weak": weak,
        "adv_dec_ratio": adv_dec_ratio,
        "temperature": temperature,
        "temperature_label": temperature_label,
    }


def fetch_global_indices(ignore_proxy: bool = False) -> pd.DataFrame:
    """Fetch a compact global risk dashboard from Tencent quotes."""
    configure_network(ignore_proxy=ignore_proxy)
    symbols = "usDJI,usIXIC,usINX,hkHSI,jpN225"
    url = f"https://qt.gtimg.cn/q={symbols}"
    try:
        response = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com/"},
        )
        response.raise_for_status()
    except Exception as exc:
        raise DataSourceError(f"无法获取全球指数：{exc}") from exc

    rows: list[dict[str, Any]] = []
    for line in response.text.splitlines():
        match = re.search(r'v_([^=]+)="(.*)";', line.strip())
        if not match:
            continue
        source_symbol, payload = match.groups()
        fields = payload.split("~")
        if len(fields) < 33:
            continue
        try:
            rows.append(
                {
                    "symbol": source_symbol,
                    "name": fields[1],
                    "price": float(fields[3]),
                    "prev_close": float(fields[4]),
                    "change_amount": float(fields[31]),
                    "change_pct": float(fields[32]),
                    "quote_time": fields[30],
                }
            )
        except (TypeError, ValueError):
            continue

    frame = pd.DataFrame(rows)
    if frame.empty:
        raise DataSourceError("全球指数返回为空")
    return frame


def fetch_market_news(
    symbol: str,
    ignore_proxy: bool = False,
    timeout: float = 5.0,
) -> tuple[pd.DataFrame, list[str]]:
    """Fetch recent market and stock headlines without blocking the UI indefinitely."""
    ak = get_akshare(ignore_proxy=ignore_proxy)
    errors: list[str] = []
    frames: list[pd.DataFrame] = []

    global_raw, global_error = _run_with_timeout(ak.stock_info_global_cls, timeout)
    if isinstance(global_raw, pd.DataFrame) and not global_raw.empty:
        global_frame = global_raw.rename(
            columns={
                "标题": "title",
                "内容": "content",
                "发布日期": "published_at",
                "发布时间": "published_at",
            }
        ).copy()
        global_frame["source"] = "财联社快讯"
        frames.append(global_frame)
    else:
        errors.append(f"市场快讯暂缺：{global_error or '返回为空'}")

    stock_raw, stock_error = _run_with_timeout(
        lambda: ak.stock_news_em(symbol=plain_code(symbol)),
        timeout,
    )
    if isinstance(stock_raw, pd.DataFrame) and not stock_raw.empty:
        stock_frame = stock_raw.rename(
            columns={
                "新闻标题": "title",
                "新闻内容": "content",
                "发布时间": "published_at",
                "文章来源": "source",
                "新闻链接": "url",
            }
        ).copy()
        frames.append(stock_frame)
    else:
        errors.append(f"个股新闻暂缺：{stock_error or '返回为空'}")

    if not frames:
        return pd.DataFrame(columns=["title", "content", "published_at", "source", "url"]), errors

    news = pd.concat(frames, ignore_index=True, sort=False)
    for column in ("title", "content", "published_at", "source", "url"):
        if column not in news.columns:
            news[column] = ""
    news = news[["title", "content", "published_at", "source", "url"]].copy()
    news["title"] = news["title"].fillna("").astype(str).str.strip()
    news["content"] = news["content"].fillna("").astype(str).str.strip()
    news["source"] = news["source"].fillna("财经资讯").astype(str).str.strip()
    news["url"] = news["url"].fillna("").astype(str).str.strip()
    news["published_at"] = pd.to_datetime(news["published_at"], errors="coerce")
    news = news[news["title"].ne("")].drop_duplicates(subset=["title"])
    return news.sort_values("published_at", ascending=False, na_position="last").head(30).reset_index(drop=True), errors


def global_risk_summary(indices: pd.DataFrame) -> dict[str, float | int | str]:
    if indices.empty:
        return {}
    changes = pd.to_numeric(indices["change_pct"], errors="coerce").dropna()
    if changes.empty:
        return {}
    average = float(changes.mean())
    positive = int((changes > 0).sum())
    score = float(max(0, min(100, 50 + average * 12 + (positive / len(changes) - 0.5) * 30)))
    if score >= 62:
        label = "偏多"
    elif score >= 42:
        label = "中性"
    else:
        label = "偏空"
    return {
        "average_change": average,
        "positive_count": positive,
        "total_count": int(len(changes)),
        "score": score,
        "label": label,
    }
