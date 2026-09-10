from __future__ import annotations

import gzip
import shutil
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from stock_quant.miniapp_feed import build_sanitized_feed, merge_market_feed
from stock_quant.storage import (
    create_run,
    load_watchlist_positions,
    save_recommendations,
    save_watchlist_position,
    update_run,
)


def save_recommendation(db_path: Path, trade_date: str, symbol: str) -> None:
    run_id = create_run(
        trade_date,
        scan_size=80,
        top_n=10,
        min_turnover=0.5,
        db_path=db_path,
    )
    save_recommendations(
        [
            {
                "run_id": run_id,
                "trade_date": trade_date,
                "horizon": "short",
                "symbol": symbol,
                "name": "同步测试",
                "score": 88,
                "base_score": 80,
                "rating": "关注",
                "priority": "重点",
                "action": "等待条件",
                "close": 10,
                "buy_zone_low": 9.8,
                "buy_zone_high": 10.1,
                "stop_loss": 9.3,
                "take_profit_1": 10.8,
                "take_profit_2": 11.5,
                "trailing_stop": 0.06,
                "position_pct": 0.1,
            }
        ],
        db_path=db_path,
    )
    update_run(run_id, "done", "测试完成", db_path=db_path)


def extract_feed(feed_path: Path, database_path: Path) -> None:
    with gzip.open(feed_path, "rb") as source, database_path.open("wb") as target:
        shutil.copyfileobj(source, target)


class MiniappFeedTests(unittest.TestCase):
    def test_sanitized_feed_keeps_market_data_and_removes_private_data(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_db = root / "source.sqlite3"
            feed_path = root / "feed.seed.gz"
            extracted_db = root / "feed.sqlite3"

            save_recommendation(source_db, "2026-09-10", "SH600000")
            save_watchlist_position(
                {
                    "owner_key": "member:test",
                    "symbol": "SZ000001",
                    "name": "私人持仓",
                    "cost_price": 12.3,
                    "shares": 100,
                },
                db_path=source_db,
            )

            result = build_sanitized_feed(source_db, feed_path)
            extract_feed(feed_path, extracted_db)

            with closing(sqlite3.connect(extracted_db)) as connection:
                recommendation = connection.execute(
                    "select trade_date, symbol from recommendations"
                ).fetchone()
                private_count = connection.execute(
                    "select count(*) from watchlist_positions"
                ).fetchone()[0]
                metadata = connection.execute(
                    "select latest_trade_date from miniapp_feed_metadata where id = 1"
                ).fetchone()

            self.assertEqual(result["latest_trade_date"], "2026-09-10")
            self.assertEqual(recommendation, ("2026-09-10", "SH600000"))
            self.assertEqual(private_count, 0)
            self.assertEqual(metadata, ("2026-09-10",))

    def test_market_feed_merge_preserves_existing_private_data(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_db = root / "pc.sqlite3"
            target_db = root / "cloud.sqlite3"
            feed_path = root / "feed.seed.gz"
            incoming_db = root / "incoming.sqlite3"

            save_recommendation(source_db, "2026-09-10", "SH600000")
            save_recommendation(target_db, "2026-09-08", "SZ000001")
            save_watchlist_position(
                {
                    "owner_key": "openid:private-user",
                    "symbol": "SZ002681",
                    "name": "云端私人持仓",
                    "cost_price": 5.5,
                    "shares": 14000,
                },
                db_path=target_db,
            )
            build_sanitized_feed(source_db, feed_path)
            extract_feed(feed_path, incoming_db)

            first = merge_market_feed(incoming_db, target_db)
            second = merge_market_feed(incoming_db, target_db)

            with closing(sqlite3.connect(target_db)) as connection:
                latest = connection.execute(
                    "select max(trade_date), group_concat(symbol) from recommendations"
                ).fetchone()
            positions = load_watchlist_positions(
                "openid:private-user", db_path=target_db
            )

            self.assertTrue(first["updated"])
            self.assertEqual(first["data_date"], "2026-09-10")
            self.assertFalse(second["updated"])
            self.assertEqual(latest, ("2026-09-10", "SH600000"))
            self.assertEqual(len(positions), 1)
            self.assertEqual(positions.iloc[0]["symbol"], "SZ002681")


if __name__ == "__main__":
    unittest.main()
