from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_quant.review import refresh_recommendation_outcomes


def main() -> int:
    parser = argparse.ArgumentParser(description="更新已保存推荐的后续收益与风控命中结果。")
    parser.add_argument("--ignore-proxy", action="store_true", help="忽略系统代理")
    args = parser.parse_args()

    def progress(done: int, total: int, symbol: str) -> None:
        print(f"[{done}/{total}] {symbol}")

    frame, errors = refresh_recommendation_outcomes(
        ignore_proxy=args.ignore_proxy,
        progress_callback=progress,
    )
    print(f"已更新 {len(frame)} 条推荐复盘结果。")
    for error in errors:
        print(error, file=sys.stderr)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
