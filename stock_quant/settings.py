from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _configured_path(name: str, default: Path) -> Path:
    value = os.getenv(name, "").strip()
    return Path(value).expanduser().resolve() if value else default


DATA_DIR = _configured_path("STOCK_QUANT_DATA_DIR", PROJECT_ROOT / "data")
CACHE_DIR = DATA_DIR / "cache"
REPORTS_DIR = _configured_path("STOCK_QUANT_REPORTS_DIR", PROJECT_ROOT / "reports")

STRATEGY_VERSION = "2026.06.3"
