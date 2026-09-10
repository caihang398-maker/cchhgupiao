from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from .settings import DATA_DIR, PROJECT_ROOT
from .storage import DB_PATH, connect


LOGGER = logging.getLogger(__name__)
MAX_FEED_BYTES = 30 * 1024 * 1024
FEED_METADATA_TABLE = "miniapp_feed_metadata"
PUBLIC_FEED_TABLES = (
    "scan_runs",
    "recommendations",
    "market_snapshots",
    "capital_hotspots",
    "sentiment_snapshots",
    "limit_up_ladder",
    "recommendation_outcomes",
    FEED_METADATA_TABLE,
)
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

_FEED_LOCK = threading.Lock()
_LAST_CHECK_MONOTONIC = 0.0
_LAST_ETAG = ""
_STATUS: dict[str, Any] = {
    "enabled": False,
    "state": "disabled",
    "message": "未配置云端数据源",
}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _validate_sqlite(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        result = connection.execute("pragma integrity_check").fetchone()
        required = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
    if not result or result[0] != "ok":
        raise RuntimeError("小程序数据快照完整性校验失败")
    if not {"scan_runs", "recommendations"}.issubset(required):
        raise RuntimeError("小程序数据快照缺少推荐表")


def _latest_trade_date(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "select max(trade_date) from recommendations"
    ).fetchone()
    return str(row[0] or "") if row else ""


def _create_feed_metadata(connection: sqlite3.Connection) -> str:
    published_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    connection.execute(
        f"""
        create table if not exists {FEED_METADATA_TABLE} (
            id integer primary key check (id = 1),
            published_at text not null,
            latest_trade_date text,
            source_run_time text
        )
        """
    )
    run = connection.execute(
        """
        select run_time
        from scan_runs
        where status = 'done'
        order by trade_date desc, run_time desc, id desc
        limit 1
        """
    ).fetchone()
    connection.execute(
        f"""
        insert into {FEED_METADATA_TABLE} (
            id, published_at, latest_trade_date, source_run_time
        ) values (1, ?, ?, ?)
        on conflict(id) do update set
            published_at = excluded.published_at,
            latest_trade_date = excluded.latest_trade_date,
            source_run_time = excluded.source_run_time
        """,
        (published_at, _latest_trade_date(connection), str(run[0] or "") if run else ""),
    )
    return published_at


def build_sanitized_feed(source: Path, output: Path) -> dict[str, str]:
    if not source.exists():
        raise FileNotFoundError(f"数据库不存在：{source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        sanitized = Path(directory) / "stock_recommendations.sqlite3"
        # SQLite may keep the newest committed rows in its WAL file. The backup
        # API produces one consistent snapshot without dropping those rows.
        _backup_database(source, sanitized)
        with closing(sqlite3.connect(sanitized)) as connection:
            for table in PRIVATE_TABLES:
                exists = connection.execute(
                    "select 1 from sqlite_master where type = 'table' and name = ?",
                    (table,),
                ).fetchone()
                if exists:
                    connection.execute(f'delete from "{table}"')
            published_at = _create_feed_metadata(connection)
            latest_trade_date = _latest_trade_date(connection)
            connection.commit()
            connection.execute("vacuum")
        _validate_sqlite(sanitized)
        with sanitized.open("rb") as source_file, output.open("wb") as raw_output:
            with gzip.GzipFile(fileobj=raw_output, mode="wb", compresslevel=9, mtime=0) as target:
                shutil.copyfileobj(source_file, target)
    return {
        "published_at": published_at,
        "latest_trade_date": latest_trade_date,
    }


def _table_exists(connection: sqlite3.Connection, schema: str, table: str) -> bool:
    row = connection.execute(
        f"select 1 from {schema}.sqlite_master where type = 'table' and name = ?",
        (table,),
    ).fetchone()
    return bool(row)


def _table_columns(connection: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [
        str(row[1])
        for row in connection.execute(f'pragma {schema}.table_info("{table}")').fetchall()
    ]


def _published_at(connection: sqlite3.Connection, schema: str = "main") -> str:
    if not _table_exists(connection, schema, FEED_METADATA_TABLE):
        return ""
    row = connection.execute(
        f"select published_at from {schema}.{FEED_METADATA_TABLE} where id = 1"
    ).fetchone()
    return str(row[0] or "") if row else ""


def _backup_database(source_path: Path, target_path: Path) -> None:
    if not source_path.exists():
        connection = connect(source_path)
        connection.close()
    with closing(sqlite3.connect(source_path, timeout=10)) as source, closing(
        sqlite3.connect(target_path)
    ) as target:
        source.execute("pragma busy_timeout = 10000")
        source.backup(target)


def merge_market_feed(incoming_path: Path, target_path: Path = DB_PATH) -> dict[str, Any]:
    _validate_sqlite(incoming_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target_path.parent) as directory:
        merged_path = Path(directory) / "merged.sqlite3"
        _backup_database(target_path, merged_path)
        with closing(sqlite3.connect(merged_path, timeout=15)) as connection:
            connection.execute("pragma busy_timeout = 15000")
            connection.execute("pragma foreign_keys = off")
            connection.execute("attach database ? as incoming", (str(incoming_path),))
            try:
                incoming_version = _published_at(connection, "incoming")
                current_version = _published_at(connection, "main")
                if incoming_version and current_version and incoming_version <= current_version:
                    return {
                        "updated": False,
                        "published_at": current_version,
                        "data_date": _latest_trade_date(connection),
                    }

                available = [
                    table
                    for table in PUBLIC_FEED_TABLES
                    if _table_exists(connection, "incoming", table)
                    and _table_exists(connection, "main", table)
                ]
                if FEED_METADATA_TABLE not in available:
                    connection.execute(
                        f"""
                        create table if not exists {FEED_METADATA_TABLE} (
                            id integer primary key check (id = 1),
                            published_at text not null,
                            latest_trade_date text,
                            source_run_time text
                        )
                        """
                    )
                    if _table_exists(connection, "incoming", FEED_METADATA_TABLE):
                        available.append(FEED_METADATA_TABLE)

                connection.execute("begin immediate")
                for table in reversed(available):
                    connection.execute(f'delete from "{table}"')
                for table in available:
                    target_columns = _table_columns(connection, "main", table)
                    incoming_columns = set(_table_columns(connection, "incoming", table))
                    columns = [column for column in target_columns if column in incoming_columns]
                    if not columns:
                        continue
                    quoted = ", ".join(f'"{column}"' for column in columns)
                    connection.execute(
                        f'insert into "{table}" ({quoted}) select {quoted} from incoming."{table}"'
                    )
                connection.commit()
                published_at = _published_at(connection)
                data_date = _latest_trade_date(connection)
            finally:
                connection.execute("detach database incoming")
        _validate_sqlite(merged_path)
        merged_path.replace(target_path)
    return {
        "updated": True,
        "published_at": published_at,
        "data_date": data_date,
    }


def _cache_busted_url(url: str, bucket: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["feed_check"] = str(bucket)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def cloud_feed_status() -> dict[str, Any]:
    return dict(_STATUS)


def refresh_market_feed(force: bool = False) -> dict[str, Any]:
    global _LAST_CHECK_MONOTONIC, _LAST_ETAG, _STATUS

    url = os.getenv("MINIAPP_CLOUD_FEED_URL", "").strip()
    if not url:
        _STATUS = {
            "enabled": False,
            "state": "disabled",
            "message": "未配置云端数据源",
        }
        return cloud_feed_status()

    interval = max(10, int(os.getenv("MINIAPP_CLOUD_FEED_REFRESH_SECONDS", "60")))
    now = time.monotonic()
    if not force and now - _LAST_CHECK_MONOTONIC < interval:
        return cloud_feed_status()

    with _FEED_LOCK:
        now = time.monotonic()
        if not force and now - _LAST_CHECK_MONOTONIC < interval:
            return cloud_feed_status()
        _LAST_CHECK_MONOTONIC = now
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            headers = {"Cache-Control": "no-cache", "Pragma": "no-cache"}
            if _LAST_ETAG:
                headers["If-None-Match"] = _LAST_ETAG
            session = requests.Session()
            session.trust_env = False
            response = session.get(
                _cache_busted_url(url, int(time.time()) // interval),
                headers=headers,
                timeout=(6, 25),
            )
            if response.status_code == 304:
                _STATUS = {
                    **_STATUS,
                    "enabled": True,
                    "state": "current",
                    "checked_at": checked_at,
                    "message": "云端数据已是最新",
                }
                return cloud_feed_status()
            response.raise_for_status()
            payload = response.content
            if not payload or len(payload) > MAX_FEED_BYTES:
                raise RuntimeError("云端数据快照大小异常")
            if not payload.startswith(b"\x1f\x8b"):
                raise RuntimeError("云端数据快照格式无效")

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=DATA_DIR) as directory:
                incoming_path = Path(directory) / "incoming.sqlite3"
                incoming_path.write_bytes(gzip.decompress(payload))
                result = merge_market_feed(incoming_path, DB_PATH)
            _LAST_ETAG = str(response.headers.get("ETag") or "")
            _STATUS = {
                "enabled": True,
                "state": "updated" if result["updated"] else "current",
                "updated": bool(result["updated"]),
                "checked_at": checked_at,
                "published_at": result.get("published_at", ""),
                "data_date": result.get("data_date", ""),
                "message": "已载入 PC 端最新数据" if result["updated"] else "云端数据已是最新",
            }
        except Exception as exc:
            LOGGER.exception("刷新小程序云端数据源失败")
            _STATUS = {
                **_STATUS,
                "enabled": True,
                "state": "error",
                "checked_at": checked_at,
                "message": f"云端数据同步失败：{exc}",
            }
        return cloud_feed_status()


def _publish_config() -> dict[str, Any] | None:
    path = Path(
        os.getenv("MINIAPP_FEED_CONFIG_PATH", str(DATA_DIR / "miniapp_sync.json"))
    ).expanduser()
    if not path.exists():
        return None
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"小程序同步配置无效：{exc}") from exc
    return config if isinstance(config, dict) else None


def publish_market_feed_if_configured() -> tuple[bool, str] | None:
    config = _publish_config()
    if not config or not _truthy(config.get("enabled")):
        return None
    env_id = str(config.get("env_id") or "").strip()
    public_base_url = str(config.get("public_base_url") or "").strip()
    cloud_path = str(
        config.get("cloud_path") or "miniapp-feed/stock_recommendations.seed.gz"
    ).strip()
    if not env_id or not public_base_url:
        return False, "同步配置缺少云环境编号或静态站点地址"
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    script = PROJECT_ROOT / "scripts" / "publish_miniapp_feed.ps1"
    if not powershell or not script.exists():
        return False, "本机缺少 PowerShell 或小程序同步脚本"
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-EnvId",
        env_id,
        "-SourceDb",
        str(DB_PATH),
        "-CloudPath",
        cloud_path,
        "-PublicBaseUrl",
        public_base_url,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=240,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"同步命令执行失败：{exc}"
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    if completed.returncode != 0:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        detail = next(
            (line for line in lines if " : " in line and ".ps1" in line),
            lines[-1] if lines else f"退出码 {completed.returncode}",
        )
        return False, f"同步到小程序失败：{detail}"
    return True, "最新行情与推荐已同步到小程序云端"
