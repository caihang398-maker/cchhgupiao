from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pandas as pd

from stock_quant.auth import _decode_json_object, add_months, create_manual_order
from stock_quant.backtest import run_backtest
from stock_quant.broker import OrderDraft, QmtConfig, qmt_symbol, validate_order_draft
from stock_quant.trading import (
    ExecutionContext,
    TradingRiskPolicy,
    TradingRiskState,
    evaluate_execution_risk,
)
from stock_quant.commercial import (
    commercial_audit,
    commercial_plan_frame,
    credibility_summary,
    data_source_health_frame,
    order_summary,
    p0_readiness_frame,
    user_behavior_frames,
    user_behavior_summary,
)
from stock_quant.data import (
    DataSourceError,
    _normalize_spot_volume_lots,
    fetch_daily_history,
    fetch_spot,
    prepare_spot_universe,
)
from stock_quant.fundamentals import _refresh_cached_market_fields
from stock_quant.health import (
    MYSQL_REQUIRED_COLUMNS,
    MYSQL_REQUIRED_VIEWS,
    assess_recommendation_freshness,
    assess_runtime_security,
    mysql_schema_findings,
)
from stock_quant.leaders import (
    fallback_leaders_from_recommendations,
    fallback_leaders_from_local_snapshot,
    leader_switches,
    normalize_constituents,
    normalized_curve,
    rank_leaders,
)
from stock_quant.notify import mask_webhook_url, validate_webhook_url
from stock_quant.position import analyze_position
from stock_quant.presentation import format_date
from stock_quant.recommender import _filter_fundamental_candidates
from stock_quant.product import (
    build_ai_research_table,
    build_daily_review_report,
    build_paper_trade_candidates,
    build_credibility_metrics,
    build_market_map,
    credibility_explanation,
    enrich_recommendations_for_product,
    filter_by_conditions,
    membership_value_metrics,
    p1_paid_value_frame,
    paper_trade_summary,
    strategy_backtest_matrix,
)
from stock_quant.review import evaluate_recommendation
from stock_quant.rotation import (
    board_flow_history,
    build_mainline_snapshot,
    build_rotation_heatmap,
)
from stock_quant.sentiment import (
    build_sentiment_snapshot,
    classify_emotion_stage,
    normalize_limit_up_pool,
)
from stock_quant.settings import STRATEGY_VERSION
from stock_quant.storage import (
    create_run,
    db_connection,
    get_or_create_paper_account,
    load_daily_review_reports,
    load_limit_up_ladder,
    load_paper_trades,
    load_runs,
    load_sentiment_history,
    load_watchlist_positions,
    get_trading_risk_state,
    load_broker_orders,
    save_daily_review_report,
    save_paper_trades,
    save_sentiment_snapshot,
    save_watchlist_position,
    save_broker_order,
    update_trading_risk_state,
)
from stock_quant.strategy import analyze_stock


def sample_history(rows: int = 260) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=rows)
    trend = np.linspace(10, 18, rows)
    wave = np.sin(np.arange(rows) / 7) * 0.25
    close = trend + wave
    return pd.DataFrame(
        {
            "date": dates,
            "open": close * 0.998,
            "close": close,
            "high": close * 1.015,
            "low": close * 0.985,
            "volume": np.where(np.arange(rows) % 13 == 0, 180_000, 120_000),
            "turnover": close * 120_000 * 100,
        }
    )


class QuantCoreTests(unittest.TestCase):
    def test_production_runtime_requires_member_login(self) -> None:
        report = assess_runtime_security("production", auth_is_enabled=False)
        self.assertTrue(report.failures)
        self.assertIn("AUTH_ENABLED=true", report.failures[0])

    def test_recommendation_freshness_counts_weekdays(self) -> None:
        friday = date(2026, 7, 17)
        sunday = date(2026, 7, 19)
        monday = date(2026, 7, 20)
        sunday_report = assess_recommendation_freshness(friday, today=sunday)
        monday_report = assess_recommendation_freshness(friday, today=monday)
        self.assertEqual(sunday_report.level, "fresh")
        self.assertEqual(monday_report.level, "aging")
        self.assertEqual(monday_report.weekdays_elapsed, 1)

    def test_mysql_schema_check_finds_missing_column_and_view(self) -> None:
        tables = set(MYSQL_REQUIRED_COLUMNS)
        columns = {table: set(items) for table, items in MYSQL_REQUIRED_COLUMNS.items()}
        columns["sys_users"].remove("password_hash")
        findings = mysql_schema_findings(tables, set(), columns)
        self.assertTrue(any("password_hash" in item for item in findings))
        self.assertTrue(any(next(iter(MYSQL_REQUIRED_VIEWS)) in item for item in findings))

    def test_manual_order_rejects_invalid_amount_before_database_access(self) -> None:
        with patch("stock_quant.auth._connect") as mocked_connect:
            with self.assertRaisesRegex(ValueError, "实收金额"):
                create_manual_order(1, 1, float("nan"), "微信", 1)
            with self.assertRaisesRegex(ValueError, "实收金额"):
                create_manual_order(1, 1, 10, "微信", 1, mark_paid=False)
        mocked_connect.assert_not_called()

    def test_cached_fundamentals_refresh_current_market_fields(self) -> None:
        cached = pd.DataFrame(
            [
                {
                    "code": "600001",
                    "name": "旧名称",
                    "price": 8.0,
                    "change_pct": np.nan,
                    "turnover": 1.0,
                    "net_inflow_3d": 150_000_000,
                    "financial_industry": "电力",
                }
            ]
        )
        spot = pd.DataFrame(
            [
                {
                    "code": "600001",
                    "symbol": "sh600001",
                    "name": "当前名称",
                    "price": 10.2,
                    "prev_close": 10.0,
                    "change_pct": 2.0,
                    "turnover": 250_000_000,
                },
                {
                    "code": "600002",
                    "symbol": "sh600002",
                    "name": "新增股票",
                    "price": 6.0,
                    "prev_close": 6.0,
                    "change_pct": 0.0,
                    "turnover": 100_000_000,
                },
            ]
        )

        refreshed = _refresh_cached_market_fields(cached, spot)

        self.assertEqual(refreshed["code"].tolist(), ["600001", "600002"])
        first = refreshed.iloc[0]
        self.assertEqual(first["name"], "当前名称")
        self.assertEqual(float(first["price"]), 10.2)
        self.assertEqual(float(first["change_pct"]), 2.0)
        self.assertEqual(float(first["turnover"]), 250_000_000)
        self.assertEqual(float(first["net_inflow_3d"]), 150_000_000)

    def test_spot_universe_derives_missing_change_percentage(self) -> None:
        spot = pd.DataFrame(
            [
                {
                    "code": "600001",
                    "name": "测试股份",
                    "price": 10.5,
                    "prev_close": 10.0,
                    "open": np.nan,
                    "turnover": 100_000_000,
                    "change_pct": np.nan,
                }
            ]
        )

        prepared = prepare_spot_universe(spot)

        self.assertEqual(len(prepared), 1)
        self.assertAlmostEqual(float(prepared.iloc[0]["change_pct"]), 5.0)
        self.assertTrue(np.isfinite(float(prepared.iloc[0]["pre_score"])))

    def test_fundamental_filter_prefers_strict_matches(self) -> None:
        universe = pd.DataFrame(
            [
                {
                    "code": "600001",
                    "fund_flow_pass": True,
                    "valuation_normal": True,
                    "cashflow_good": True,
                    "dividend_stable": True,
                },
                {
                    "code": "600002",
                    "fund_flow_pass": True,
                    "valuation_normal": True,
                    "cashflow_good": True,
                    "dividend_stable": False,
                },
            ]
        )
        filtered, note = _filter_fundamental_candidates(universe)
        self.assertEqual(filtered["code"].tolist(), ["600001"])
        self.assertEqual(filtered.iloc[0]["filter_mode"], "严格匹配")
        self.assertEqual(note, "")

    def test_fundamental_filter_uses_labeled_near_match_only_when_strict_is_empty(self) -> None:
        universe = pd.DataFrame(
            [
                {
                    "code": "600002",
                    "fund_flow_pass": True,
                    "valuation_normal": True,
                    "cashflow_good": True,
                    "dividend_stable": False,
                },
                {
                    "code": "600003",
                    "fund_flow_pass": True,
                    "valuation_normal": False,
                    "cashflow_good": False,
                    "dividend_stable": False,
                },
            ]
        )
        filtered, note = _filter_fundamental_candidates(universe)
        self.assertEqual(filtered["code"].tolist(), ["600002"])
        self.assertEqual(int(filtered.iloc[0]["filter_match_count"]), 3)
        self.assertIn("历史分红", filtered.iloc[0]["filter_missing_labels"])
        self.assertIn("至少满足3/4项", note)

        strict_only, _ = _filter_fundamental_candidates(universe, allow_near_match=False)
        self.assertTrue(strict_only.empty)

    def test_spot_automatically_switches_proxy_mode(self) -> None:
        expected = pd.DataFrame({"code": ["600000"]})
        with patch(
            "stock_quant.data._fetch_spot_once",
            side_effect=[DataSourceError("代理失败"), expected],
        ) as mocked:
            actual = fetch_spot(ignore_proxy=False)
        self.assertEqual(mocked.call_args_list[0].kwargs["ignore_proxy"], False)
        self.assertEqual(mocked.call_args_list[1].kwargs["ignore_proxy"], True)
        self.assertEqual(actual.iloc[0]["code"], "600000")
        self.assertIn("自动切换", actual.attrs.get("source_warning", ""))

    def test_daily_history_uses_last_good_exact_request_cache(self) -> None:
        expected = sample_history(30)
        with TemporaryDirectory() as directory:
            with patch("stock_quant.data.DAILY_CACHE_DIR", Path(directory)):
                with patch("stock_quant.data._fetch_daily_history_once", return_value=expected):
                    online = fetch_daily_history(
                        "SH600000",
                        start_date="2026-01-01",
                        end_date="2026-02-28",
                    )
                with patch(
                    "stock_quant.data._fetch_daily_history_once",
                    side_effect=DataSourceError("网络不可用"),
                ) as mocked:
                    cached = fetch_daily_history(
                        "SH600000",
                        start_date="2026-01-01",
                        end_date="2026-02-28",
                    )
        self.assertEqual(len(online), 30)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(len(cached), 30)
        self.assertIn("最近一次成功缓存", cached.attrs.get("source_warning", ""))

    def test_local_spot_snapshot_can_supply_securities_leaders(self) -> None:
        with TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            pd.DataFrame(
                [
                    {"symbol": "sh600999", "name": "招商证券", "price": 18, "change_pct": 3, "turnover": 8e8},
                    {"symbol": "sh601688", "name": "华泰证券", "price": 16, "change_pct": 2, "turnover": 7e8},
                    {"symbol": "sz000776", "name": "广发证券", "price": 15, "change_pct": 1, "turnover": 6e8},
                    {"symbol": "sh600000", "name": "浦发银行", "price": 10, "change_pct": 5, "turnover": 9e8},
                ]
            ).to_pickle(cache_dir / "spot_latest.pkl")
            with patch("stock_quant.leaders.CACHE_DIR", cache_dir):
                leaders = fallback_leaders_from_local_snapshot("证券")
        self.assertEqual(len(leaders), 3)
        self.assertTrue(leaders["name"].str.contains("证券", regex=False).all())

    def test_qmt_symbol_and_live_order_risk_limits(self) -> None:
        self.assertEqual(qmt_symbol("SH600000"), "600000.SH")
        self.assertEqual(qmt_symbol("000001"), "000001.SZ")
        config = QmtConfig(
            userdata_path="C:/qmt/userdata_mini",
            account_id="test",
            max_order_notional=20_000,
            max_price_deviation_pct=2,
        )
        safe = OrderDraft(symbol="600000", side="buy", quantity=100, limit_price=10)
        self.assertEqual(
            validate_order_draft(safe, latest_price=10, available_cash=20_000, sellable_shares=0, config=config),
            [],
        )
        unsafe = OrderDraft(symbol="600000", side="buy", quantity=150, limit_price=12)
        errors = validate_order_draft(
            unsafe,
            latest_price=10,
            available_cash=1_000,
            sellable_shares=0,
            config=config,
        )
        self.assertTrue(any("100股" in item for item in errors))
        self.assertTrue(any("偏离" in item for item in errors))
        self.assertTrue(any("资金不足" in item for item in errors))

    def test_execution_risk_blocks_kill_switch_stale_quote_and_concentration(self) -> None:
        draft = OrderDraft(symbol="600000", side="buy", quantity=1000, limit_price=10)
        errors = evaluate_execution_risk(
            draft,
            ExecutionContext(
                latest_price=10,
                available_cash=100_000,
                total_asset=100_000,
                current_position_market_value=20_000,
                sellable_shares=0,
                quote_age_seconds=60,
            ),
            TradingRiskState(kill_switch=True, daily_realized_pnl=-4_000, daily_order_count=20),
            TradingRiskPolicy(),
        )
        self.assertTrue(any("熔断" in item for item in errors))
        self.assertTrue(any("行情价格" in item for item in errors))
        self.assertTrue(any("集中度" in item for item in errors))
        self.assertTrue(any("委托次数" in item for item in errors))

    def test_trading_risk_state_and_order_ledger_storage(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "trading.sqlite3"
            initial = get_trading_risk_state("admin", db_path=db_path)
            self.assertEqual(initial["daily_order_count"], 0)
            updated = update_trading_risk_state(
                "admin", kill_switch=True, increment_orders=1, db_path=db_path
            )
            self.assertEqual(updated["kill_switch"], 1)
            self.assertEqual(updated["daily_order_count"], 1)
            save_broker_order(
                {
                    "owner_key": "admin",
                    "client_order_id": "draft-1",
                    "symbol": "600000.SH",
                    "side": "买入",
                    "quantity": 100,
                    "limit_price": 10,
                    "notional": 1000,
                    "status": "草稿",
                },
                db_path=db_path,
            )
            ledger = load_broker_orders("admin", db_path=db_path)
            self.assertEqual(len(ledger), 1)
            self.assertEqual(ledger.iloc[0]["symbol"], "600000.SH")

    def test_commercial_audit_scores_product_readiness(self) -> None:
        recommendations = pd.DataFrame(
            [
                {
                    "symbol": "SH600000",
                    "name": "测试银行",
                    "credibility_score": 82,
                    "credibility_sample_count": 4,
                    "avg_t1_return": 0.01,
                    "avg_t3_return": 0.02,
                    "avg_t5_return": 0.03,
                    "avg_t20_return": 0.08,
                    "high_win_repeat": 1,
                }
            ]
        )
        outcomes = pd.DataFrame(
            [
                {
                    "symbol": "SH600000",
                    "available_days": 20,
                    "t1_return": 0.02,
                    "t3_return": 0.03,
                    "t5_return": 0.04,
                    "t20_return": 0.09,
                    "max_drawdown_20": -0.05,
                    "stop_hit": 0,
                    "target1_hit": 1,
                }
            ]
        )
        positions = pd.DataFrame([{"symbol": "SH600000", "name": "测试银行"}])
        alert_rules = pd.DataFrame([{"symbol": "SH600000", "alert_type": "到达买点", "enabled": 1}])
        alert_events = pd.DataFrame([{"symbol": "SH600000", "severity": "机会"}])
        audit = commercial_audit(
            recommendations,
            outcomes,
            positions,
            alert_rules,
            alert_events,
            breadth={"total": 5527},
        )
        self.assertGreaterEqual(audit["score"], 80)
        self.assertEqual(audit["high_trust_count"], 1)
        self.assertTrue(audit["gaps"])
        credibility = credibility_summary(recommendations, outcomes)
        self.assertEqual(credibility["high_trust_count"], 1)
        self.assertAlmostEqual(credibility["avg_t5"], 0.04)
        health = data_source_health_frame(
            {"id": 1, "run_time": "2026-06-22 10:00:00", "status": "done"},
            {"total": 5527},
            errors=[],
            capital_hotspots=pd.DataFrame([{"name": "银行"}]),
            recommendations=recommendations,
        )
        readiness = p0_readiness_frame(
            recommendations,
            outcomes,
            positions,
            pd.DataFrame([{"trade_date": "2026-06-22", "symbol": "SH600000"}]),
            alert_rules,
            alert_events,
            health,
        )
        self.assertIn("P0模块", readiness.columns)
        self.assertIn("数据源稳定性", set(readiness["P0模块"]))
        plan = commercial_plan_frame()
        self.assertIn("商业价值", plan.columns)
        self.assertIn("P0", set(plan["优先级"]))

    def test_p1_paid_value_tables(self) -> None:
        recommendations = pd.DataFrame(
            [
                {
                    "symbol": "SH600000",
                    "name": "测试银行",
                    "horizon": "mid",
                    "industry": "银行",
                    "topic_text": "中特估",
                    "strategy_type": "主线趋势股",
                    "score": 86,
                    "credibility_label": "高可信",
                    "buy_zone_low": 9.8,
                    "stop_loss": 9.2,
                },
                {
                    "symbol": "SH600001",
                    "name": "替代银行",
                    "horizon": "mid",
                    "industry": "银行",
                    "topic_text": "中特估",
                    "strategy_type": "主线趋势股",
                    "score": 80,
                    "credibility_label": "可跟踪",
                    "buy_zone_low": 8.8,
                    "stop_loss": 8.2,
                },
            ]
        )
        outcomes = pd.DataFrame(
            [
                {
                    "symbol": "SH600000",
                    "available_days": 20,
                    "t5_return": 0.03,
                    "t20_return": 0.08,
                    "max_drawdown_20": -0.04,
                }
            ]
        )
        hotspots = pd.DataFrame(
            [
                {
                    "period": "daily",
                    "category": "行业",
                    "name": "银行",
                    "trade_date": "2026-06-22",
                    "net_inflow_yi": 12.5,
                    "leader": "测试银行",
                }
            ]
        )
        history = pd.DataFrame(
            [
                {"period": "daily", "category": "行业", "name": "银行", "trade_date": "2026-06-21", "net_inflow_yi": 3.0},
                {"period": "daily", "category": "行业", "name": "银行", "trade_date": "2026-06-20", "net_inflow_yi": 2.0},
            ]
        )
        market_map = build_market_map(recommendations, hotspots, pd.DataFrame(), history=history)
        self.assertIn("主线持续", market_map.columns)
        self.assertGreaterEqual(int(market_map.iloc[0]["主线持续"]), 1)

        ai_table = build_ai_research_table(recommendations, {"label": "中性"})
        self.assertIn("同板块替代股", ai_table.columns)
        self.assertIn("替代银行", ai_table.iloc[0]["同板块替代股"])

        p1 = p1_paid_value_frame(
            recommendations,
            hotspots,
            market_map,
            outcomes,
            pd.DataFrame([{"symbol": "SH600000"}]),
            pd.DataFrame([{"alert_type": "到达买点"}]),
        )
        self.assertIn("AI投研解释", set(p1["P1模块"]))

        matrix = strategy_backtest_matrix(
            recommendations,
            outcomes,
            pd.DataFrame([{"symbol": "SH600000"}]),
            pd.DataFrame([{"emotion_stage": "升温"}]),
        )
        self.assertEqual(len(matrix), 5)
        self.assertIn("推荐池历史回测", set(matrix["回测类型"]))

        metrics = membership_value_metrics(
            pd.DataFrame([{"remaining_days": 3}, {"remaining_days": 30}]),
            pd.DataFrame([{"id": 1}]),
            pd.DataFrame([{"id": 1}, {"id": 2}]),
            pd.DataFrame(
                [
                    {"user_id": 1, "usage_date": str(date.today())},
                    {"user_id": 2, "usage_date": str(date.today())},
                ]
            ),
        )
        self.assertEqual(metrics["expiring_7d"], 1)
        self.assertEqual(metrics["active_users_7d"], 2)

    def test_sentiment_ladder_score_and_stage(self) -> None:
        raw = pd.DataFrame(
            {
                "代码": ["600001", "600002", "600003", "600004", "600005"],
                "名称": ["首板甲", "二板甲", "三板甲", "高标甲", "高标乙"],
                "涨跌幅": [10.0] * 5,
                "最新价": [10, 12, 15, 18, 20],
                "成交额": [1_000_000_000] * 5,
                "换手率": [5, 8, 10, 12, 15],
                "封板资金": [100_000_000, 90_000_000, 80_000_000, 70_000_000, 60_000_000],
                "首次封板时间": ["093000"] * 5,
                "最后封板时间": ["100000"] * 5,
                "炸板次数": [0, 0, 1, 0, 1],
                "涨停统计": ["1/1"] * 5,
                "连板数": [1, 2, 3, 4, 5],
                "所属行业": ["机器人"] * 5,
            }
        )
        limit_up = normalize_limit_up_pool(raw)
        previous = pd.DataFrame(
            {
                "code": ["600002", "600003", "600004", "600005"],
                "change_pct": [5.0, 6.0, 8.0, 10.0],
            }
        )
        snapshot = build_sentiment_snapshot(
            "2026-06-12",
            limit_up,
            pd.DataFrame({"code": ["600010"]}),
            pd.DataFrame(),
            previous,
            breadth={"up_ratio": 0.7},
        )
        self.assertEqual(snapshot["first_board_count"], 1)
        self.assertEqual(snapshot["second_board_count"], 1)
        self.assertEqual(snapshot["third_board_count"], 1)
        self.assertEqual(snapshot["high_board_count"], 2)
        self.assertEqual(snapshot["max_streak"], 5)
        self.assertEqual(snapshot["promotion_rate"], 1.0)
        self.assertGreater(snapshot["emotion_score"], 50)

    def test_sentiment_stage_detects_retreat(self) -> None:
        self.assertEqual(
            classify_emotion_stage(
                score=32,
                broken_rate=0.5,
                previous_premium=-3,
                max_streak=2,
                limit_down_count=28,
                limit_up_count=20,
                previous_score=58,
            ),
            "退潮",
        )

    def test_sentiment_snapshot_is_upserted_with_ladder(self) -> None:
        with TemporaryDirectory() as temp:
            db_path = Path(temp) / "sentiment.sqlite3"
            snapshot = {
                "trade_date": "2026-06-12",
                "limit_up_count": 2,
                "broken_count": 1,
                "limit_down_count": 0,
                "first_board_count": 1,
                "second_board_count": 1,
                "third_board_count": 0,
                "high_board_count": 0,
                "max_streak": 2,
                "seal_rate": 0.6667,
                "broken_rate": 0.3333,
                "promotion_rate": 0.5,
                "previous_premium": 2.0,
                "previous_red_rate": 0.7,
                "emotion_score": 62.0,
                "emotion_stage": "升温",
                "components": {"封板质量": 66.67},
            }
            ladder = pd.DataFrame(
                {
                    "symbol": ["SH600001", "SH600002"],
                    "code": ["600001", "600002"],
                    "name": ["测试甲", "测试乙"],
                    "streak": [1, 2],
                    "change_pct": [10.0, 10.0],
                    "price": [10, 20],
                    "turnover": [1e8, 2e8],
                    "turnover_rate": [5, 8],
                    "seal_amount": [5e7, 8e7],
                    "first_seal_time": ["093000", "100000"],
                    "last_seal_time": ["093000", "103000"],
                    "break_count": [0, 1],
                    "industry": ["机器人", "电力"],
                }
            )
            save_sentiment_snapshot(snapshot, ladder, db_path)
            snapshot["emotion_score"] = 65.0
            save_sentiment_snapshot(snapshot, ladder.head(1), db_path)
            history = load_sentiment_history(db_path=db_path)
            saved_ladder = load_limit_up_ladder(db_path=db_path)
            self.assertEqual(len(history), 1)
            self.assertEqual(float(history.iloc[0]["emotion_score"]), 65.0)
            self.assertEqual(len(saved_ladder), 1)

    def test_mainline_lifecycle_and_rotation_heatmap(self) -> None:
        rows = []
        for day, daily_flow, rank in [
            ("2026-06-10", 1.0, 8),
            ("2026-06-11", 2.0, 5),
            ("2026-06-12", 3.0, 2),
        ]:
            rows.append(
                {
                    "id": len(rows) + 1,
                    "trade_date": day,
                    "category": "行业",
                    "period": "daily",
                    "name": "机器人",
                    "net_inflow_yi": daily_flow,
                    "rank": rank,
                    "change_pct": 3.0,
                    "leader": "龙头甲",
                    "leader_change_pct": 8.0,
                }
            )
        rows.extend(
            [
                {
                    "id": 10,
                    "trade_date": "2026-06-12",
                    "category": "行业",
                    "period": "weekly",
                    "name": "机器人",
                    "net_inflow_yi": 8.0,
                    "rank": 2,
                    "change_pct": 5.0,
                },
                {
                    "id": 11,
                    "trade_date": "2026-06-12",
                    "category": "行业",
                    "period": "monthly",
                    "name": "机器人",
                    "net_inflow_yi": 20.0,
                    "rank": 3,
                    "change_pct": 10.0,
                },
            ]
        )
        history = pd.DataFrame(rows)
        snapshot = build_mainline_snapshot(history, "行业")
        self.assertEqual(snapshot.iloc[0]["name"], "机器人")
        self.assertEqual(snapshot.iloc[0]["stage"], "加速")
        self.assertGreater(snapshot.iloc[0]["mainline_score"], 0)
        heatmap = build_rotation_heatmap(history, "行业", ["机器人"])
        self.assertEqual(heatmap.shape, (1, 3))
        trend = board_flow_history(history, "行业", "机器人")
        self.assertEqual(set(trend["period"]), {"daily", "weekly", "monthly"})

    def test_mainline_snapshot_accepts_missing_previous_rank(self) -> None:
        history = pd.DataFrame(
            [
                {
                    "id": 1,
                    "trade_date": "2026-06-15",
                    "category": "industry",
                    "name": "power",
                    "period": "daily",
                    "net_inflow_yi": 3.2,
                    "rank": 2,
                    "change_pct": 1.5,
                    "leader": "sample",
                    "leader_change_pct": 3.0,
                }
            ]
        )

        snapshot = build_mainline_snapshot(history, "industry")

        self.assertEqual(len(snapshot), 1)
        self.assertTrue(pd.isna(snapshot.iloc[0]["rank_change"]))
        self.assertIsInstance(snapshot.iloc[0]["stage"], str)

    def test_leader_ranking_curve_and_switch_detection(self) -> None:
        raw = pd.DataFrame(
            {
                "代码": ["600001", "000002", "300003", "600004"],
                "名称": ["龙头甲", "龙头乙", "龙头丙", "普通股"],
                "最新价": [12, 18, 25, 8],
                "涨跌幅": [9.8, 6.2, 4.1, 1.0],
                "成交额": [2_000_000_000, 1_800_000_000, 900_000_000, 200_000_000],
                "换手率": [12, 9, 7, 2],
                "量比": [2.2, 1.8, 1.5, 0.8],
            }
        )
        leaders = rank_leaders(normalize_constituents(raw))
        self.assertEqual(leaders["name"].tolist(), ["龙头甲", "龙头乙", "龙头丙"])
        self.assertEqual(leaders["leader_label"].tolist(), ["龙一", "龙二", "龙三"])

        dates = pd.bdate_range("2026-05-01", periods=10)
        curve = normalized_curve(
            {
                "龙一 龙头甲": pd.DataFrame({"date": dates, "close": np.linspace(10, 12, 10)}),
                "龙二 龙头乙": pd.DataFrame({"date": dates, "close": np.linspace(20, 21, 10)}),
            },
            benchmark=pd.DataFrame({"date": dates, "close": np.linspace(4000, 4040, 10)}),
        )
        self.assertAlmostEqual(float(curve["龙一 龙头甲"].iloc[0]), 100.0)
        self.assertIn("龙头等权指数", curve.columns)
        self.assertIn("沪深300", curve.columns)

        switches = leader_switches(
            pd.DataFrame(
                {
                    "id": [1, 2, 3],
                    "trade_date": ["2026-06-01", "2026-06-02", "2026-06-03"],
                    "leader": ["甲", "甲", "乙"],
                    "leader_change_pct": [5.0, 6.0, 8.0],
                }
            )
        )
        self.assertEqual(len(switches), 1)
        self.assertEqual(switches.iloc[0]["原龙头"], "甲")
        self.assertEqual(switches.iloc[0]["新龙头"], "乙")

    def test_leader_fallback_uses_latest_recommendation_pool(self) -> None:
        recommendations = pd.DataFrame(
            [
                {
                    "symbol": "SH600919",
                    "name": "江苏银行",
                    "industry": "银行Ⅱ",
                    "topics": '["互联金融"]',
                    "score": 88.7,
                    "close": 11.8,
                },
                {
                    "symbol": "SH601838",
                    "name": "成都银行",
                    "industry": "银行Ⅱ",
                    "topics": '["互联金融"]',
                    "score": 91.5,
                    "close": 19.6,
                },
                {
                    "symbol": "SZ300623",
                    "name": "捷捷微电",
                    "industry": "半导体",
                    "topics": '["半导体概念"]',
                    "score": 79.1,
                    "close": 34.2,
                },
            ]
        )

        leaders = fallback_leaders_from_recommendations(
            recommendations,
            "行业",
            "银行",
        )

        self.assertEqual(leaders["name"].tolist(), ["成都银行", "江苏银行"])
        self.assertEqual(leaders["leader_label"].tolist(), ["龙一", "龙二"])

    def test_decode_saved_position_params(self) -> None:
        self.assertEqual(
            _decode_json_object('{"query":"北方导航","holding_cost":13.87}'),
            {"query": "北方导航", "holding_cost": 13.87},
        )
        self.assertEqual(_decode_json_object("[]"), {})

    def test_add_months_clamps_end_of_month(self) -> None:
        from datetime import datetime

        self.assertEqual(
            add_months(datetime(2026, 1, 31, 8, 30), 1),
            datetime(2026, 2, 28, 8, 30),
        )

    def test_spot_volume_is_normalized_from_shares_to_lots(self) -> None:
        spot = pd.DataFrame(
            {
                "price": [10.0, 20.0],
                "volume": [10_000_000.0, 5_000_000.0],
                "turnover": [100_000_000.0, 100_000_000.0],
            }
        )
        normalized = _normalize_spot_volume_lots(spot)
        self.assertEqual(normalized["volume"].tolist(), [100_000.0, 50_000.0])
        self.assertEqual(normalized.attrs["volume_unit"], "lot")

    def test_buy_zone_keeps_distance_above_stop(self) -> None:
        report = analyze_stock(sample_history(), "sh600000", "测试股票")
        self.assertGreater(report.buy_zone_low, report.stop_loss)
        self.assertGreaterEqual(report.buy_zone_high, report.buy_zone_low)

    def test_position_plan_uses_board_lots_and_reduces_cost(self) -> None:
        history = sample_history()
        report = analyze_stock(history, "sh600000", "测试股票")
        plan = analyze_position(
            history,
            report,
            cost_price=20.0,
            shares=1200,
            available_cash=20_000,
            t_ratio=0.2,
            breadth={"temperature": 55},
            global_summary={"score": 50},
        )
        self.assertEqual(plan.t_shares % 100, 0)
        self.assertGreater(plan.t_sell_low, plan.t_buy_high)
        self.assertLessEqual(plan.cost_after_one_round, plan.cost_price)
        self.assertLessEqual(plan.cost_after_three_rounds, plan.cost_after_one_round)
        self.assertGreaterEqual(plan.opportunity_days_20d, 0)
        self.assertLessEqual(plan.opportunity_days_20d, 20)
        self.assertGreater(plan.average_turnover_20d, 0)
        self.assertEqual(sum(item["shares"] for item in plan.buy_ladder), plan.t_shares)

    def test_position_plan_rejects_non_board_lot(self) -> None:
        history = sample_history()
        report = analyze_stock(history, "sh600000", "测试股票")
        with self.assertRaises(ValueError):
            analyze_position(history, report, cost_price=12.0, shares=150)

    def test_position_plan_respects_sellable_inventory_and_cash(self) -> None:
        history = sample_history()
        report = analyze_stock(history, "sh600000", "测试股票")
        sell_first = analyze_position(
            history,
            report,
            cost_price=20.0,
            shares=2000,
            sellable_shares=300,
            t_ratio=0.5,
            t_preference="先卖后买",
        )
        self.assertLessEqual(sell_first.t_shares, 300)
        buy_first = analyze_position(
            history,
            report,
            cost_price=20.0,
            shares=2000,
            sellable_shares=2000,
            available_cash=0,
            t_ratio=0.5,
            t_preference="先买后卖",
        )
        self.assertEqual(buy_first.t_shares, 0)

    def test_position_plan_marks_policy_event_risk(self) -> None:
        history = sample_history()
        report = analyze_stock(history, "sh600000", "测试股票")
        news = pd.DataFrame(
            {
                "title": ["海外关税与出口限制升级，相关行业面临风险"],
                "published_at": [pd.Timestamp("2026-06-11 09:30")],
                "source": ["测试资讯"],
            }
        )
        plan = analyze_position(
            history,
            report,
            cost_price=20.0,
            shares=1000,
            news=news,
        )
        self.assertIn("风险", plan.event_label)
        self.assertEqual(len(plan.event_headlines), 1)

    def test_backtest_uses_board_lots_and_keeps_cash_non_negative(self) -> None:
        curve, trades, metrics = run_backtest(sample_history(), initial_cash=100_000)
        self.assertFalse(curve.empty)
        self.assertTrue((curve["cash"] >= 0).all())
        if not trades.empty:
            self.assertTrue((trades["股数"] % 100 == 0).all())
        self.assertIn("max_drawdown", metrics)
        self.assertIn("sharpe", metrics)

    def test_recommendation_review_uses_future_trading_days(self) -> None:
        history = pd.DataFrame(
            {
                "date": pd.bdate_range("2026-06-01", periods=25),
                "open": [10.0] * 25,
                "close": np.linspace(10.0, 12.4, 25),
                "high": np.linspace(10.1, 12.6, 25),
                "low": np.linspace(9.9, 11.9, 25),
                "volume": [100_000] * 25,
                "turnover": [100_000_000] * 25,
            }
        )
        recommendation = {
            "id": 1,
            "run_id": 1,
            "trade_date": "2026-06-01",
            "horizon": "short",
            "symbol": "SH600000",
            "name": "测试股票",
            "close": 10.0,
            "stop_loss": 9.5,
            "take_profit_1": 11.0,
            "take_profit_2": 12.0,
        }
        outcome = evaluate_recommendation(recommendation, history)
        self.assertEqual(outcome["available_days"], 20)
        self.assertAlmostEqual(outcome["t1_return"], 0.01)
        self.assertAlmostEqual(outcome["t3_return"], 0.03)
        self.assertAlmostEqual(outcome["t5_return"], 0.05)
        self.assertAlmostEqual(outcome["t20_return"], 0.20)
        self.assertEqual(outcome["target1_hit"], 1)
        self.assertEqual(outcome["target2_hit"], 1)
        self.assertEqual(outcome["review_status"], "complete")

    def test_recommendation_review_uses_peak_to_trough_drawdown(self) -> None:
        history = pd.DataFrame(
            {
                "date": pd.bdate_range("2026-06-01", periods=6),
                "open": [10, 10, 12, 11, 10, 10],
                "close": [10, 12, 11, 9, 10, 10],
                "high": [10.1, 12.2, 11.2, 9.2, 10.2, 10.2],
                "low": [9.9, 11.8, 10.8, 8.8, 9.8, 9.8],
                "volume": [100_000] * 6,
                "turnover": [100_000_000] * 6,
            }
        )
        outcome = evaluate_recommendation(
            {
                "id": 2,
                "run_id": 1,
                "trade_date": "2026-06-01",
                "horizon": "short",
                "symbol": "SH600000",
                "name": "测试股票",
                "close": 10.0,
                "stop_loss": 8.0,
                "take_profit_1": 13.0,
                "take_profit_2": 14.0,
            },
            history,
        )
        self.assertAlmostEqual(outcome["max_drawdown_20"], -0.25)

    def test_sqlite_uses_wal_foreign_keys_and_saves_strategy_version(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            run_id = create_run(
                "2026-06-08",
                80,
                10,
                50_000_000,
                parameters={"行业筛选": "全部"},
                db_path=db_path,
            )
            with db_connection(db_path) as connection:
                self.assertEqual(connection.execute("pragma foreign_keys").fetchone()[0], 1)
                self.assertEqual(connection.execute("pragma journal_mode").fetchone()[0], "wal")
            runs = load_runs(db_path=db_path)
            self.assertEqual(int(runs.iloc[0]["id"]), run_id)
            self.assertEqual(runs.iloc[0]["strategy_version"], STRATEGY_VERSION)
            self.assertIn("行业筛选", runs.iloc[0]["parameters_json"])

    def test_product_credibility_strategy_and_market_map(self) -> None:
        outcomes = pd.DataFrame(
            [
                {
                    "symbol": "SH600000",
                    "horizon": "short",
                    "available_days": 20,
                    "t1_return": 0.01,
                    "t3_return": 0.03,
                    "t5_return": 0.05,
                    "t20_return": 0.08,
                    "max_drawdown_20": -0.04,
                    "stop_hit": 0,
                    "target1_hit": 1,
                }
                for _ in range(3)
            ]
        )
        metrics = build_credibility_metrics(outcomes)
        self.assertTrue(bool(metrics.iloc[0]["high_win_repeat"]))
        recommendations = pd.DataFrame(
            [
                {
                    "trade_date": "2026-06-08",
                    "symbol": "SH600000",
                    "name": "浦发银行",
                    "horizon": "short",
                    "score": 92,
                    "base_score": 85,
                    "priority": "重点",
                    "industry": "银行",
                    "net_inflow_3d": 120_000_000,
                    "net_inflow_5d": 150_000_000,
                    "net_inflow_10d": 180_000_000,
                    "position_pct": 0.2,
                }
            ]
        )
        enriched = enrich_recommendations_for_product(recommendations, outcomes)
        self.assertIn(enriched.iloc[0]["strategy_type"], {"超短情绪龙头", "资金异动股"})
        explanation = credibility_explanation(enriched.iloc[0])
        self.assertIn(explanation["action"], {"重点跟踪", "轻仓验证", "谨慎观察"})
        self.assertTrue(explanation["reasons"])
        market_map = build_market_map(
            enriched,
            pd.DataFrame(
                [
                    {
                        "period": "daily",
                        "category": "行业",
                        "name": "银行",
                        "net_inflow_yi": 12.0,
                        "leader": "浦发银行",
                    }
                ]
            ),
            pd.DataFrame([{"industry": "银行", "symbol": "SH600000"}]),
        )
        self.assertFalse(market_map.empty)
        self.assertIn("银行", set(market_map["方向"]))

    def test_condition_selector_paper_trade_and_daily_review(self) -> None:
        recommendations = pd.DataFrame(
            [
                {
                    "id": 101,
                    "trade_date": "2026-06-16",
                    "symbol": "SH600000",
                    "name": "测试银行",
                    "horizon": "short",
                    "priority": "重点",
                    "strategy_type": "资金异动股",
                    "industry": "银行",
                    "topic_text": "金融科技",
                    "score": 90,
                    "base_score": 82,
                    "credibility_score": 76,
                    "high_win_repeat": 1,
                    "close": 10.05,
                    "buy_zone_low": 9.9,
                    "buy_zone_high": 10.1,
                    "stop_loss": 9.3,
                    "take_profit_1": 11.0,
                    "net_inflow_3d": 150_000_000,
                    "net_inflow_5d": 180_000_000,
                    "net_inflow_10d": 220_000_000,
                    "position_pct": 0.2,
                    "valuation_normal": 1,
                    "cashflow_good": 1,
                    "dividend_stable": 1,
                },
                {
                    "id": 102,
                    "trade_date": "2026-06-16",
                    "symbol": "SZ000001",
                    "name": "弱势样本",
                    "horizon": "short",
                    "priority": "观察",
                    "strategy_type": "高风险博弈股",
                    "industry": "测试",
                    "score": 55,
                    "base_score": 50,
                    "credibility_score": 40,
                    "close": 20,
                    "buy_zone_low": 18,
                    "buy_zone_high": 19,
                    "position_pct": 0.6,
                    "net_inflow_3d": -1,
                    "net_inflow_5d": -1,
                    "net_inflow_10d": -1,
                },
            ]
        )
        outcomes = pd.DataFrame(
            [
                {
                    "recommendation_id": 101,
                    "symbol": "SH600000",
                    "t1_return": 0.02,
                    "t5_return": 0.05,
                }
            ]
        )

        filtered = filter_by_conditions(
            recommendations,
            ["主力资金连续流入", "技术评分较高", "处于买点附近", "市场主线方向", "高可信度推荐"],
            min_score=70,
            max_risk_position=0.35,
        )
        self.assertEqual(filtered["symbol"].tolist(), ["SH600000"])

        trades = build_paper_trade_candidates(filtered, outcomes, account_id=1, owner_key="local", cash_per_trade=10_000)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["shares"] % 100, 0)
        curve, summary = paper_trade_summary(pd.DataFrame(trades), initial_cash=100_000)
        self.assertFalse(curve.empty)
        self.assertGreater(summary["total_return"], 0)

        report, metrics = build_daily_review_report(
            filtered,
            outcomes,
            pd.DataFrame([{"period": "daily", "rank": 1, "name": "银行", "net_inflow_yi": 12.0}]),
            {"total": 5000, "up": 3000, "down": 1800, "temperature": 66, "temperature_label": "偏强"},
            {"label": "中性"},
            pd.DataFrame([{"emotion_stage": "升温"}]),
        )
        self.assertIn("今日推荐", report)
        self.assertEqual(metrics["recommendation_count"], 1)

    def test_paper_trade_and_daily_review_storage(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "paper.sqlite3"
            account = get_or_create_paper_account("local", initial_cash=100_000, db_path=db_path)
            save_paper_trades(
                [
                    {
                        "account_id": account["id"],
                        "owner_key": "local",
                        "trade_date": "2026-06-16",
                        "symbol": "SH600000",
                        "name": "测试银行",
                        "horizon": "short",
                        "side": "买入",
                        "price": 10.0,
                        "shares": 1000,
                        "amount": 10_000,
                        "fee": 5,
                        "return_pct": 0.03,
                        "status": "已复盘",
                        "reason": "测试",
                    }
                ],
                db_path=db_path,
            )
            trades = load_paper_trades("local", account_id=int(account["id"]), db_path=db_path)
            self.assertEqual(len(trades), 1)
            self.assertEqual(trades.iloc[0]["symbol"], "SH600000")

            save_daily_review_report(
                "local",
                "2026-06-16",
                "日报",
                "摘要",
                "# 日报内容",
                {"recommendation_count": 1},
                db_path=db_path,
            )
            reports = load_daily_review_reports("local", db_path=db_path)
            self.assertEqual(len(reports), 1)
            self.assertIn("日报内容", reports.iloc[0]["report_text"])

    def test_watchlist_position_storage(self) -> None:
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "watch.sqlite3"
            position_id = save_watchlist_position(
                {
                    "owner_key": "local",
                    "symbol": "SH600000",
                    "name": "浦发银行",
                    "cost_price": 10.2,
                    "shares": 1000,
                    "available_cash": 5000,
                    "sellable_shares": 1000,
                },
                db_path=db_path,
            )
            self.assertGreater(position_id, 0)
            loaded = load_watchlist_positions("local", db_path=db_path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded.iloc[0]["symbol"], "SH600000")

    def test_chinese_date_and_webhook_validation(self) -> None:
        self.assertEqual(format_date("2026-06-08"), "2026年06月08日")
        self.assertEqual(
            validate_webhook_url(
                "企业微信",
                "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
            ),
            (True, "企业微信"),
        )
        valid, message = validate_webhook_url(
            "企业微信",
            "https://127.0.0.1/internal",
        )
        self.assertFalse(valid)
        self.assertIn("域名", message)
        masked = mask_webhook_url(
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=secret-value"
        )
        self.assertNotIn("secret-value", masked)
        self.assertIn("qyapi.weixin.qq.com", masked)

    def test_p2_user_behavior_summary(self) -> None:
        today = pd.Timestamp(date.today())
        accounts = pd.DataFrame(
            [
                {"id": 1, "mobile": "13800000001", "real_name": "张三"},
                {"id": 2, "mobile": "13800000002", "real_name": "李四"},
            ]
        )
        usage = pd.DataFrame(
            [
                {
                    "user_id": 1,
                    "mobile": "13800000001",
                    "real_name": "张三",
                    "usage_date": today,
                    "login_count": 2,
                    "query_count": 5,
                    "stock_analysis_count": 3,
                    "last_activity_at": today,
                }
            ]
        )
        queries = pd.DataFrame(
            [
                {
                    "query_type": "stock_analysis",
                    "stock_symbol": "SH600000",
                    "stock_name": "浦发银行",
                    "created_at": today,
                },
                {
                    "query_type": "stock_analysis",
                    "stock_symbol": "SH600000",
                    "stock_name": "浦发银行",
                    "created_at": today,
                },
            ]
        )
        logs = pd.DataFrame(
            [
                {"success": 1, "created_at": today},
                {"success": 0, "created_at": today},
            ]
        )
        summary = user_behavior_summary(accounts, usage, queries, logs)
        self.assertEqual(summary["active_today"], 1)
        self.assertEqual(summary["silent_7d"], 1)
        self.assertIn("stock_analysis", summary["top_feature"])
        feature_rank, stock_rank, user_rank = user_behavior_frames(usage, queries)
        self.assertEqual(feature_rank.iloc[0]["使用次数"], 2)
        self.assertIn("浦发银行", stock_rank.iloc[0]["股票"])
        self.assertEqual(user_rank.iloc[0]["查询次数"], 5)

    def test_p2_order_summary(self) -> None:
        plans = pd.DataFrame(
            [
                {"id": 1, "plan_name": "7天试用", "price": 0},
                {"id": 2, "plan_name": "月卡", "price": 10},
            ]
        )
        orders = pd.DataFrame(
            [
                {"plan_id": 1, "order_status": "paid", "paid_amount": 0},
                {"plan_id": 2, "order_status": "paid", "paid_amount": 10},
                {"plan_id": 2, "order_status": "pending", "paid_amount": 0},
            ]
        )
        payments = pd.DataFrame([{"amount": 10}])
        accounts = pd.DataFrame(
            [{"service_expires_at": pd.Timestamp.now() + pd.Timedelta(days=3)}]
        )
        summary = order_summary(plans, orders, payments, accounts)
        self.assertEqual(summary["order_count"], 3)
        self.assertEqual(summary["paid_order_count"], 2)
        self.assertEqual(summary["trial_count"], 1)
        self.assertEqual(summary["expiring_7d"], 1)


if __name__ == "__main__":
    unittest.main()
