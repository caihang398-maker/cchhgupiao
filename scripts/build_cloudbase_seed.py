from __future__ import annotations

import argparse
import gzip
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


PRIVATE_TABLES = (
    "alert_events",
    "alert_rules",
    "broker_order_ledger",
    "daily_review_reports",
    "paper_accounts",
    "paper_trades",
    "position_plan_snapshots",
    "stock_analysis",
    "trading_risk_state",
    "trading_runtime_events",
    "watchlist_positions",
)


def build_seed(source: Path, output: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"数据库不存在：{source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        sanitized = Path(directory) / "stock_recommendations.sqlite3"
        shutil.copy2(source, sanitized)
        with closing(sqlite3.connect(sanitized)) as connection:
            for table in PRIVATE_TABLES:
                exists = connection.execute(
                    "select 1 from sqlite_master where type = 'table' and name = ?",
                    (table,),
                ).fetchone()
                if exists:
                    connection.execute(f'delete from "{table}"')
            connection.commit()
            connection.execute("vacuum")
            integrity = connection.execute("pragma integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise RuntimeError("脱敏数据库完整性校验失败")
        with sanitized.open("rb") as source_file, output.open("wb") as raw_output:
            with gzip.GzipFile(fileobj=raw_output, mode="wb", compresslevel=9, mtime=0) as target:
                shutil.copyfileobj(source_file, target)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成不含个人持仓、预警和交易记录的云端行情种子")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/stock_recommendations.sqlite3"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("deploy/cloudbase/stock_recommendations.seed.gz"),
    )
    args = parser.parse_args()
    build_seed(args.source.resolve(), args.output.resolve())
    print(f"CloudBase seed written: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
