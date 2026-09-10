from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_quant.miniapp_feed import build_sanitized_feed


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
    result = build_sanitized_feed(args.source.resolve(), args.output.resolve())
    print(
        f"CloudBase seed written: {args.output.resolve()} "
        f"(data date: {result['latest_trade_date'] or '-'})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
