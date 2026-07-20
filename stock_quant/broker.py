from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BrokerError(RuntimeError):
    """Raised when the broker gateway is unavailable or rejects an operation."""


@dataclass(frozen=True)
class QmtConfig:
    userdata_path: str
    account_id: str
    live_enabled: bool = False
    max_order_notional: float = 50_000.0
    max_price_deviation_pct: float = 2.0

    @classmethod
    def from_env(cls) -> "QmtConfig":
        return cls(
            userdata_path=os.getenv("QMT_USERDATA_PATH", "").strip(),
            account_id=os.getenv("QMT_ACCOUNT_ID", "").strip(),
            live_enabled=os.getenv("QMT_LIVE_TRADING", "false").strip().lower()
            in {"1", "true", "yes", "on"},
            max_order_notional=float(os.getenv("QMT_MAX_ORDER_NOTIONAL", "50000")),
            max_price_deviation_pct=float(os.getenv("QMT_MAX_PRICE_DEVIATION_PCT", "2")),
        )

    @property
    def configured(self) -> bool:
        return bool(self.userdata_path and self.account_id)


@dataclass(frozen=True)
class OrderDraft:
    symbol: str
    side: str
    quantity: int
    limit_price: float

    @property
    def notional(self) -> float:
        return self.quantity * self.limit_price


def qmt_symbol(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    digits = "".join(re.findall(r"\d", text))[-6:]
    if len(digits) != 6:
        raise ValueError("请输入有效的6位A股代码")
    suffix = "SH" if digits.startswith(("5", "6", "9")) else "SZ"
    return f"{digits}.{suffix}"


def validate_order_draft(
    draft: OrderDraft,
    latest_price: float,
    available_cash: float,
    sellable_shares: int,
    config: QmtConfig,
) -> list[str]:
    errors: list[str] = []
    side = draft.side.lower()
    if side not in {"buy", "sell"}:
        errors.append("委托方向必须是买入或卖出")
    if draft.quantity <= 0:
        errors.append("委托数量必须大于0")
    if side == "buy" and draft.quantity % 100 != 0:
        errors.append("A股买入数量必须是100股的整数倍")
    if draft.limit_price <= 0:
        errors.append("限价必须大于0")
    if latest_price <= 0:
        errors.append("缺少有效实时价格，禁止提交委托")
    else:
        deviation = abs(draft.limit_price / latest_price - 1) * 100
        if deviation > config.max_price_deviation_pct:
            errors.append(
                f"委托价偏离最新价{deviation:.2f}%，超过{config.max_price_deviation_pct:.2f}%限制"
            )
    if draft.notional > config.max_order_notional:
        errors.append(
            f"单笔金额{draft.notional:,.0f}元，超过{config.max_order_notional:,.0f}元限制"
        )
    if side == "buy" and draft.notional > available_cash:
        errors.append("可用资金不足")
    if side == "sell" and draft.quantity > sellable_shares:
        errors.append("可卖数量不足；当天新买入股票不能当天卖出")
    return errors


class QmtBrokerGateway:
    """Thin, optional adapter over the official XtQuant/MiniQMT interface."""

    def __init__(self, config: QmtConfig | None = None):
        self.config = config or QmtConfig.from_env()
        self._trader: Any = None
        self._account: Any = None

    def readiness(self) -> dict[str, Any]:
        result = {
            "configured": self.config.configured,
            "userdata_exists": False,
            "xtquant_available": False,
            "live_enabled": self.config.live_enabled,
            "message": "",
        }
        if not self.config.configured:
            result["message"] = "尚未配置QMT用户目录和资金账号"
            return result
        result["userdata_exists"] = Path(self.config.userdata_path).exists()
        try:
            import xtquant  # noqa: F401

            result["xtquant_available"] = True
        except ImportError:
            result["message"] = "当前Python环境未找到xtquant，请使用东吴QMT随附组件"
            return result
        if not result["userdata_exists"]:
            result["message"] = "QMT userdata_mini目录不存在"
            return result
        result["message"] = "QMT连接条件已具备"
        return result

    def connect(self) -> None:
        ready = self.readiness()
        if not ready["xtquant_available"] or not ready["userdata_exists"]:
            raise BrokerError(str(ready["message"]))
        from xtquant.xttrader import XtQuantTrader
        from xtquant.xttype import StockAccount

        self._account = StockAccount(self.config.account_id)
        self._trader = XtQuantTrader(self.config.userdata_path, int(time.time() * 1000) % 2_000_000_000)
        self._trader.start()
        connect_result = self._trader.connect()
        if connect_result != 0:
            raise BrokerError(f"连接MiniQMT失败，返回码：{connect_result}")
        subscribe_result = self._trader.subscribe(self._account)
        if subscribe_result != 0:
            raise BrokerError(f"订阅交易账号失败，返回码：{subscribe_result}")

    @property
    def connected(self) -> bool:
        return self._trader is not None and self._account is not None

    def account_snapshot(self) -> dict[str, float]:
        if not self.connected:
            raise BrokerError("请先连接东吴QMT")
        asset = self._trader.query_stock_asset(self._account)
        if asset is None:
            raise BrokerError("QMT未返回资金账户信息")
        return {
            "cash": float(getattr(asset, "cash", 0) or 0),
            "total_asset": float(getattr(asset, "total_asset", 0) or 0),
            "market_value": float(getattr(asset, "market_value", 0) or 0),
            "frozen_cash": float(getattr(asset, "frozen_cash", 0) or 0),
        }

    def positions(self) -> list[dict[str, Any]]:
        if not self.connected:
            raise BrokerError("请先连接东吴QMT")
        rows = self._trader.query_stock_positions(self._account) or []
        return [
            {
                "代码": str(getattr(item, "stock_code", "")),
                "持仓数量": int(getattr(item, "volume", 0) or 0),
                "可卖数量": int(getattr(item, "can_use_volume", 0) or 0),
                "成本价": float(getattr(item, "open_price", 0) or 0),
                "市值": float(getattr(item, "market_value", 0) or 0),
            }
            for item in rows
        ]

    def orders(self, cancelable_only: bool = False) -> list[dict[str, Any]]:
        if not self.connected:
            raise BrokerError("请先连接东吴QMT")
        rows = self._trader.query_stock_orders(self._account, cancelable_only) or []
        return [
            {
                "订单编号": int(getattr(item, "order_id", 0) or 0),
                "代码": str(getattr(item, "stock_code", "")),
                "委托方向": int(getattr(item, "order_type", 0) or 0),
                "委托数量": int(getattr(item, "order_volume", 0) or 0),
                "成交数量": int(getattr(item, "traded_volume", 0) or 0),
                "委托价格": float(getattr(item, "price", 0) or 0),
                "委托状态": int(getattr(item, "order_status", 0) or 0),
                "状态说明": str(getattr(item, "status_msg", "") or ""),
            }
            for item in rows
        ]

    def cancel_order(self, order_id: int, confirmation: str) -> None:
        if not self.config.live_enabled:
            raise BrokerError("实盘开关未开启，不能撤销真实委托")
        if confirmation.strip() != "确认撤单":
            raise BrokerError("请输入“确认撤单”后再执行")
        if not self.connected:
            raise BrokerError("请先连接东吴QMT")
        result = self._trader.cancel_order_stock(self._account, int(order_id))
        if int(result) != 0:
            raise BrokerError(f"QMT撤单请求失败，返回值：{result}")

    def submit_limit_order(
        self,
        draft: OrderDraft,
        latest_price: float,
        available_cash: float,
        sellable_shares: int,
        confirmation: str,
    ) -> int:
        if not self.config.live_enabled:
            raise BrokerError("实盘开关未开启，当前只能生成订单草稿")
        if confirmation.strip() != "确认实盘委托":
            raise BrokerError("请输入“确认实盘委托”后再提交")
        errors = validate_order_draft(
            draft,
            latest_price=latest_price,
            available_cash=available_cash,
            sellable_shares=sellable_shares,
            config=self.config,
        )
        if errors:
            raise BrokerError("；".join(errors))
        if not self.connected:
            raise BrokerError("请先连接东吴QMT")
        from xtquant import xtconstant

        order_type = xtconstant.STOCK_BUY if draft.side.lower() == "buy" else xtconstant.STOCK_SELL
        order_id = self._trader.order_stock(
            self._account,
            qmt_symbol(draft.symbol),
            order_type,
            int(draft.quantity),
            xtconstant.FIX_PRICE,
            float(draft.limit_price),
            "stock_quant_personal",
            "个人决策台人工确认委托",
        )
        if int(order_id) <= 0:
            raise BrokerError(f"QMT委托失败，返回值：{order_id}")
        return int(order_id)
