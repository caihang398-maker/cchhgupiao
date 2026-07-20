from __future__ import annotations

from dataclasses import dataclass

from .broker import OrderDraft, QmtConfig, validate_order_draft


@dataclass(frozen=True)
class TradingRiskPolicy:
    max_order_notional: float = 50_000.0
    max_daily_loss: float = 3_000.0
    max_daily_orders: int = 20
    max_single_position_pct: float = 0.25
    max_quote_age_seconds: int = 30


@dataclass(frozen=True)
class TradingRiskState:
    kill_switch: bool = False
    daily_realized_pnl: float = 0.0
    daily_order_count: int = 0


@dataclass(frozen=True)
class ExecutionContext:
    latest_price: float
    available_cash: float
    total_asset: float
    current_position_market_value: float
    sellable_shares: int
    quote_age_seconds: float = 0.0


def evaluate_execution_risk(
    draft: OrderDraft,
    context: ExecutionContext,
    state: TradingRiskState,
    policy: TradingRiskPolicy,
) -> list[str]:
    """Return every blocking risk reason for an order draft."""
    config = QmtConfig(
        userdata_path="",
        account_id="",
        max_order_notional=policy.max_order_notional,
    )
    errors = validate_order_draft(
        draft,
        latest_price=context.latest_price,
        available_cash=context.available_cash,
        sellable_shares=context.sellable_shares,
        config=config,
    )
    if state.kill_switch:
        errors.append("交易总熔断已开启")
    if state.daily_realized_pnl <= -abs(policy.max_daily_loss):
        errors.append("当日累计亏损已达到风控上限")
    if state.daily_order_count >= policy.max_daily_orders:
        errors.append("当日委托次数已达到风控上限")
    if context.quote_age_seconds > policy.max_quote_age_seconds:
        errors.append("行情价格已过期，请刷新后再核对订单")
    if draft.side.lower() == "buy" and context.total_asset > 0:
        post_value = context.current_position_market_value + draft.notional
        if post_value / context.total_asset > policy.max_single_position_pct:
            errors.append("买入后单只股票仓位将超过集中度上限")
    return list(dict.fromkeys(errors))
