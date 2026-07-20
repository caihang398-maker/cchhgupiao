from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_quant.settings import DATA_DIR
from stock_quant.storage import DB_PATH, db_connection


def remove_expired_backups(directory: Path, retention_days: int) -> int:
    cutoff = datetime.now() - timedelta(days=retention_days)
    removed = 0
    for path in directory.glob("股票数据_*.sqlite3"):
        modified = datetime.fromtimestamp(path.stat().st_mtime)
        if modified < cutoff:
            path.unlink()
            removed += 1
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="在线备份股票系统SQLite数据。")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DATA_DIR / "backups",
        help="备份目录",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=30,
        help="备份保留天数",
    )
    args = parser.parse_args()
    if args.retention_days < 1:
        parser.error("备份保留天数必须大于0")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    destination = args.output_dir / f"股票数据_{datetime.now():%Y%m%d_%H%M%S}.sqlite3"
    with db_connection(DB_PATH) as source:
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
            result = target.execute("pragma integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"备份完整性检查失败：{result}")
        finally:
            target.close()

    removed = remove_expired_backups(args.output_dir, args.retention_days)
    print(f"备份完成：{destination}")
    print(f"已清理过期备份：{removed}个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
