from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from stock_quant.settings import PROJECT_ROOT


LOG_DIRECTORY = PROJECT_ROOT / "logs"
APPLICATION_LOG = LOG_DIRECTORY / "application.log"


def configure_application_logging(
    log_path: str | Path = APPLICATION_LOG,
    level: int = logging.INFO,
) -> Path:
    target = Path(log_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()

    for handler in root_logger.handlers:
        existing = getattr(handler, "baseFilename", None)
        if existing and Path(existing).resolve() == target:
            return target

    handler = RotatingFileHandler(
        target,
        maxBytes=5 * 1024 * 1024,
        backupCount=7,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler.setLevel(level)
    root_logger.addHandler(handler)
    if root_logger.level == logging.NOTSET or root_logger.level > level:
        root_logger.setLevel(level)
    return target
