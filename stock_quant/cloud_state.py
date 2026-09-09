from __future__ import annotations

import gzip
import hashlib
import logging
import os
import sqlite3
import tempfile
import threading
from contextlib import closing
from pathlib import Path

from .settings import DATA_DIR, PROJECT_ROOT
from .storage import DB_PATH, connect


LOGGER = logging.getLogger(__name__)
STATE_KEY = "miniapp-primary"
_SYNC_LOCK = threading.Lock()


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def cloud_sqlite_sync_enabled() -> bool:
    return _truthy("MINIAPP_CLOUD_SQLITE_SYNC", "false")


def _database_url() -> str:
    return os.getenv("MINIAPP_CLOUD_DATABASE_URL", "").strip()


def _seed_path() -> Path:
    configured = os.getenv("MINIAPP_CLOUD_SEED_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return PROJECT_ROOT / "deploy" / "cloudbase" / "stock_recommendations.seed.gz"


def _connect_postgres():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("云端数据库同步依赖 psycopg 未安装") from exc
    url = _database_url()
    if not url:
        raise RuntimeError("尚未配置 MINIAPP_CLOUD_DATABASE_URL")
    return psycopg.connect(url, connect_timeout=8)


def _ensure_cloud_table(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            create table if not exists stock_quant_miniapp_state (
                state_key text primary key,
                payload bytea not null,
                sha256 char(64) not null,
                updated_at timestamptz not null default now()
            )
            """
        )


def _validate_sqlite(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        result = connection.execute("pragma integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise RuntimeError("云端 SQLite 快照完整性校验失败")


def _atomic_write(payload: bytes) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = DB_PATH.with_suffix(".restore.tmp")
    temp_path.write_bytes(payload)
    try:
        _validate_sqlite(temp_path)
        temp_path.replace(DB_PATH)
    finally:
        temp_path.unlink(missing_ok=True)


def _install_seed() -> None:
    seed_path = _seed_path()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if seed_path.exists():
        with gzip.open(seed_path, "rb") as source:
            _atomic_write(source.read())
        LOGGER.info("已载入脱敏行情种子：%s", seed_path)
        return
    connection = connect(DB_PATH)
    connection.close()
    LOGGER.warning("未找到云端行情种子，已创建空数据库：%s", seed_path)


def prepare_cloud_state() -> None:
    """Restore durable state before the CloudBase API starts."""

    if not cloud_sqlite_sync_enabled():
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    url = _database_url()
    if url:
        try:
            with _connect_postgres() as connection:
                _ensure_cloud_table(connection)
                with connection.cursor() as cursor:
                    cursor.execute(
                        "select payload, sha256 from stock_quant_miniapp_state where state_key = %s",
                        (STATE_KEY,),
                    )
                    row = cursor.fetchone()
            if row:
                payload = bytes(row[0])
                digest = hashlib.sha256(payload).hexdigest()
                if digest != str(row[1]):
                    raise RuntimeError("云端 SQLite 快照哈希校验失败")
                _atomic_write(payload)
                LOGGER.info("已从 CloudBase PostgreSQL 恢复小程序数据")
                return
        except Exception:
            LOGGER.exception("读取 CloudBase PostgreSQL 快照失败，将使用行情种子启动")
    _install_seed()
    if url:
        try:
            sync_cloud_state()
        except Exception:
            LOGGER.exception("初始化 CloudBase PostgreSQL 快照失败，服务将从行情种子继续启动")


def _snapshot_bytes() -> bytes:
    if not DB_PATH.exists():
        connection = connect(DB_PATH)
        connection.close()
    with tempfile.TemporaryDirectory() as directory:
        snapshot_path = Path(directory) / "snapshot.sqlite3"
        with closing(sqlite3.connect(DB_PATH, timeout=10)) as source, closing(
            sqlite3.connect(snapshot_path)
        ) as target:
            source.execute("pragma busy_timeout = 10000")
            source.backup(target)
        _validate_sqlite(snapshot_path)
        return snapshot_path.read_bytes()


def sync_cloud_state() -> None:
    if not cloud_sqlite_sync_enabled() or not _database_url():
        return
    with _SYNC_LOCK:
        payload = _snapshot_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        with _connect_postgres() as connection:
            _ensure_cloud_table(connection)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    insert into stock_quant_miniapp_state (state_key, payload, sha256, updated_at)
                    values (%s, %s, %s, now())
                    on conflict (state_key) do update set
                        payload = excluded.payload,
                        sha256 = excluded.sha256,
                        updated_at = excluded.updated_at
                    """,
                    (STATE_KEY, payload, digest),
                )
        LOGGER.info("小程序数据已同步到 CloudBase PostgreSQL")
