from __future__ import annotations

import logging

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from stock_quant.presentation import (
    PLOTLY_CONFIG,
    chinese_date_axis,
    format_date,
)
from stock_quant.review import outcome_summary, refresh_recommendation_outcomes
from stock_quant.storage import load_recommendation_outcomes


LOGGER = logging.getLogger(__name__)


st.set_page_config(page_title="推荐复盘 - A股每日量化推荐", layout="wide")


def pct_text(value: object) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.2%}"


def metric_row(items: list[tuple[str, str]]) -> None:
    columns = st.columns(len(items))
    for column, (label, value) in zip(columns, items):
        column.metric(label, value)


def return_chart(frame: pd.DataFrame) -> go.Figure:
    data = frame.copy()
    return_columns = ["t1_return", "t5_return", "t20_return"]
    for column in return_columns:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    grouped = data.groupby("trade_date", as_index=False)[
        return_columns
    ].mean(numeric_only=True)
    grouped["trade_date"] = pd.to_datetime(grouped["trade_date"], errors="coerce")
    fig = go.Figure()
    for column, label, color in [
        ("t1_return", "推荐后1日", "#2563eb"),
        ("t5_return", "推荐后5日", "#16a34a"),
        ("t20_return", "推荐后20日", "#dc2626"),
    ]:
        fig.add_trace(
            go.Bar(
                x=grouped["trade_date"],
                y=grouped[column] * 100,
                name=label,
                marker_color=color,
                hovertemplate="推荐日期：%{x|%Y年%m月%d日}<br>平均收益：%{y:.2f}%<extra></extra>",
            )
        )
    fig.update_layout(
        height=390,
        template="plotly_white",
        barmode="group",
        margin=dict(l=10, r=10, t=20, b=10),
        yaxis_title="平均收益率（%）",
        xaxis_title="推荐日期",
        legend_title_text="观察周期",
    )
    chinese_date_axis(fig)
    return fig


def hit_chart(summary: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for column, label, color in [
        ("推荐后5日上涨率", "推荐后5日上涨率", "#2563eb"),
        ("推荐后20日上涨率", "推荐后20日上涨率", "#16a34a"),
        ("目标一命中率", "目标一命中率", "#f59e0b"),
        ("止损触发率", "止损触发率", "#dc2626"),
    ]:
        fig.add_trace(
            go.Bar(
                x=summary["周期"],
                y=pd.to_numeric(summary[column], errors="coerce") * 100,
                name=label,
                marker_color=color,
            )
        )
    fig.update_layout(
        height=390,
        template="plotly_white",
        barmode="group",
        margin=dict(l=10, r=10, t=20, b=10),
        yaxis_title="比例（%）",
        xaxis_title="推荐周期",
        legend_title_text="统计指标",
    )
    return fig


st.title("每日推荐自动复盘")
st.caption(
    "这里验证的是系统每天实际保存的推荐，不是单只股票的通用技术策略。"
    "同一天多次刷新时，只取该股票、该周期最后一次推荐，避免重复计算。"
)

st.info(
    "推荐后1日、5日、20日分别表示推荐之后第1、5、20个交易日的收盘收益。"
    "止损和目标位检查推荐后前20个交易日的最低价、最高价。未走够时间的数据标记为跟踪中。"
)

outcomes = load_recommendation_outcomes()
refresh_col, note_col = st.columns([0.24, 0.76])
with refresh_col:
    refresh_clicked = st.button("更新全部推荐复盘", type="primary", width="stretch")
with note_col:
    st.write("建议每个交易日收盘后更新；定时脚本也会自动执行。")

if refresh_clicked or outcomes.empty:
    progress = st.progress(0)
    status = st.empty()

    def on_progress(done: int, total: int, symbol: str) -> None:
        progress.progress(done / max(total, 1))
        status.write(f"正在更新 {done}/{total}：{symbol}")

    try:
        with st.spinner("正在下载推荐后的日K并计算复盘结果"):
            _updated, errors = refresh_recommendation_outcomes(
                ignore_proxy=False,
                progress_callback=on_progress,
            )
        outcomes = load_recommendation_outcomes()
        st.success(f"复盘已更新，共 {len(outcomes)} 个推荐样本。")
        if errors:
            with st.expander(f"查看 {len(errors)} 条数据源提示"):
                st.write("\n".join(errors[:100]))
    except Exception:
        LOGGER.exception("推荐复盘更新失败")
        st.error("复盘更新失败，系统已记录详细信息，请稍后重试。")
    finally:
        progress.empty()
        status.empty()

if outcomes.empty:
    st.warning("还没有可复盘的推荐记录。先在量化推荐页生成每日推荐。")
    st.stop()

outcomes["strategy_version"] = outcomes["strategy_version"].fillna("历史未标记版本").astype(str)
versions = list(dict.fromkeys(outcomes["strategy_version"].tolist()))
version_col, version_note_col = st.columns([0.32, 0.68])
with version_col:
    selected_version = st.selectbox(
        "策略版本",
        ["全部版本", *versions],
        index=1 if versions else 0,
        help="评分规则发生变化后应分版本查看，避免把不同策略的结果混在一起。",
    )
with version_note_col:
    st.write("策略版本用于保证历史推荐复盘口径可追溯；新生成的推荐会自动记录当前版本。")
if selected_version != "全部版本":
    outcomes = outcomes[outcomes["strategy_version"] == selected_version].copy()

for column in (
    "t1_return",
    "t5_return",
    "t20_return",
    "max_gain_20",
    "max_drawdown_20",
):
    outcomes[column] = pd.to_numeric(outcomes[column], errors="coerce")

metric_row(
    [
        ("推荐样本", str(len(outcomes))),
        ("已有1日结果", str(int(outcomes["t1_return"].notna().sum()))),
        ("已有5日结果", str(int(outcomes["t5_return"].notna().sum()))),
        ("已有20日结果", str(int(outcomes["t20_return"].notna().sum()))),
        ("跟踪期目标一命中", pct_text(outcomes.loc[outcomes["available_days"] > 0, "target1_hit"].mean())),
        ("跟踪期止损触发", pct_text(outcomes.loc[outcomes["available_days"] > 0, "stop_hit"].mean())),
    ]
)

summary = outcome_summary(outcomes)
st.subheader("按推荐周期统计")
summary_display = summary.copy()
percent_columns = [
    "推荐后1日平均收益",
    "推荐后1日上涨率",
    "推荐后5日平均收益",
    "推荐后5日上涨率",
    "推荐后20日平均收益",
    "推荐后20日上涨率",
    "止损触发率",
    "目标一命中率",
    "目标二命中率",
    "平均最大回撤",
    "最差最大回撤",
]
for column in percent_columns:
    summary_display[column] = summary_display[column].map(pct_text)
st.dataframe(summary_display, width="stretch", hide_index=True)

chart_col, hit_col = st.columns(2)
with chart_col:
    st.markdown("#### 各推荐日期后续收益")
    st.plotly_chart(return_chart(outcomes), width="stretch", config=PLOTLY_CONFIG)
with hit_col:
    st.markdown("#### 周期胜率与风控触发")
    st.plotly_chart(hit_chart(summary), width="stretch", config=PLOTLY_CONFIG)

st.subheader("逐条推荐结果")
filter_col, horizon_col, status_col = st.columns([0.5, 0.25, 0.25])
with filter_col:
    keyword = st.text_input("搜索股票", placeholder="代码、名称、行业")
with horizon_col:
    horizon_label = st.selectbox("推荐周期", ["全部", "短期", "中期", "长期"])
with status_col:
    status_label = st.selectbox("复盘状态", ["全部", "待开始", "跟踪中", "已满20日"])

filtered = outcomes.copy()
if keyword:
    text = (
        filtered["symbol"].fillna("").astype(str)
        + " "
        + filtered["name"].fillna("").astype(str)
        + " "
        + filtered["industry"].fillna("").astype(str)
    )
    filtered = filtered[text.str.contains(keyword, case=False, regex=False)]
if horizon_label != "全部":
    horizon = {"短期": "short", "中期": "mid", "长期": "long"}[horizon_label]
    filtered = filtered[filtered["horizon"] == horizon]
if status_label != "全部":
    status = {"待开始": "pending", "跟踪中": "tracking", "已满20日": "complete"}[status_label]
    filtered = filtered[filtered["review_status"] == status]

detail = pd.DataFrame(
    {
        "推荐日期": filtered["trade_date"].map(format_date),
        "周期": filtered["horizon"].map({"short": "短期", "mid": "中期", "long": "长期"}),
        "代码": filtered["symbol"],
        "名称": filtered["name"],
        "评分": filtered["score"],
        "策略版本": filtered["strategy_version"],
        "推荐价": filtered["entry_price"],
        "已跟踪交易日": filtered["available_days"],
        "推荐后1日": filtered["t1_return"].map(pct_text),
        "推荐后5日": filtered["t5_return"].map(pct_text),
        "推荐后20日": filtered["t20_return"].map(pct_text),
        "最大上涨": filtered["max_gain_20"].map(pct_text),
        "收盘最大回撤": filtered["max_drawdown_20"].map(pct_text),
        "止损": filtered.apply(
            lambda row: "待观察" if int(row["available_days"]) == 0 else ("已触发" if int(row["stop_hit"]) else "未触发"),
            axis=1,
        ),
        "目标一": filtered.apply(
            lambda row: "待观察" if int(row["available_days"]) == 0 else ("已命中" if int(row["target1_hit"]) else "未命中"),
            axis=1,
        ),
        "目标二": filtered.apply(
            lambda row: "待观察" if int(row["available_days"]) == 0 else ("已命中" if int(row["target2_hit"]) else "未命中"),
            axis=1,
        ),
        "状态": filtered["review_status"].map(
            {"pending": "待开始", "tracking": "跟踪中", "complete": "已满20日"}
        ),
        "更新至": filtered["evaluated_through"].map(format_date),
    }
)
st.dataframe(detail, width="stretch", hide_index=True)

with st.expander("统计口径与局限"):
    st.markdown(
        """
- 推荐收益以系统保存的推荐价格为基准，不假设一定在买点区间成交。
- 分阶段收益使用推荐之后对应交易日的收盘价；自然日、周末和节假日不计入。
- 最大上涨使用后续20个交易日最高价；最大回撤按推荐价与后续每日收盘价形成的路径计算历史峰值至低点跌幅。
- 同一天同时触及止损和目标时，日K无法判断盘中先后顺序，因此两个事件会分别记录。
- 当前收益未计佣金、印花税和分红；它用于衡量推荐方向和风险，不是账户实际收益。
"""
    )
