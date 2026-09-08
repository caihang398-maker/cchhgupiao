from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_quant.auth import auth_enabled
from stock_quant.health import (
    assess_recommendation_freshness,
    assess_runtime_security,
    mysql_schema_findings,
)
from stock_quant.settings import CACHE_DIR, DATA_DIR, REPORTS_DIR
from stock_quant.storage import DB_PATH, db_connection, latest_run, mark_stale_running_runs
from stock_quant.payments import (
    PaymentConfigurationError,
    load_creem_config,
    payment_enabled,
)


REQUIRED_MODULES = (
    "akshare",
    "streamlit",
    "plotly",
    "pandas",
    "numpy",
    "requests",
)

OPTIONAL_PERSONAL_MODULES = ("xtquant",)

SERVER_MODULES = ("pymysql", "bcrypt")
REQUIRED_TABLES = {
    "scan_runs",
    "recommendations",
    "stock_analysis",
    "market_snapshots",
    "capital_hotspots",
    "sentiment_snapshots",
    "limit_up_ladder",
    "recommendation_outcomes",
    "watchlist_positions",
    "position_plan_snapshots",
    "alert_rules",
    "alert_events",
    "paper_accounts",
    "paper_trades",
    "daily_review_reports",
    "broker_order_ledger",
    "trading_risk_state",
    "trading_runtime_events",
}


def check_writeable(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / ".write_test"
    marker.write_text("ok", encoding="ascii")
    marker.unlink()


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    runtime_safety = assess_runtime_security(auth_is_enabled=auth_enabled())
    failures.extend(runtime_safety.failures)
    warnings.extend(runtime_safety.warnings)
    payment_on = payment_enabled()
    if payment_on and not auth_enabled():
        failures.append("启用在线订阅时必须同时设置 AUTH_ENABLED=true。")
    if payment_on:
        try:
            payment_config = load_creem_config(
                require_api_key=True,
                require_webhook_secret=True,
            )
            if payment_config.environment == "test":
                warnings.append("Creem 当前为测试模式，不会产生真实扣款。")
        except PaymentConfigurationError as exc:
            failures.append(f"Creem支付配置无效：{exc}")

    for module_name in REQUIRED_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            failures.append(f"依赖不可用：{module_name}（{exc}）")

    if auth_enabled():
        for module_name in SERVER_MODULES:
            try:
                importlib.import_module(module_name)
            except Exception as exc:
                failures.append(f"会员服务依赖不可用：{module_name}（{exc}）")
        for variable in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"):
            if not os.getenv(variable):
                failures.append(f"启用会员登录后必须设置环境变量：{variable}")
        if not any(
            failure.startswith("会员服务依赖不可用")
            or failure.startswith("启用会员登录后必须设置环境变量")
            for failure in failures
        ):
            connection = None
            try:
                import pymysql

                connection = pymysql.connect(
                    host=os.environ["DB_HOST"],
                    port=int(os.environ["DB_PORT"]),
                    user=os.environ["DB_USER"],
                    password=os.environ["DB_PASSWORD"],
                    database=os.environ["DB_NAME"],
                    charset="utf8mb4",
                    connect_timeout=10,
                    read_timeout=10,
                    write_timeout=10,
                )
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        select table_name, table_type
                        from information_schema.tables
                        where table_schema = %s
                        """,
                        (os.environ["DB_NAME"],),
                    )
                    objects = cursor.fetchall()
                    table_names = {
                        row[0]
                        for row in objects
                        if str(row[1]).upper() == "BASE TABLE"
                    }
                    view_names = {
                        row[0]
                        for row in objects
                        if str(row[1]).upper() == "VIEW"
                    }
                    cursor.execute(
                        """
                        select table_name, column_name
                        from information_schema.columns
                        where table_schema = %s
                        """,
                        (os.environ["DB_NAME"],),
                    )
                    columns_by_table: dict[str, set[str]] = {}
                    for table_name, column_name in cursor.fetchall():
                        columns_by_table.setdefault(str(table_name), set()).add(str(column_name))
                    failures.extend(
                        mysql_schema_findings(
                            table_names,
                            view_names,
                            columns_by_table,
                            include_payment=payment_on,
                        )
                    )
                    if not failures:
                        cursor.execute("select 1 from v_user_account_status limit 1")
            except Exception as exc:
                error_code = getattr(exc, "args", [None])[0]
                if error_code == 1045:
                    failures.append("MySQL会员数据库登录失败（错误码1045），请检查应用账号和密码。")
                elif error_code == 1049:
                    failures.append("MySQL会员数据库不存在（错误码1049），请检查 DB_NAME。")
                elif error_code in {2002, 2003}:
                    failures.append("无法连接MySQL会员数据库，请检查服务状态、地址和端口。")
                else:
                    failures.append(
                        "MySQL会员数据库检查失败"
                        + (f"（错误码{error_code}）" if error_code else "。")
                    )
            finally:
                if connection is not None:
                    connection.close()

    if os.getenv("ENABLE_PERSONAL_TRADING", "false").strip().lower() in {"1", "true", "yes", "on"}:
        for module_name in OPTIONAL_PERSONAL_MODULES:
            try:
                importlib.import_module(module_name)
            except Exception:
                warnings.append(
                    "个人实盘决策台已启用，但未找到xtquant；实时分析可用，东吴QMT账户与委托功能不可用。"
                )

    for directory in (DATA_DIR, CACHE_DIR, REPORTS_DIR):
        try:
            check_writeable(directory)
        except Exception as exc:
            failures.append(f"目录不可写：{directory}（{exc}）")

    try:
        with db_connection(DB_PATH) as connection:
            integrity = connection.execute("pragma integrity_check").fetchone()[0]
            if integrity != "ok":
                failures.append(f"SQLite完整性检查失败：{integrity}")
            tables = {
                row[0]
                for row in connection.execute(
                    "select name from sqlite_master where type = 'table'"
                ).fetchall()
            }
            missing = REQUIRED_TABLES - tables
            if missing:
                failures.append("SQLite缺少数据表：" + "、".join(sorted(missing)))
            if connection.execute("pragma foreign_keys").fetchone()[0] != 1:
                failures.append("SQLite外键校验未启用")
            if connection.execute("pragma journal_mode").fetchone()[0].lower() != "wal":
                failures.append("SQLite未启用WAL并发模式")
        cleaned_runs = mark_stale_running_runs(max_age_minutes=120, db_path=DB_PATH)
        if cleaned_runs:
            warnings.append(f"已自动关闭 {cleaned_runs} 条超时运行中的扫描记录。")
        recent_run = latest_run(DB_PATH)
        if not recent_run:
            warnings.append("尚无推荐扫描记录；首次使用后请生成推荐并确认结果。")
        elif str(recent_run.get("status", "")) == "done":
            freshness = assess_recommendation_freshness(recent_run.get("trade_date"))
            if freshness.level in {"aging", "stale", "missing", "invalid"}:
                warnings.append(freshness.message)
        else:
            warnings.append(
                f"最近一次扫描状态为 {recent_run.get('status') or '未知'}；"
                "请确认上一轮成功推荐仍可读取。"
            )
    except Exception as exc:
        failures.append(f"SQLite检查失败：{exc}")

    if not failures:
        try:
            from streamlit.testing.v1 import AppTest

            # A new installation may need to initialize full-market snapshots before the
            # first render. Slower Windows servers can take more than two minutes.
            app_test = AppTest.from_file(str(ROOT / "dashboard.py"), default_timeout=240)
            app_test.run()
            if app_test.exception:
                failures.extend(
                    f"量化推荐页面初始化失败：{item.message}"
                    for item in app_test.exception
                )
        except Exception as exc:
            failures.append(f"量化推荐页面自检失败：{exc}")

    print(f"项目目录：{ROOT}")
    print(f"数据文件：{DB_PATH}")
    for warning in warnings:
        print(f"[提醒] {warning}")
    for failure in failures:
        print(f"[失败] {failure}", file=sys.stderr)
    if failures:
        print(f"发布前自检未通过，共 {len(failures)} 项问题。", file=sys.stderr)
        return 1
    print("发布前自检通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
