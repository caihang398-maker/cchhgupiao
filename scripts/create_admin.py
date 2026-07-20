from __future__ import annotations

import argparse
import getpass
import os
import re
import sys

import bcrypt
import pymysql


MOBILE_PATTERN = re.compile(r"^\+?[0-9]{7,20}$")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="创建或重置股票系统管理员账号。")
    parser.add_argument("--host", default=env("DB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(env("DB_PORT", "3306")))
    parser.add_argument("--database", default=env("DB_NAME", "stock_quant_saas"))
    parser.add_argument("--db-user", default=env("DB_USER", "stock_quant_app"))
    parser.add_argument("--db-password", default=env("DB_PASSWORD"))
    parser.add_argument("--mobile", default=env("ADMIN_MOBILE"))
    parser.add_argument("--real-name", default=env("ADMIN_REAL_NAME", "系统管理员"))
    parser.add_argument("--login-name", default=env("ADMIN_LOGIN_NAME", "admin"))
    parser.add_argument("--password", default=env("ADMIN_PASSWORD"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.db_password:
        args.db_password = getpass.getpass("MySQL数据库密码：")
    if not args.mobile:
        args.mobile = input("管理员手机号：").strip()
    if not args.password:
        args.password = getpass.getpass("管理员登录密码：")

    if not MOBILE_PATTERN.fullmatch(args.mobile):
        print("手机号应为7至20位数字，可使用国际区号前缀+。", file=sys.stderr)
        return 2
    if len(args.password) < 12:
        print("管理员密码至少需要12位。", file=sys.stderr)
        return 2

    password_hash = bcrypt.hashpw(
        args.password.encode("utf-8"),
        bcrypt.gensalt(rounds=12),
    ).decode("ascii")

    connection = pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.db_user,
        password=args.db_password,
        database=args.database,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, login_name, mobile
                FROM sys_users
                WHERE mobile = %s OR login_name = %s
                FOR UPDATE
                """,
                (args.mobile, args.login_name),
            )
            matches = cursor.fetchall()
            if len(matches) > 1:
                raise ValueError(
                    "手机号和登录名分别属于不同账号，请先处理冲突后再重置管理员。"
                )

            if matches:
                user_id = matches[0]["id"]
                cursor.execute(
                    """
                    UPDATE sys_users
                    SET login_name = %s,
                        mobile = %s,
                        real_name = %s,
                        password_hash = %s,
                        status = 'active',
                        service_started_at = COALESCE(service_started_at, UTC_TIMESTAMP()),
                        service_expires_at = '2099-12-31 00:00:00',
                        password_changed_at = UTC_TIMESTAMP(),
                        failed_login_count = 0,
                        locked_until = NULL,
                        deleted_at = NULL
                    WHERE id = %s
                    """,
                    (
                        args.login_name,
                        args.mobile,
                        args.real_name,
                        password_hash,
                        user_id,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO sys_users (
                        login_name, mobile, real_name, password_hash, status,
                        service_started_at, service_expires_at, password_changed_at
                    )
                    VALUES (
                        %s, %s, %s, %s, 'active', UTC_TIMESTAMP(),
                        '2099-12-31 00:00:00', UTC_TIMESTAMP()
                    )
                    """,
                    (args.login_name, args.mobile, args.real_name, password_hash),
                )
                user_id = cursor.lastrowid

            cursor.execute(
                """
                INSERT IGNORE INTO sys_user_roles (user_id, role_id, granted_by)
                SELECT %s, id, %s
                FROM sys_roles
                WHERE role_code = 'ADMIN'
                """,
                (user_id, user_id),
            )
            cursor.execute(
                """
                INSERT INTO audit_logs (
                    operator_user_id, action_code, target_type, target_id, after_json
                )
                VALUES (%s, 'admin.bootstrap', 'user', %s, JSON_OBJECT('mobile', %s, 'login_name', %s))
                """,
                (user_id, str(user_id), args.mobile, args.login_name),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    print(f"管理员账号已就绪：{args.login_name} / {args.mobile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
