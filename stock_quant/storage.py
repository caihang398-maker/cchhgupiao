from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .settings import DATA_DIR, STRATEGY_VERSION


DB_PATH = DATA_DIR / "stock_recommendations.sqlite3"
SQLITE_JOURNAL_MODES = {"delete", "truncate", "persist", "memory", "wal", "off"}


def connect(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    conn.execute("pragma busy_timeout = 10000")
    journal_mode = os.getenv("STOCK_QUANT_SQLITE_JOURNAL_MODE", "wal").strip().lower()
    if journal_mode not in SQLITE_JOURNAL_MODES:
        journal_mode = "wal"
    conn.execute(f"pragma journal_mode = {journal_mode}")
    conn.execute("pragma synchronous = normal")
    init_db(conn)
    return conn


@contextmanager
def db_connection(db_path: str | Path = DB_PATH):
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table if not exists scan_runs (
            id integer primary key autoincrement,
            trade_date text not null,
            run_time text not null,
            scan_size integer not null,
            top_n integer not null,
            min_turnover real not null,
            strategy_version text,
            parameters_json text,
            status text not null,
            note text
        );

        create table if not exists recommendations (
            id integer primary key autoincrement,
            run_id integer not null,
            trade_date text not null,
            horizon text not null,
            symbol text not null,
            name text not null,
            score real not null,
            base_score real not null,
            rating text not null,
            priority text not null,
            action text not null,
            close real not null,
            buy_zone_low real not null,
            buy_zone_high real not null,
            stop_loss real not null,
            take_profit_1 real not null,
            take_profit_2 real not null,
            trailing_stop real not null,
            position_pct real not null,
            industry text,
            topics text,
            theme_strength real,
            reasons text,
            sell_triggers text,
            warnings text,
            created_at text not null,
            foreign key(run_id) references scan_runs(id)
        );

        create index if not exists idx_recommendations_date
            on recommendations(trade_date, horizon, score desc);
        create index if not exists idx_recommendations_symbol
            on recommendations(symbol, trade_date desc);

        create table if not exists stock_analysis (
            id integer primary key autoincrement,
            trade_date text not null,
            query text not null,
            symbol text not null,
            name text,
            score real not null,
            rating text not null,
            action text not null,
            close real not null,
            buy_zone_low real not null,
            buy_zone_high real not null,
            stop_loss real not null,
            take_profit_1 real not null,
            take_profit_2 real not null,
            trailing_stop real not null,
            position_pct real not null,
            reasons text,
            sell_triggers text,
            warnings text,
            created_at text not null
        );

        create table if not exists market_snapshots (
            id integer primary key autoincrement,
            run_id integer,
            trade_date text not null,
            total integer,
            up_count integer,
            down_count integer,
            flat_count integer,
            limit_up integer,
            limit_down integer,
            strong_count integer,
            weak_count integer,
            turnover real,
            median_change real,
            up_ratio real,
            adv_dec_ratio real,
            temperature real,
            temperature_label text,
            global_score real,
            global_label text,
            global_indices text,
            created_at text not null
        );

        create table if not exists capital_hotspots (
            id integer primary key autoincrement,
            run_id integer not null,
            trade_date text not null,
            period text not null,
            category text not null,
            rank integer,
            name text not null,
            net_inflow_yi real,
            change_pct real,
            stock_count integer,
            leader text,
            leader_change_pct real,
            created_at text not null,
            foreign key(run_id) references scan_runs(id)
        );

        create index if not exists idx_capital_hotspots_run
            on capital_hotspots(run_id, period, category, rank);

        create table if not exists sentiment_snapshots (
            id integer primary key autoincrement,
            trade_date text not null unique,
            limit_up_count integer not null,
            broken_count integer not null,
            limit_down_count integer not null,
            first_board_count integer not null,
            second_board_count integer not null,
            third_board_count integer not null,
            high_board_count integer not null,
            max_streak integer not null,
            seal_rate real not null,
            broken_rate real not null,
            promotion_rate real not null,
            previous_premium real not null,
            previous_red_rate real not null,
            emotion_score real not null,
            emotion_stage text not null,
            components_json text,
            created_at text not null
        );

        create index if not exists idx_sentiment_snapshots_date
            on sentiment_snapshots(trade_date desc);

        create table if not exists limit_up_ladder (
            id integer primary key autoincrement,
            trade_date text not null,
            symbol text not null,
            code text not null,
            name text not null,
            streak integer not null,
            change_pct real,
            price real,
            turnover real,
            turnover_rate real,
            seal_amount real,
            first_seal_time text,
            last_seal_time text,
            break_count integer,
            industry text,
            created_at text not null,
            unique(trade_date, symbol)
        );

        create index if not exists idx_limit_up_ladder_date_streak
            on limit_up_ladder(trade_date desc, streak desc);

        create table if not exists recommendation_outcomes (
            recommendation_id integer primary key,
            run_id integer not null,
            trade_date text not null,
            horizon text not null,
            symbol text not null,
            name text,
            entry_price real not null,
            available_days integer not null default 0,
            t1_return real,
            t3_return real,
            t5_return real,
            t20_return real,
            max_gain_20 real,
            max_drawdown_20 real,
            stop_hit integer not null default 0,
            stop_hit_date text,
            target1_hit integer not null default 0,
            target1_hit_date text,
            target2_hit integer not null default 0,
            target2_hit_date text,
            target1_before_stop integer,
            review_status text not null,
            evaluated_through text,
            updated_at text not null,
            foreign key(recommendation_id) references recommendations(id)
        );

        create index if not exists idx_recommendation_outcomes_date
            on recommendation_outcomes(trade_date, horizon);
        create index if not exists idx_recommendation_outcomes_symbol
            on recommendation_outcomes(symbol, trade_date desc);

        create table if not exists watchlist_positions (
            id integer primary key autoincrement,
            owner_key text not null default 'local',
            symbol text not null,
            name text,
            cost_price real not null,
            shares integer not null,
            available_cash real not null default 0,
            account_assets real not null default 0,
            sellable_shares integer not null default 0,
            t_ratio real not null default 0.2,
            t_preference text not null default '自动判断',
            target_break_even real,
            note text,
            is_active integer not null default 1,
            created_at text not null,
            updated_at text not null,
            unique(owner_key, symbol)
        );

        create index if not exists idx_watchlist_positions_owner
            on watchlist_positions(owner_key, is_active, updated_at desc);

        create table if not exists position_plan_snapshots (
            id integer primary key autoincrement,
            owner_key text not null default 'local',
            position_id integer,
            trade_date text not null,
            symbol text not null,
            name text,
            current_price real,
            cost_price real,
            shares integer,
            t_buy_low real,
            t_buy_high real,
            t_sell_low real,
            t_sell_high real,
            feasibility_score integer,
            feasibility_label text,
            action_summary text,
            recovery_price_after_one real,
            recovery_price_after_three real,
            plan_json text,
            created_at text not null,
            foreign key(position_id) references watchlist_positions(id)
        );

        create index if not exists idx_position_plan_snapshots_owner
            on position_plan_snapshots(owner_key, trade_date desc, created_at desc);

        create table if not exists alert_rules (
            id integer primary key autoincrement,
            owner_key text not null default 'local',
            symbol text,
            name text,
            alert_type text not null,
            comparator text not null default '>=',
            threshold_value real,
            enabled integer not null default 1,
            note text,
            created_at text not null,
            updated_at text not null
        );

        create index if not exists idx_alert_rules_owner
            on alert_rules(owner_key, enabled, updated_at desc);

        create table if not exists alert_events (
            id integer primary key autoincrement,
            owner_key text not null default 'local',
            rule_id integer,
            trade_date text not null,
            symbol text,
            name text,
            alert_type text not null,
            severity text not null default '提示',
            observed_value real,
            message text not null,
            created_at text not null,
            foreign key(rule_id) references alert_rules(id)
        );

        create index if not exists idx_alert_events_owner
            on alert_events(owner_key, trade_date desc, created_at desc);

        create table if not exists paper_accounts (
            id integer primary key autoincrement,
            owner_key text not null default 'local',
            name text not null,
            initial_cash real not null,
            cash real not null,
            equity real not null,
            is_active integer not null default 1,
            created_at text not null,
            updated_at text not null,
            unique(owner_key, name)
        );

        create index if not exists idx_paper_accounts_owner
            on paper_accounts(owner_key, is_active, updated_at desc);

        create table if not exists paper_trades (
            id integer primary key autoincrement,
            account_id integer not null,
            owner_key text not null default 'local',
            trade_date text not null,
            recommendation_id integer,
            symbol text not null,
            name text,
            horizon text,
            strategy_type text,
            side text not null,
            price real not null,
            shares integer not null,
            amount real not null,
            fee real not null default 0,
            close_price real,
            return_pct real,
            status text not null default '持仓中',
            reason text,
            created_at text not null,
            foreign key(account_id) references paper_accounts(id),
            foreign key(recommendation_id) references recommendations(id)
        );

        create index if not exists idx_paper_trades_account
            on paper_trades(account_id, trade_date desc, id desc);

        create table if not exists daily_review_reports (
            id integer primary key autoincrement,
            owner_key text not null default 'local',
            trade_date text not null,
            title text not null,
            summary text,
            report_text text not null,
            metrics_json text,
            created_at text not null,
            unique(owner_key, trade_date)
        );

        create index if not exists idx_daily_review_reports_owner
            on daily_review_reports(owner_key, trade_date desc);

        create table if not exists broker_order_ledger (
            id integer primary key autoincrement,
            owner_key text not null default 'local',
            client_order_id text not null,
            broker text not null default '东吴QMT',
            account_mask text,
            symbol text not null,
            side text not null,
            quantity integer not null,
            limit_price real not null,
            notional real not null,
            mode text not null default '草稿',
            status text not null default '草稿',
            broker_order_id text,
            strategy_name text,
            risk_snapshot_json text,
            error_message text,
            created_at text not null,
            updated_at text not null,
            unique(owner_key, client_order_id)
        );

        create index if not exists idx_broker_order_ledger_owner
            on broker_order_ledger(owner_key, updated_at desc);

        create table if not exists trading_risk_state (
            owner_key text primary key,
            state_date text not null,
            kill_switch integer not null default 0,
            daily_realized_pnl real not null default 0,
            daily_order_count integer not null default 0,
            max_daily_loss real not null default 3000,
            max_daily_orders integer not null default 20,
            max_order_notional real not null default 50000,
            max_single_position_pct real not null default 0.25,
            updated_at text not null
        );

        create table if not exists trading_runtime_events (
            id integer primary key autoincrement,
            owner_key text not null default 'local',
            event_type text not null,
            severity text not null default '提示',
            message text not null,
            details_json text,
            created_at text not null
        );

        create index if not exists idx_trading_runtime_events_owner
            on trading_runtime_events(owner_key, created_at desc);
        """
    )
    existing = {
        row["name"]
        for row in conn.execute("pragma table_info(recommendations)").fetchall()
    }
    additions = {
        "fundamental_score": "real",
        "net_inflow_3d": "real",
        "net_inflow_5d": "real",
        "net_inflow_10d": "real",
        "pe_est": "real",
        "pb_est": "real",
        "industry_pe_median": "real",
        "industry_pb_median": "real",
        "operating_cash_flow": "real",
        "operating_cash_flow_per_share": "real",
        "dividend_count": "real",
        "dividend_year_ratio": "real",
        "valuation_normal": "integer",
        "cashflow_good": "integer",
        "dividend_stable": "integer",
    }
    for column, column_type in additions.items():
        if column not in existing:
            conn.execute(f"alter table recommendations add column {column} {column_type}")
    run_existing = {
        row["name"]
        for row in conn.execute("pragma table_info(scan_runs)").fetchall()
    }
    run_additions = {
        "strategy_version": "text",
        "parameters_json": "text",
    }
    for column, column_type in run_additions.items():
        if column not in run_existing:
            conn.execute(f"alter table scan_runs add column {column} {column_type}")
    outcome_existing = {
        row["name"]
        for row in conn.execute("pragma table_info(recommendation_outcomes)").fetchall()
    }
    outcome_additions = {
        "t3_return": "real",
    }
    for column, column_type in outcome_additions.items():
        if column not in outcome_existing:
            conn.execute(f"alter table recommendation_outcomes add column {column} {column_type}")
    conn.commit()


def create_run(
    trade_date: str,
    scan_size: int,
    top_n: int,
    min_turnover: float,
    status: str = "running",
    note: str = "",
    strategy_version: str = STRATEGY_VERSION,
    parameters: dict[str, Any] | None = None,
    db_path: str | Path = DB_PATH,
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with db_connection(db_path) as conn:
        cur = conn.execute(
            """
            insert into scan_runs (
                trade_date, run_time, scan_size, top_n, min_turnover,
                strategy_version, parameters_json, status, note
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_date,
                now,
                scan_size,
                top_n,
                min_turnover,
                strategy_version,
                _json(parameters or {}),
                status,
                note,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_run(run_id: int, status: str, note: str = "", db_path: str | Path = DB_PATH) -> None:
    with db_connection(db_path) as conn:
        conn.execute("update scan_runs set status = ?, note = ? where id = ?", (status, note, run_id))
        conn.commit()


def _mark_stale_running_runs_in_connection(
    conn: sqlite3.Connection,
    max_age_minutes: int = 120,
) -> int:
    cutoff = (datetime.now() - timedelta(minutes=max_age_minutes)).isoformat(timespec="seconds")
    cursor = conn.execute(
        """
        update scan_runs
        set status = 'failed',
            note = case
                when coalesce(note, '') = ''
                    then '运行中断：服务重启或进程退出，已自动关闭旧任务'
                else note || '；运行中断：已自动关闭旧任务'
            end
        where status = 'running'
          and run_time < ?
        """,
        (cutoff,),
    )
    if cursor.rowcount:
        conn.commit()
    return int(cursor.rowcount or 0)


def mark_stale_running_runs(
    max_age_minutes: int = 120,
    db_path: str | Path = DB_PATH,
) -> int:
    with db_connection(db_path) as conn:
        return _mark_stale_running_runs_in_connection(conn, max_age_minutes=max_age_minutes)


def _json(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _float_or_none(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def save_recommendations(records: list[dict[str, Any]], db_path: str | Path = DB_PATH) -> None:
    if not records:
        return
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for item in records:
        rows.append(
            (
                item["run_id"],
                item["trade_date"],
                item["horizon"],
                item["symbol"],
                item.get("name", ""),
                float(item["score"]),
                float(item.get("base_score", 0)),
                item.get("rating", ""),
                item.get("priority", ""),
                item.get("action", ""),
                float(item["close"]),
                float(item["buy_zone_low"]),
                float(item["buy_zone_high"]),
                float(item["stop_loss"]),
                float(item["take_profit_1"]),
                float(item["take_profit_2"]),
                float(item["trailing_stop"]),
                float(item.get("position_pct", 0)),
                item.get("industry", ""),
                _json(item.get("topics", "")),
                float(item.get("theme_strength", 0)),
                _float_or_none(item.get("fundamental_score")),
                _float_or_none(item.get("net_inflow_3d")),
                _float_or_none(item.get("net_inflow_5d")),
                _float_or_none(item.get("net_inflow_10d")),
                _float_or_none(item.get("pe_est")),
                _float_or_none(item.get("pb_est")),
                _float_or_none(item.get("industry_pe_median")),
                _float_or_none(item.get("industry_pb_median")),
                _float_or_none(item.get("operating_cash_flow")),
                _float_or_none(item.get("operating_cash_flow_per_share")),
                _float_or_none(item.get("dividend_count")),
                _float_or_none(item.get("dividend_year_ratio")),
                int(bool(item.get("valuation_normal", False))),
                int(bool(item.get("cashflow_good", False))),
                int(bool(item.get("dividend_stable", False))),
                _json(item.get("reasons", "")),
                _json(item.get("sell_triggers", "")),
                _json(item.get("warnings", "")),
                now,
            )
        )

    with db_connection(db_path) as conn:
        conn.executemany(
            """
            insert into recommendations (
                run_id, trade_date, horizon, symbol, name, score, base_score, rating, priority, action,
                close, buy_zone_low, buy_zone_high, stop_loss, take_profit_1, take_profit_2,
                trailing_stop, position_pct, industry, topics, theme_strength,
                fundamental_score, net_inflow_3d, net_inflow_5d, net_inflow_10d,
                pe_est, pb_est, industry_pe_median, industry_pb_median,
                operating_cash_flow, operating_cash_flow_per_share,
                dividend_count, dividend_year_ratio, valuation_normal, cashflow_good,
                dividend_stable, reasons, sell_triggers, warnings, created_at
            )
            values (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            rows,
        )
        conn.commit()


def save_capital_hotspots(
    run_id: int,
    frame: pd.DataFrame,
    db_path: str | Path = DB_PATH,
) -> None:
    if frame.empty:
        return
    now = datetime.now().isoformat(timespec="seconds")
    trade_date = datetime.now().strftime("%Y-%m-%d")
    rows = []
    for _, item in frame.iterrows():
        rows.append(
            (
                run_id,
                trade_date,
                str(item.get("period", "")),
                str(item.get("category", "")),
                int(item["rank"]) if pd.notna(item.get("rank")) else None,
                str(item.get("name", "")),
                _float_or_none(item.get("net_inflow_yi")),
                _float_or_none(item.get("change_pct")),
                int(item["stock_count"]) if pd.notna(item.get("stock_count")) else None,
                str(item.get("leader", "")) if pd.notna(item.get("leader")) else "",
                _float_or_none(item.get("leader_change_pct")),
                now,
            )
        )
    with db_connection(db_path) as conn:
        conn.executemany(
            """
            insert into capital_hotspots (
                run_id, trade_date, period, category, rank, name, net_inflow_yi,
                change_pct, stock_count, leader, leader_change_pct, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def save_stock_analysis(
    query: str,
    report: Any,
    db_path: str | Path = DB_PATH,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with db_connection(db_path) as conn:
        conn.execute(
            """
            insert into stock_analysis (
                trade_date, query, symbol, name, score, rating, action, close,
                buy_zone_low, buy_zone_high, stop_loss, take_profit_1, take_profit_2,
                trailing_stop, position_pct, reasons, sell_triggers, warnings, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.date,
                query,
                report.symbol.upper(),
                report.name,
                report.score,
                report.rating,
                report.action,
                report.close,
                report.buy_zone_low,
                report.buy_zone_high,
                report.stop_loss,
                report.take_profit_1,
                report.take_profit_2,
                report.trailing_stop,
                report.position_pct,
                _json(report.reasons),
                _json(report.sell_triggers),
                _json(report.warnings),
                now,
            ),
        )
        conn.commit()


def save_market_snapshot(
    run_id: int | None,
    market: dict[str, Any],
    global_summary: dict[str, Any],
    global_indices: pd.DataFrame,
    db_path: str | Path = DB_PATH,
) -> None:
    if not market:
        return
    now = datetime.now().isoformat(timespec="seconds")
    trade_date = datetime.now().strftime("%Y-%m-%d")
    indices_json = global_indices.to_json(orient="records", force_ascii=False) if not global_indices.empty else "[]"
    with db_connection(db_path) as conn:
        conn.execute(
            """
            insert into market_snapshots (
                run_id, trade_date, total, up_count, down_count, flat_count,
                limit_up, limit_down, strong_count, weak_count, turnover,
                median_change, up_ratio, adv_dec_ratio, temperature, temperature_label,
                global_score, global_label, global_indices, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                trade_date,
                market.get("total", 0),
                market.get("up", 0),
                market.get("down", 0),
                market.get("flat", 0),
                market.get("limit_up", 0),
                market.get("limit_down", 0),
                market.get("strong", 0),
                market.get("weak", 0),
                market.get("turnover", 0),
                market.get("median_change", 0),
                market.get("up_ratio", 0),
                market.get("adv_dec_ratio", 0),
                market.get("temperature", 0),
                market.get("temperature_label", ""),
                global_summary.get("score", 0),
                global_summary.get("label", ""),
                indices_json,
                now,
            ),
        )
        conn.commit()


def save_sentiment_snapshot(
    snapshot: dict[str, Any],
    ladder: pd.DataFrame,
    db_path: str | Path = DB_PATH,
) -> None:
    if not snapshot:
        return
    now = datetime.now().isoformat(timespec="seconds")
    trade_date = str(snapshot["trade_date"])
    with db_connection(db_path) as conn:
        conn.execute(
            """
            insert into sentiment_snapshots (
                trade_date, limit_up_count, broken_count, limit_down_count,
                first_board_count, second_board_count, third_board_count,
                high_board_count, max_streak, seal_rate, broken_rate,
                promotion_rate, previous_premium, previous_red_rate,
                emotion_score, emotion_stage, components_json, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(trade_date) do update set
                limit_up_count = excluded.limit_up_count,
                broken_count = excluded.broken_count,
                limit_down_count = excluded.limit_down_count,
                first_board_count = excluded.first_board_count,
                second_board_count = excluded.second_board_count,
                third_board_count = excluded.third_board_count,
                high_board_count = excluded.high_board_count,
                max_streak = excluded.max_streak,
                seal_rate = excluded.seal_rate,
                broken_rate = excluded.broken_rate,
                promotion_rate = excluded.promotion_rate,
                previous_premium = excluded.previous_premium,
                previous_red_rate = excluded.previous_red_rate,
                emotion_score = excluded.emotion_score,
                emotion_stage = excluded.emotion_stage,
                components_json = excluded.components_json,
                created_at = excluded.created_at
            """,
            (
                trade_date,
                snapshot["limit_up_count"],
                snapshot["broken_count"],
                snapshot["limit_down_count"],
                snapshot["first_board_count"],
                snapshot["second_board_count"],
                snapshot["third_board_count"],
                snapshot["high_board_count"],
                snapshot["max_streak"],
                snapshot["seal_rate"],
                snapshot["broken_rate"],
                snapshot["promotion_rate"],
                snapshot["previous_premium"],
                snapshot["previous_red_rate"],
                snapshot["emotion_score"],
                snapshot["emotion_stage"],
                _json(snapshot.get("components", {})),
                now,
            ),
        )
        conn.execute("delete from limit_up_ladder where trade_date = ?", (trade_date,))
        if not ladder.empty:
            rows = []
            for _, item in ladder.iterrows():
                rows.append(
                    (
                        trade_date,
                        str(item.get("symbol", "")).upper(),
                        str(item.get("code", "")),
                        str(item.get("name", "")),
                        int(item.get("streak", 1)),
                        _float_or_none(item.get("change_pct")),
                        _float_or_none(item.get("price")),
                        _float_or_none(item.get("turnover")),
                        _float_or_none(item.get("turnover_rate")),
                        _float_or_none(item.get("seal_amount")),
                        str(item.get("first_seal_time", "")),
                        str(item.get("last_seal_time", "")),
                        int(item.get("break_count", 0)),
                        str(item.get("industry", "")),
                        now,
                    )
                )
            conn.executemany(
                """
                insert into limit_up_ladder (
                    trade_date, symbol, code, name, streak, change_pct, price,
                    turnover, turnover_rate, seal_amount, first_seal_time,
                    last_seal_time, break_count, industry, created_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        conn.commit()


def load_sentiment_history(
    limit: int = 90,
    db_path: str | Path = DB_PATH,
) -> pd.DataFrame:
    try:
        with db_connection(db_path) as conn:
            return pd.read_sql_query(
                """
                select * from sentiment_snapshots
                order by trade_date desc
                limit ?
                """,
                conn,
                params=(limit,),
            )
    except sqlite3.DatabaseError:
        return pd.DataFrame()


def load_limit_up_ladder(
    trade_date: str | None = None,
    db_path: str | Path = DB_PATH,
) -> pd.DataFrame:
    try:
        with db_connection(db_path) as conn:
            selected_date = trade_date
            if selected_date is None:
                row = conn.execute(
                    "select trade_date from sentiment_snapshots order by trade_date desc limit 1"
                ).fetchone()
                selected_date = str(row["trade_date"]) if row else None
            if not selected_date:
                return pd.DataFrame()
            return pd.read_sql_query(
                """
                select * from limit_up_ladder
                where trade_date = ?
                order by streak desc, seal_amount desc, first_seal_time
                """,
                conn,
                params=(selected_date,),
            )
    except sqlite3.DatabaseError:
        return pd.DataFrame()


def latest_run(db_path: str | Path = DB_PATH) -> dict[str, Any] | None:
    with db_connection(db_path) as conn:
        _mark_stale_running_runs_in_connection(conn)
        row = conn.execute("select * from scan_runs order by id desc limit 1").fetchone()
        return dict(row) if row else None


def load_recommendations(
    trade_date: str | None = None,
    run_id: int | None = None,
    db_path: str | Path = DB_PATH,
) -> pd.DataFrame:
    where = []
    params: list[Any] = []
    if run_id is not None:
        where.append("run_id = ?")
        params.append(run_id)
    if trade_date is not None:
        where.append("trade_date = ?")
        params.append(trade_date)
    clause = " where " + " and ".join(where) if where else ""
    query = f"select * from recommendations{clause} order by horizon, score desc"
    with db_connection(db_path) as conn:
        return pd.read_sql_query(query, conn, params=params)


def load_recommendations_for_review(
    db_path: str | Path = DB_PATH,
) -> pd.DataFrame:
    with db_connection(db_path) as conn:
        return pd.read_sql_query(
            """
            select r.*
            from recommendations r
            join (
                select max(id) as id
                from recommendations
                group by trade_date, symbol, horizon
            ) latest on latest.id = r.id
            order by r.trade_date desc, r.horizon, r.score desc
            """,
            conn,
        )


def save_recommendation_outcomes(
    records: list[dict[str, Any]],
    db_path: str | Path = DB_PATH,
) -> None:
    if not records:
        return
    now = datetime.now().isoformat(timespec="seconds")
    rows = [
        (
            item["recommendation_id"],
            item["run_id"],
            item["trade_date"],
            item["horizon"],
            item["symbol"],
            item.get("name", ""),
            item["entry_price"],
            item["available_days"],
            item.get("t1_return"),
            item.get("t3_return"),
            item.get("t5_return"),
            item.get("t20_return"),
            item.get("max_gain_20"),
            item.get("max_drawdown_20"),
            item.get("stop_hit", 0),
            item.get("stop_hit_date"),
            item.get("target1_hit", 0),
            item.get("target1_hit_date"),
            item.get("target2_hit", 0),
            item.get("target2_hit_date"),
            item.get("target1_before_stop"),
            item["review_status"],
            item.get("evaluated_through"),
            now,
        )
        for item in records
    ]
    with db_connection(db_path) as conn:
        conn.executemany(
            """
            insert into recommendation_outcomes (
                recommendation_id, run_id, trade_date, horizon, symbol, name,
                entry_price, available_days, t1_return, t3_return, t5_return, t20_return,
                max_gain_20, max_drawdown_20, stop_hit, stop_hit_date,
                target1_hit, target1_hit_date, target2_hit, target2_hit_date,
                target1_before_stop, review_status, evaluated_through, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(recommendation_id) do update set
                available_days = excluded.available_days,
                t1_return = excluded.t1_return,
                t3_return = excluded.t3_return,
                t5_return = excluded.t5_return,
                t20_return = excluded.t20_return,
                max_gain_20 = excluded.max_gain_20,
                max_drawdown_20 = excluded.max_drawdown_20,
                stop_hit = excluded.stop_hit,
                stop_hit_date = excluded.stop_hit_date,
                target1_hit = excluded.target1_hit,
                target1_hit_date = excluded.target1_hit_date,
                target2_hit = excluded.target2_hit,
                target2_hit_date = excluded.target2_hit_date,
                target1_before_stop = excluded.target1_before_stop,
                review_status = excluded.review_status,
                evaluated_through = excluded.evaluated_through,
                updated_at = excluded.updated_at
            """,
            rows,
        )
        conn.commit()


def load_recommendation_outcomes(
    db_path: str | Path = DB_PATH,
) -> pd.DataFrame:
    with db_connection(db_path) as conn:
        return pd.read_sql_query(
            """
            select o.*, r.score, r.base_score, r.priority, r.industry,
                   r.stop_loss, r.take_profit_1, r.take_profit_2,
                   coalesce(s.strategy_version, '历史未标记版本') as strategy_version
            from recommendation_outcomes o
            join recommendations r on r.id = o.recommendation_id
            join scan_runs s on s.id = o.run_id
            order by o.trade_date desc, o.horizon, r.score desc
            """,
            conn,
        )


def save_watchlist_position(
    item: dict[str, Any],
    db_path: str | Path = DB_PATH,
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    owner_key = str(item.get("owner_key") or "local")
    symbol = str(item["symbol"]).upper()
    with db_connection(db_path) as conn:
        row = conn.execute(
            "select id, created_at from watchlist_positions where owner_key = ? and symbol = ?",
            (owner_key, symbol),
        ).fetchone()
        if row:
            conn.execute(
                """
                update watchlist_positions
                set name = ?, cost_price = ?, shares = ?, available_cash = ?,
                    account_assets = ?, sellable_shares = ?, t_ratio = ?,
                    t_preference = ?, target_break_even = ?, note = ?,
                    is_active = 1, updated_at = ?
                where id = ?
                """,
                (
                    item.get("name", ""),
                    float(item.get("cost_price") or 0),
                    int(item.get("shares") or 0),
                    float(item.get("available_cash") or 0),
                    float(item.get("account_assets") or 0),
                    int(item.get("sellable_shares") or 0),
                    float(item.get("t_ratio") or 0.2),
                    str(item.get("t_preference") or "自动判断"),
                    _float_or_none(item.get("target_break_even")),
                    item.get("note", ""),
                    now,
                    int(row["id"]),
                ),
            )
            conn.commit()
            return int(row["id"])
        cur = conn.execute(
            """
            insert into watchlist_positions (
                owner_key, symbol, name, cost_price, shares, available_cash,
                account_assets, sellable_shares, t_ratio, t_preference,
                target_break_even, note, is_active, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                owner_key,
                symbol,
                item.get("name", ""),
                float(item.get("cost_price") or 0),
                int(item.get("shares") or 0),
                float(item.get("available_cash") or 0),
                float(item.get("account_assets") or 0),
                int(item.get("sellable_shares") or 0),
                float(item.get("t_ratio") or 0.2),
                str(item.get("t_preference") or "自动判断"),
                _float_or_none(item.get("target_break_even")),
                item.get("note", ""),
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def load_watchlist_positions(
    owner_key: str = "local",
    active_only: bool = True,
    db_path: str | Path = DB_PATH,
) -> pd.DataFrame:
    query = "select * from watchlist_positions where owner_key = ?"
    params: list[Any] = [owner_key]
    if active_only:
        query += " and is_active = 1"
    query += " order by updated_at desc, id desc"
    with db_connection(db_path) as conn:
        return pd.read_sql_query(query, conn, params=params)


def deactivate_watchlist_position(
    position_id: int,
    owner_key: str = "local",
    db_path: str | Path = DB_PATH,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with db_connection(db_path) as conn:
        conn.execute(
            "update watchlist_positions set is_active = 0, updated_at = ? where id = ? and owner_key = ?",
            (now, position_id, owner_key),
        )
        conn.commit()


def save_position_plan_snapshot(
    item: dict[str, Any],
    db_path: str | Path = DB_PATH,
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with db_connection(db_path) as conn:
        cur = conn.execute(
            """
            insert into position_plan_snapshots (
                owner_key, position_id, trade_date, symbol, name, current_price,
                cost_price, shares, t_buy_low, t_buy_high, t_sell_low, t_sell_high,
                feasibility_score, feasibility_label, action_summary,
                recovery_price_after_one, recovery_price_after_three, plan_json, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(item.get("owner_key") or "local"),
                item.get("position_id"),
                str(item.get("trade_date") or date.today().strftime("%Y-%m-%d")),
                str(item.get("symbol") or "").upper(),
                item.get("name", ""),
                _float_or_none(item.get("current_price")),
                _float_or_none(item.get("cost_price")),
                int(item.get("shares") or 0),
                _float_or_none(item.get("t_buy_low")),
                _float_or_none(item.get("t_buy_high")),
                _float_or_none(item.get("t_sell_low")),
                _float_or_none(item.get("t_sell_high")),
                int(item.get("feasibility_score") or 0),
                item.get("feasibility_label", ""),
                item.get("action_summary", ""),
                _float_or_none(item.get("recovery_price_after_one")),
                _float_or_none(item.get("recovery_price_after_three")),
                _json(item.get("plan_json") or {}),
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def load_position_plan_snapshots(
    owner_key: str = "local",
    limit: int = 50,
    db_path: str | Path = DB_PATH,
) -> pd.DataFrame:
    with db_connection(db_path) as conn:
        return pd.read_sql_query(
            """
            select * from position_plan_snapshots
            where owner_key = ?
            order by created_at desc, id desc
            limit ?
            """,
            conn,
            params=(owner_key, limit),
        )


def save_alert_rule(
    item: dict[str, Any],
    db_path: str | Path = DB_PATH,
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with db_connection(db_path) as conn:
        cur = conn.execute(
            """
            insert into alert_rules (
                owner_key, symbol, name, alert_type, comparator, threshold_value,
                enabled, note, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(item.get("owner_key") or "local"),
                str(item.get("symbol") or "").upper() or None,
                item.get("name", ""),
                str(item.get("alert_type") or "到达买点"),
                str(item.get("comparator") or ">="),
                _float_or_none(item.get("threshold_value")),
                int(bool(item.get("enabled", True))),
                item.get("note", ""),
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def load_alert_rules(
    owner_key: str = "local",
    enabled_only: bool = False,
    db_path: str | Path = DB_PATH,
) -> pd.DataFrame:
    query = "select * from alert_rules where owner_key = ?"
    params: list[Any] = [owner_key]
    if enabled_only:
        query += " and enabled = 1"
    query += " order by updated_at desc, id desc"
    with db_connection(db_path) as conn:
        return pd.read_sql_query(query, conn, params=params)


def update_alert_rule_enabled(
    rule_id: int,
    enabled: bool,
    owner_key: str = "local",
    db_path: str | Path = DB_PATH,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with db_connection(db_path) as conn:
        conn.execute(
            "update alert_rules set enabled = ?, updated_at = ? where id = ? and owner_key = ?",
            (int(enabled), now, rule_id, owner_key),
        )
        conn.commit()


def save_alert_events(
    records: list[dict[str, Any]],
    db_path: str | Path = DB_PATH,
) -> None:
    if not records:
        return
    now = datetime.now().isoformat(timespec="seconds")
    rows = [
        (
            str(item.get("owner_key") or "local"),
            item.get("rule_id"),
            str(item.get("trade_date") or date.today().strftime("%Y-%m-%d")),
            str(item.get("symbol") or "").upper() or None,
            item.get("name", ""),
            str(item.get("alert_type") or "提示"),
            str(item.get("severity") or "提示"),
            _float_or_none(item.get("observed_value")),
            str(item.get("message") or ""),
            now,
        )
        for item in records
    ]
    with db_connection(db_path) as conn:
        conn.executemany(
            """
            insert into alert_events (
                owner_key, rule_id, trade_date, symbol, name, alert_type,
                severity, observed_value, message, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def load_alert_events(
    owner_key: str = "local",
    limit: int = 100,
    db_path: str | Path = DB_PATH,
) -> pd.DataFrame:
    with db_connection(db_path) as conn:
        return pd.read_sql_query(
            """
            select * from alert_events
            where owner_key = ?
            order by created_at desc, id desc
            limit ?
            """,
            conn,
            params=(owner_key, limit),
        )


def get_or_create_paper_account(
    owner_key: str = "local",
    name: str = "默认模拟账户",
    initial_cash: float = 100_000.0,
    db_path: str | Path = DB_PATH,
) -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    with db_connection(db_path) as conn:
        row = conn.execute(
            "select * from paper_accounts where owner_key = ? and name = ?",
            (owner_key, name),
        ).fetchone()
        if row:
            return dict(row)
        cur = conn.execute(
            """
            insert into paper_accounts (
                owner_key, name, initial_cash, cash, equity, is_active, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (owner_key, name, float(initial_cash), float(initial_cash), float(initial_cash), now, now),
        )
        conn.commit()
        account_id = int(cur.lastrowid)
        row = conn.execute("select * from paper_accounts where id = ?", (account_id,)).fetchone()
        return dict(row)


def save_paper_trades(
    records: list[dict[str, Any]],
    db_path: str | Path = DB_PATH,
) -> None:
    if not records:
        return
    now = datetime.now().isoformat(timespec="seconds")
    rows = [
        (
            int(item["account_id"]),
            str(item.get("owner_key") or "local"),
            str(item.get("trade_date") or date.today().strftime("%Y-%m-%d")),
            item.get("recommendation_id"),
            str(item.get("symbol") or "").upper(),
            item.get("name", ""),
            item.get("horizon", ""),
            item.get("strategy_type", ""),
            str(item.get("side") or "买入"),
            float(item.get("price") or 0),
            int(item.get("shares") or 0),
            float(item.get("amount") or 0),
            float(item.get("fee") or 0),
            _float_or_none(item.get("close_price")),
            _float_or_none(item.get("return_pct")),
            str(item.get("status") or "持仓中"),
            item.get("reason", ""),
            now,
        )
        for item in records
    ]
    with db_connection(db_path) as conn:
        conn.executemany(
            """
            insert into paper_trades (
                account_id, owner_key, trade_date, recommendation_id, symbol, name,
                horizon, strategy_type, side, price, shares, amount, fee,
                close_price, return_pct, status, reason, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        total = conn.execute(
            """
            select
                coalesce(sum(case when status = '持仓中' then amount else 0 end), 0) as position_cost,
                coalesce(sum(case when return_pct is not null then amount * (1 + return_pct) - amount else 0 end), 0) as realized_pnl
            from paper_trades
            where account_id = ?
            """,
            (int(records[0]["account_id"]),),
        ).fetchone()
        account = conn.execute("select * from paper_accounts where id = ?", (int(records[0]["account_id"]),)).fetchone()
        if account:
            equity = float(account["initial_cash"]) + float(total["realized_pnl"] or 0)
            cash = max(0.0, equity - float(total["position_cost"] or 0))
            conn.execute(
                "update paper_accounts set cash = ?, equity = ?, updated_at = ? where id = ?",
                (cash, equity, now, int(records[0]["account_id"])),
            )
        conn.commit()


def load_paper_accounts(
    owner_key: str = "local",
    db_path: str | Path = DB_PATH,
) -> pd.DataFrame:
    with db_connection(db_path) as conn:
        return pd.read_sql_query(
            "select * from paper_accounts where owner_key = ? order by updated_at desc, id desc",
            conn,
            params=(owner_key,),
        )


def load_paper_trades(
    owner_key: str = "local",
    account_id: int | None = None,
    limit: int = 500,
    db_path: str | Path = DB_PATH,
) -> pd.DataFrame:
    query = "select * from paper_trades where owner_key = ?"
    params: list[Any] = [owner_key]
    if account_id is not None:
        query += " and account_id = ?"
        params.append(int(account_id))
    query += " order by trade_date desc, id desc limit ?"
    params.append(int(limit))
    with db_connection(db_path) as conn:
        return pd.read_sql_query(query, conn, params=params)


def save_daily_review_report(
    owner_key: str,
    trade_date: str,
    title: str,
    summary: str,
    report_text: str,
    metrics: dict[str, Any] | None = None,
    db_path: str | Path = DB_PATH,
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with db_connection(db_path) as conn:
        cur = conn.execute(
            """
            insert into daily_review_reports (
                owner_key, trade_date, title, summary, report_text, metrics_json, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?)
            on conflict(owner_key, trade_date) do update set
                title = excluded.title,
                summary = excluded.summary,
                report_text = excluded.report_text,
                metrics_json = excluded.metrics_json,
                created_at = excluded.created_at
            """,
            (owner_key, trade_date, title, summary, report_text, _json(metrics or {}), now),
        )
        conn.commit()
        return int(cur.lastrowid or 0)


def load_daily_review_reports(
    owner_key: str = "local",
    limit: int = 30,
    db_path: str | Path = DB_PATH,
) -> pd.DataFrame:
    with db_connection(db_path) as conn:
        return pd.read_sql_query(
            """
            select * from daily_review_reports
            where owner_key = ?
            order by trade_date desc, id desc
            limit ?
            """,
            conn,
            params=(owner_key, limit),
        )


def get_trading_risk_state(
    owner_key: str = "local",
    db_path: str | Path = DB_PATH,
) -> dict[str, Any]:
    today = date.today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    with db_connection(db_path) as conn:
        conn.execute(
            """
            insert into trading_risk_state (owner_key, state_date, updated_at)
            values (?, ?, ?)
            on conflict(owner_key) do nothing
            """,
            (owner_key, today, now),
        )
        conn.execute(
            """
            update trading_risk_state
            set state_date = ?, daily_realized_pnl = 0, daily_order_count = 0, updated_at = ?
            where owner_key = ? and state_date <> ?
            """,
            (today, now, owner_key, today),
        )
        conn.commit()
        row = conn.execute(
            "select * from trading_risk_state where owner_key = ?",
            (owner_key,),
        ).fetchone()
        return dict(row) if row else {}


def update_trading_risk_state(
    owner_key: str = "local",
    *,
    kill_switch: bool | None = None,
    daily_realized_pnl: float | None = None,
    increment_orders: int = 0,
    policy: dict[str, Any] | None = None,
    db_path: str | Path = DB_PATH,
) -> dict[str, Any]:
    get_trading_risk_state(owner_key, db_path=db_path)
    updates: list[str] = []
    values: list[Any] = []
    if kill_switch is not None:
        updates.append("kill_switch = ?")
        values.append(int(kill_switch))
    if daily_realized_pnl is not None:
        updates.append("daily_realized_pnl = ?")
        values.append(float(daily_realized_pnl))
    if increment_orders:
        updates.append("daily_order_count = daily_order_count + ?")
        values.append(int(increment_orders))
    allowed = {
        "max_daily_loss",
        "max_daily_orders",
        "max_order_notional",
        "max_single_position_pct",
    }
    for key, value in (policy or {}).items():
        if key in allowed:
            updates.append(f"{key} = ?")
            values.append(value)
    if updates:
        updates.append("updated_at = ?")
        values.append(datetime.now().isoformat(timespec="seconds"))
        values.append(owner_key)
        with db_connection(db_path) as conn:
            conn.execute(
                f"update trading_risk_state set {', '.join(updates)} where owner_key = ?",
                values,
            )
            conn.commit()
    return get_trading_risk_state(owner_key, db_path=db_path)


def save_broker_order(item: dict[str, Any], db_path: str | Path = DB_PATH) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    owner_key = str(item.get("owner_key") or "local")
    client_order_id = str(item["client_order_id"])
    with db_connection(db_path) as conn:
        conn.execute(
            """
            insert into broker_order_ledger (
                owner_key, client_order_id, broker, account_mask, symbol, side,
                quantity, limit_price, notional, mode, status, broker_order_id,
                strategy_name, risk_snapshot_json, error_message, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(owner_key, client_order_id) do update set
                status = excluded.status,
                broker_order_id = excluded.broker_order_id,
                risk_snapshot_json = excluded.risk_snapshot_json,
                error_message = excluded.error_message,
                updated_at = excluded.updated_at
            """,
            (
                owner_key,
                client_order_id,
                item.get("broker", "东吴QMT"),
                item.get("account_mask", ""),
                item["symbol"],
                item["side"],
                int(item["quantity"]),
                float(item["limit_price"]),
                float(item["notional"]),
                item.get("mode", "草稿"),
                item.get("status", "草稿"),
                item.get("broker_order_id"),
                item.get("strategy_name", "个人决策台"),
                _json(item.get("risk_snapshot", {})),
                item.get("error_message", ""),
                item.get("created_at", now),
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "select id from broker_order_ledger where owner_key = ? and client_order_id = ?",
            (owner_key, client_order_id),
        ).fetchone()
        return int(row["id"])


def load_broker_orders(
    owner_key: str = "local",
    limit: int = 100,
    db_path: str | Path = DB_PATH,
) -> pd.DataFrame:
    with db_connection(db_path) as conn:
        return pd.read_sql_query(
            """
            select * from broker_order_ledger
            where owner_key = ?
            order by updated_at desc, id desc
            limit ?
            """,
            conn,
            params=(owner_key, int(limit)),
        )


def save_trading_event(
    owner_key: str,
    event_type: str,
    message: str,
    severity: str = "提示",
    details: dict[str, Any] | None = None,
    db_path: str | Path = DB_PATH,
) -> int:
    with db_connection(db_path) as conn:
        cur = conn.execute(
            """
            insert into trading_runtime_events (
                owner_key, event_type, severity, message, details_json, created_at
            ) values (?, ?, ?, ?, ?, ?)
            """,
            (
                owner_key,
                event_type,
                severity,
                message,
                _json(details or {}),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def load_runs(limit: int = 20, db_path: str | Path = DB_PATH) -> pd.DataFrame:
    with db_connection(db_path) as conn:
        _mark_stale_running_runs_in_connection(conn)
        return pd.read_sql_query(
            "select * from scan_runs order by id desc limit ?",
            conn,
            params=(limit,),
        )


def load_capital_hotspots(
    run_id: int | None = None,
    db_path: str | Path = DB_PATH,
) -> pd.DataFrame:
    with db_connection(db_path) as conn:
        selected_run = run_id
        if selected_run is None:
            row = conn.execute(
                "select run_id from capital_hotspots order by id desc limit 1"
            ).fetchone()
            selected_run = int(row["run_id"]) if row else None
        if selected_run is None:
            return pd.DataFrame()
        return pd.read_sql_query(
            """
            select * from capital_hotspots
            where run_id = ?
            order by period, category, rank
            """,
            conn,
            params=(selected_run,),
        )


def load_capital_hotspot_history(
    category: str,
    name: str,
    limit: int = 120,
    db_path: str | Path = DB_PATH,
) -> pd.DataFrame:
    with db_connection(db_path) as conn:
        return pd.read_sql_query(
            """
            select *
            from capital_hotspots
            where category = ? and name = ?
            order by trade_date desc, id desc
            limit ?
            """,
            conn,
            params=(category, name, limit),
        )


def load_capital_hotspot_timeline(
    limit: int = 5000,
    db_path: str | Path = DB_PATH,
) -> pd.DataFrame:
    with db_connection(db_path) as conn:
        return pd.read_sql_query(
            """
            select *
            from capital_hotspots
            order by trade_date desc, id desc
            limit ?
            """,
            conn,
            params=(limit,),
        )


def load_latest_market_snapshot(
    db_path: str | Path = DB_PATH,
) -> tuple[dict[str, Any], pd.DataFrame, dict[str, Any]]:
    with db_connection(db_path) as conn:
        row = conn.execute("select * from market_snapshots order by id desc limit 1").fetchone()
    if not row:
        return {}, pd.DataFrame(), {}

    data = dict(row)
    breadth = {
        "total": data.get("total", 0),
        "up": data.get("up_count", 0),
        "down": data.get("down_count", 0),
        "flat": data.get("flat_count", 0),
        "limit_up": data.get("limit_up", 0),
        "limit_down": data.get("limit_down", 0),
        "strong": data.get("strong_count", 0),
        "weak": data.get("weak_count", 0),
        "turnover": data.get("turnover", 0),
        "median_change": data.get("median_change", 0),
        "up_ratio": data.get("up_ratio", 0),
        "adv_dec_ratio": data.get("adv_dec_ratio", 0),
        "temperature": data.get("temperature", 0),
        "temperature_label": data.get("temperature_label", ""),
    }
    try:
        global_indices = pd.DataFrame(json.loads(data.get("global_indices") or "[]"))
    except Exception:
        global_indices = pd.DataFrame()
    global_summary = {
        "score": data.get("global_score", 0),
        "label": data.get("global_label", ""),
    }
    return breadth, global_indices, global_summary
