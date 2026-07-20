from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import time
from typing import Any, Callable

import numpy as np
import pandas as pd

from .data import get_akshare, plain_code
from .settings import CACHE_DIR


FLOW_PERIODS = {
    "3日排行": "net_inflow_3d",
    "5日排行": "net_inflow_5d",
    "10日排行": "net_inflow_10d",
}

HOT_PERIODS = {
    "即时": "daily",
    "5日排行": "weekly",
    "20日排行": "monthly",
}

INDUSTRY_ALIASES = {
    "电力": ("电力", "公用事业", "电网", "风电", "光伏", "能源"),
    "机器人": ("机器人", "自动化", "专用设备", "通用设备", "机械设备", "工业母机"),
    "半导体": ("半导体", "芯片", "电子", "元件"),
    "人工智能": ("人工智能", "软件", "计算机", "通信", "互联网"),
    "新能源": ("新能源", "电池", "光伏", "风电", "储能", "电力设备"),
    "医药": ("医药", "医疗", "生物", "中药"),
    "消费": ("食品", "饮料", "家电", "零售", "旅游", "纺织"),
    "金融": ("银行", "证券", "保险", "多元金融"),
    "军工": ("军工", "航空", "航天", "国防"),
    "有色资源": ("有色", "金属", "煤炭", "石油", "矿业", "资源"),
}
FUNDAMENTAL_CACHE_PATH = CACHE_DIR / "fundamental_latest.pkl"
FUNDAMENTAL_CACHE_TTL_SECONDS = 20 * 60


@dataclass
class FundamentalSnapshot:
    universe: pd.DataFrame
    hotspots: pd.DataFrame
    report_date: str
    errors: list[str]


def _refresh_cached_market_fields(
    universe: pd.DataFrame,
    spot: pd.DataFrame,
) -> pd.DataFrame:
    """Combine cached factors with the latest quote fields from ``spot``.

    The fundamental cache intentionally lives longer than a spot quote.  Keeping
    quote columns from that cache can leave price/change data stale or empty and
    make the market filter reject every otherwise valid candidate.
    """
    if universe.empty or spot.empty:
        return universe.copy()

    current = spot.copy()
    cached = universe.copy()
    if "code" not in current.columns and "symbol" in current.columns:
        current["code"] = current["symbol"].map(normalize_code)
    if "code" not in cached.columns:
        return universe.copy()

    current["code"] = current["code"].map(normalize_code)
    cached["code"] = cached["code"].map(normalize_code)
    current = current.drop_duplicates("code", keep="last")
    cached = cached.drop_duplicates("code", keep="last")

    # Columns present in the current spot frame belong to the quote snapshot.
    # Drop their stale cached copies before joining the longer-lived factors.
    overlapping_quote_columns = [
        column
        for column in cached.columns
        if column != "code" and column in current.columns
    ]
    factors = cached.drop(columns=overlapping_quote_columns)
    refreshed = current.merge(factors, on="code", how="left", validate="one_to_one")
    refreshed.attrs.update(spot.attrs)
    return refreshed


def normalize_code(value: Any) -> str:
    return plain_code(str(value)).zfill(6)


def money_to_yuan(value: Any) -> float:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return float("nan")
    if isinstance(value, (int, float, np.number)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "nan", "None"}:
        return float("nan")
    multiplier = 1.0
    if text.endswith("亿"):
        multiplier = 100_000_000.0
        text = text[:-1]
    elif text.endswith("万"):
        multiplier = 10_000.0
        text = text[:-1]
    elif text.endswith("元"):
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return float("nan")


def _numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = frame.copy()
    for column in columns:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    return out


def _report_candidates(today: date | None = None) -> list[str]:
    current = today or date.today()
    candidates: list[str] = []
    for year in range(current.year, current.year - 3, -1):
        for suffix in ("1231", "0930", "0630", "0331"):
            value = f"{year}{suffix}"
            if value <= current.strftime("%Y%m%d"):
                candidates.append(value)
    return sorted(candidates, reverse=True)


def _fetch_latest_report(
    fetcher: Callable[..., pd.DataFrame],
    errors: list[str],
    label: str,
) -> tuple[pd.DataFrame, str]:
    last_error = ""
    for report_date in _report_candidates():
        try:
            frame = fetcher(date=report_date)
            if frame is not None and not frame.empty:
                return frame, report_date
        except Exception as exc:
            last_error = str(exc)
    errors.append(f"{label}获取失败：{last_error or '没有可用报告期'}")
    return pd.DataFrame(), ""


def _fetch_flow_period(ak: Any, symbol: str, column: str) -> pd.DataFrame:
    raw = ak.stock_fund_flow_individual(symbol=symbol)
    if raw.empty:
        return pd.DataFrame(columns=["code", column])
    frame = raw.rename(
        columns={
            "股票代码": "code",
            "股票简称": "flow_name",
            "资金流入净额": column,
        }
    ).copy()
    frame["code"] = frame["code"].map(normalize_code)
    frame[column] = frame[column].map(money_to_yuan)
    return frame[["code", column]].drop_duplicates("code")


def fetch_fund_flows(ak: Any, errors: list[str]) -> pd.DataFrame:
    frames: dict[str, pd.DataFrame] = {}
    for symbol, column in FLOW_PERIODS.items():
        last_error = ""
        for attempt in range(2):
            try:
                frame = _fetch_flow_period(ak, symbol, column)
                if len(frame) >= 1000:
                    frames[column] = frame
                    break
            except Exception as exc:
                last_error = str(exc)
            time.sleep(2 * (attempt + 1))
        if column not in frames:
            errors.append(f"{symbol}主力资金获取失败：{last_error or '返回数据不完整'}")

    merged = pd.DataFrame(columns=["code"])
    for column in FLOW_PERIODS.values():
        frame = frames.get(column, pd.DataFrame(columns=["code", column]))
        merged = frame if merged.empty else merged.merge(frame, on="code", how="outer")
    return merged


def _normalize_hot_frame(raw: pd.DataFrame, period: str, category: str) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    name_column = "行业" if "行业" in raw.columns else "概念"
    if name_column not in raw.columns:
        return pd.DataFrame()
    frame = raw.rename(
        columns={
            name_column: "name",
            "净额": "net_inflow_yi",
            "阶段涨跌幅": "change_pct",
            "行业-涨跌幅": "change_pct",
            "公司家数": "stock_count",
            "领涨股": "leader",
            "领涨股-涨跌幅": "leader_change_pct",
        }
    ).copy()
    for column in ("net_inflow_yi", "change_pct", "stock_count", "leader_change_pct"):
        if column not in frame.columns:
            frame[column] = np.nan
    frame = _numeric(frame, ["net_inflow_yi", "change_pct", "stock_count", "leader_change_pct"])
    frame["period"] = period
    frame["category"] = category
    frame["rank"] = frame["net_inflow_yi"].rank(method="first", ascending=False).astype("Int64")
    keep = [
        "period",
        "category",
        "rank",
        "name",
        "net_inflow_yi",
        "change_pct",
        "stock_count",
        "leader",
        "leader_change_pct",
    ]
    for column in keep:
        if column not in frame.columns:
            frame[column] = np.nan
    return frame[keep].sort_values("net_inflow_yi", ascending=False).head(30)


def fetch_capital_hotspots(ak: Any, errors: list[str]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for source, category in (
        (ak.stock_fund_flow_industry, "行业"),
        (ak.stock_fund_flow_concept, "题材"),
    ):
        for api_period, period in HOT_PERIODS.items():
            try:
                raw = source(symbol=api_period)
                normalized = _normalize_hot_frame(raw, period, category)
                if not normalized.empty:
                    frames.append(normalized)
            except Exception as exc:
                errors.append(f"{category}{api_period}资金热点获取失败：{exc}")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _financial_frame(ak: Any, errors: list[str]) -> tuple[pd.DataFrame, str]:
    performance, performance_date = _fetch_latest_report(ak.stock_yjbb_em, errors, "业绩报表")
    cashflow, cashflow_date = _fetch_latest_report(ak.stock_xjll_em, errors, "现金流量表")

    if performance.empty:
        performance_frame = pd.DataFrame(columns=["code"])
    else:
        performance_frame = performance.rename(
            columns={
                "股票代码": "code",
                "股票简称": "financial_name",
                "每股收益": "eps",
                "每股净资产": "book_value_per_share",
                "净资产收益率": "roe",
                "每股经营现金流量": "operating_cash_flow_per_share",
                "所处行业": "financial_industry",
            }
        ).copy()
        performance_frame["code"] = performance_frame["code"].map(normalize_code)
        performance_frame = _numeric(
            performance_frame,
            ["eps", "book_value_per_share", "roe", "operating_cash_flow_per_share"],
        )
        keep = [
            "code",
            "financial_name",
            "eps",
            "book_value_per_share",
            "roe",
            "operating_cash_flow_per_share",
            "financial_industry",
        ]
        performance_frame = performance_frame[keep].drop_duplicates("code")

    if cashflow.empty:
        cashflow_frame = pd.DataFrame(columns=["code"])
    else:
        cashflow_frame = cashflow.rename(
            columns={
                "股票代码": "code",
                "经营性现金流-现金流量净额": "operating_cash_flow",
                "经营性现金流-净现金流占比": "operating_cash_flow_ratio",
            }
        ).copy()
        cashflow_frame["code"] = cashflow_frame["code"].map(normalize_code)
        cashflow_frame = _numeric(cashflow_frame, ["operating_cash_flow", "operating_cash_flow_ratio"])
        cashflow_frame = cashflow_frame[
            ["code", "operating_cash_flow", "operating_cash_flow_ratio"]
        ].drop_duplicates("code")

    merged = performance_frame.merge(cashflow_frame, on="code", how="outer")
    return merged, performance_date or cashflow_date


def _dividend_frame(ak: Any, errors: list[str]) -> pd.DataFrame:
    annual_frames: list[pd.DataFrame] = []
    completed_year = date.today().year - 1
    years = list(range(completed_year, completed_year - 4, -1))
    successful_years = 0
    for year in years:
        try:
            raw = ak.stock_fhps_em(date=f"{year}1231")
        except Exception as exc:
            errors.append(f"{year}年度分红获取失败：{exc}")
            continue
        if raw.empty or "代码" not in raw.columns:
            continue
        successful_years += 1
        frame = raw.rename(
            columns={
                "代码": "code",
                "现金分红-现金分红比例": "cash_dividend",
                "现金分红-股息率": "dividend_yield",
            }
        ).copy()
        frame["code"] = frame["code"].map(normalize_code)
        frame = _numeric(frame, ["cash_dividend", "dividend_yield"])
        frame = frame[(frame["cash_dividend"] > 0) | (frame["dividend_yield"] > 0)].copy()
        frame["dividend_year"] = year
        annual_frames.append(frame[["code", "dividend_year", "cash_dividend", "dividend_yield"]])

    if not annual_frames or successful_years == 0:
        errors.append("历史年度分红数据暂不可用")
        return pd.DataFrame(columns=["code"])

    combined = pd.concat(annual_frames, ignore_index=True).drop_duplicates(["code", "dividend_year"])
    grouped = combined.groupby("code", as_index=False).agg(
        dividend_count=("dividend_year", "nunique"),
        annual_dividend=("cash_dividend", "mean"),
        average_dividend_yield=("dividend_yield", "mean"),
    )
    grouped["dividend_year_ratio"] = (grouped["dividend_count"] / successful_years).clip(upper=1)
    grouped["dividend_stable"] = (
        (grouped["dividend_count"] >= min(4, successful_years))
        & (grouped["dividend_year_ratio"] >= 0.8)
    )
    return grouped


def industry_matches(value: Any, selected: str) -> bool:
    if not selected or selected == "全部":
        return True
    industry = str(value or "")
    keywords = INDUSTRY_ALIASES.get(selected, (selected,))
    return any(keyword in industry for keyword in keywords)


def _add_factor_scores(frame: pd.DataFrame, report_date: str, min_inflow: float) -> pd.DataFrame:
    out = frame.copy()
    for column in FLOW_PERIODS.values():
        if column not in out.columns:
            out[column] = np.nan
    for column in (
        "eps",
        "book_value_per_share",
        "roe",
        "operating_cash_flow_per_share",
        "operating_cash_flow",
        "dividend_count",
        "dividend_year_ratio",
    ):
        if column not in out.columns:
            out[column] = np.nan

    annual_factor = 1.0
    if report_date.endswith("0331"):
        annual_factor = 4.0
    elif report_date.endswith("0630"):
        annual_factor = 2.0
    elif report_date.endswith("0930"):
        annual_factor = 4.0 / 3.0

    out["pe_est"] = out["price"] / (out["eps"] * annual_factor)
    out["pb_est"] = out["price"] / out["book_value_per_share"]
    out.loc[(out["pe_est"] <= 0) | ~np.isfinite(out["pe_est"]), "pe_est"] = np.nan
    out.loc[(out["pb_est"] <= 0) | ~np.isfinite(out["pb_est"]), "pb_est"] = np.nan
    out["financial_industry"] = out["financial_industry"].fillna("未分类").astype(str)
    out["industry_pe_median"] = out.groupby("financial_industry")["pe_est"].transform("median")
    out["industry_pb_median"] = out.groupby("financial_industry")["pb_est"].transform("median")

    pe_ratio = out["pe_est"] / out["industry_pe_median"]
    pb_ratio = out["pb_est"] / out["industry_pb_median"]
    out["valuation_normal"] = pe_ratio.between(0.45, 1.8) & pb_ratio.between(0.45, 1.8)
    out["cashflow_good"] = (
        (out["operating_cash_flow"] > 0)
        & (out["operating_cash_flow_per_share"].fillna(0) > 0)
    )
    out["fund_flow_pass"] = (
        (out["net_inflow_3d"] >= min_inflow)
        & (out["net_inflow_5d"] >= min_inflow)
        & (out["net_inflow_10d"] >= min_inflow)
    )
    if "dividend_stable" not in out.columns:
        out["dividend_stable"] = False
    out["dividend_stable"] = out["dividend_stable"].fillna(False).astype(bool)

    flow_score = sum(
        out[column].rank(pct=True, na_option="bottom").fillna(0) * 15
        for column in FLOW_PERIODS.values()
    )
    out["fundamental_score"] = (
        flow_score
        + out["valuation_normal"].astype(float) * 15
        + out["cashflow_good"].astype(float) * 15
        + out["dividend_stable"].astype(float) * 15
        + out["roe"].rank(pct=True, na_option="bottom").fillna(0) * 10
    ).clip(0, 100)
    return out


def fetch_fundamental_snapshot(
    spot: pd.DataFrame,
    min_inflow: float = 100_000_000,
    ignore_proxy: bool = True,
) -> FundamentalSnapshot:
    if FUNDAMENTAL_CACHE_PATH.exists():
        age = time.time() - FUNDAMENTAL_CACHE_PATH.stat().st_mtime
        if age <= FUNDAMENTAL_CACHE_TTL_SECONDS:
            try:
                cached = pd.read_pickle(FUNDAMENTAL_CACHE_PATH)
                universe = cached["universe"].copy()
                latest_acceptable_report = f"{date.today().year - 1}1231"
                flow_complete = all(
                    column in universe.columns and universe[column].notna().sum() >= 1000
                    for column in FLOW_PERIODS.values()
                )
                if (
                    len(universe) < 5000
                    or not flow_complete
                    or str(cached.get("report_date", "")) < latest_acceptable_report
                ):
                    raise ValueError("缓存完整性校验未通过")
                universe = _refresh_cached_market_fields(universe, spot)
                universe = _add_factor_scores(
                    universe,
                    str(cached.get("report_date", "")),
                    min_inflow=min_inflow,
                )
                return FundamentalSnapshot(
                    universe=universe,
                    hotspots=cached.get("hotspots", pd.DataFrame()).copy(),
                    report_date=str(cached.get("report_date", "")),
                    errors=["资金面与基本面使用20分钟内的完整缓存快照"],
                )
            except Exception:
                pass

    ak = get_akshare(ignore_proxy=ignore_proxy)
    errors: list[str] = []

    flows = fetch_fund_flows(ak, errors)
    financials, report_date = _financial_frame(ak, errors)
    dividends = _dividend_frame(ak, errors)

    universe = spot.copy()
    universe["code"] = universe["code"].map(normalize_code)
    for extra in (flows, financials, dividends):
        if not extra.empty:
            universe = universe.merge(extra, on="code", how="left")
    if "financial_industry" not in universe.columns:
        universe["financial_industry"] = "未分类"
    universe = _add_factor_scores(universe, report_date, min_inflow=min_inflow)

    hotspots = fetch_capital_hotspots(ak, errors)
    snapshot = FundamentalSnapshot(
        universe=universe,
        hotspots=hotspots,
        report_date=report_date,
        errors=errors,
    )
    latest_acceptable_report = f"{date.today().year - 1}1231"
    flow_complete = all(
        column in universe.columns and universe[column].notna().sum() >= 1000
        for column in FLOW_PERIODS.values()
    )
    if len(universe) >= 5000 and flow_complete and report_date >= latest_acceptable_report:
        try:
            FUNDAMENTAL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            pd.to_pickle(
                {
                    "universe": universe,
                    "hotspots": hotspots,
                    "report_date": report_date,
                },
                FUNDAMENTAL_CACHE_PATH,
            )
        except Exception:
            pass
    return snapshot
