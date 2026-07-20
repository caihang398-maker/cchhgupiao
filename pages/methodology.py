from __future__ import annotations

import pandas as pd
import streamlit as st

from stock_quant.glossary import FILTER_DEFINITIONS, SCORING_NOTES, TERM_GROUPS


st.set_page_config(page_title="名词与方法 - A股每日量化推荐", layout="wide")

st.title("名词与方法")
st.caption("解释系统筛选条件、指标口径、评分结构和使用边界。这里描述的是当前代码实际采用的规则。")

st.warning(
    "综合评分是候选排序工具，不是上涨概率，也不能解释为“90分就有90%概率上涨”。"
    "财报、资金流和行情数据均可能延迟或修订。"
)

st.subheader("筛选条件有什么用")
filters = pd.DataFrame(FILTER_DEFINITIONS).rename(
    columns={"name": "筛选条件", "meaning": "作用", "rule": "当前规则", "caution": "使用提醒"}
)
st.dataframe(filters, width="stretch", hide_index=True)

st.subheader("系统如何形成推荐")
for title, description in SCORING_NOTES:
    with st.expander(title):
        st.write(description)

st.subheader("名词查询")
keyword = st.text_input("搜索名词", placeholder="例如：相对强弱指标、主力资金、止损、最大回撤")
matched = 0
for group, terms in TERM_GROUPS.items():
    visible = [
        (term, meaning)
        for term, meaning in terms
        if not keyword.strip() or keyword.strip().lower() in f"{term} {meaning}".lower()
    ]
    if not visible:
        continue
    matched += len(visible)
    st.markdown(f"#### {group}")
    st.dataframe(
        pd.DataFrame(visible, columns=["名词", "解释"]),
        width="stretch",
        hide_index=True,
    )

if matched == 0:
    st.info("没有找到匹配名词，可以尝试更短的关键词。")

st.subheader("回测与实盘的差别")
st.markdown(
    """
- 当前历史回测使用日线收盘后生成信号，并在下一可交易日开盘成交。
- 买入数量按100股整数手计算，计入佣金最低收费、卖出印花税和滑点。
- 回测仍无法完整模拟涨跌停封单、停牌、公告冲击、盘口深度和成交失败。
- 个股回测只验证技术策略，不会还原某一天的热点、资金流和财报快照。
- 复权方式、样本区间和参数改变都可能显著影响结果，应同时观察收益、基准、回撤、夏普和交易次数。
"""
)

st.subheader("数据口径")
st.markdown(
    """
- 行情与财务数据主要通过公开数据聚合接口获取。
- 实时成交量统一换算为“手”，与历史日K成交量保持一致。
- 估算市盈率采用最近可用财报每股收益按报告期年化，不等同于专业终端的严格滚动十二个月值。
- 主力资金是数据供应商的成交分类估算，不代表可以验证的机构真实账户净买入。
- 全球指数只作为低权重风险环境，不直接决定某只A股的买卖信号。
"""
)
