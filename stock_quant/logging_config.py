from __future__ import annotations

import logging
import os
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

    file_handler_exists = False
    for handler in root_logger.handlers:
        existing = getattr(handler, "baseFilename", None)
        if existing and Path(existing).resolve() == target:
            file_handler_exists = True
            break

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if not file_handler_exists:
        handler = RotatingFileHandler(
            target,
            maxBytes=5 * 1024 * 1024,
            backupCount=7,
            encoding="utf-8",
        )
        handler.setFormatter(formatter)
        handler.setLevel(level)
        root_logger.addHandler(handler)

    log_to_stdout = os.getenv("STOCK_QUANT_LOG_STDOUT", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    console_handler_exists = any(
        getattr(handler, "_stock_quant_console", False) for handler in root_logger.handlers
    )
    if log_to_stdout and not console_handler_exists:
        console_handler = logging.StreamHandler()
        console_handler._stock_quant_console = True
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        root_logger.addHandler(console_handler)

    if root_logger.level == logging.NOTSET or root_logger.level > level:
        root_logger.setLevel(level)
    return target
