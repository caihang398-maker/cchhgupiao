from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
REPORTS_DIR = PROJECT_ROOT / "reports"

STRATEGY_VERSION = "2026.06.3"
