from __future__ import annotations

import logging
import os

import streamlit as st

from stock_quant.auth import auth_enabled
from stock_quant.logging_config import configure_application_logging
from stock_quant.presentation import format_utc_datetime


configure_application_logging()
LOGGER = logging.getLogger(__name__)


def login_service_message(exc: Exception) -> str:
    detail = str(exc)
    lowered = detail.lower()
    if "can't connect to mysql" in lowered or "connectionrefused" in lowered or "10061" in lowered:
        host = os.getenv("DB_HOST", "127.0.0.1")
        port = os.getenv("DB_PORT", "3306")
        return f"登录服务暂不可用：无法连接会员数据库 {host}:{port}，请检查 MySQL 是否启动。"
    if "access denied" in lowered:
        return "登录服务暂不可用：数据库账号或密码不正确，请检查服务器环境变量。"
    if "unknown database" in lowered:
        return "登录服务暂不可用：会员数据库不存在或尚未导入初始化脚本。"
    return "登录服务暂不可用，请稍后重试或联系管理员。"


def inject_mobile_css() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stMetricValue"] {
          white-space: normal !important;
          overflow-wrap: anywhere !important;
          line-height: 1.2 !important;
        }
        div[data-testid="stDataFrame"] {
          max-width: 100% !important;
          overflow-x: auto !important;
        }
        @media (max-width: 760px) {
          .block-container {
            padding-left: 1rem !important;
            padding-right: 1rem !important;
            padding-top: 1rem !important;
          }
          h1 {
            font-size: 2rem !important;
            line-height: 1.18 !important;
          }
          h2 {
            font-size: 1.45rem !important;
          }
          [data-testid="stSidebar"] {
            min-width: 15.5rem !important;
          }
          [data-testid="stHorizontalBlock"] {
            gap: 0.6rem !important;
          }
          [data-testid="stMetricValue"] {
            font-size: 1.35rem !important;
          }
          div[data-testid="stDataFrame"] {
            overflow-x: auto !important;
          }
          .stButton > button {
            min-height: 2.75rem;
          }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_login() -> None:
    st.set_page_config(page_title="登录 - A股每日量化推荐", layout="centered")
    inject_mobile_css()
    st.title("A股每日量化推荐")
    st.caption("请输入管理员登录名或会员手机号。")
    with st.form("login_form"):
        identifier = st.text_input("手机号或登录名")
        password = st.text_input("密码", type="password")
        submitted = st.form_submit_button("登录", type="primary", width="stretch")
    if submitted:
        try:
            from stock_quant.auth import authenticate

            st.session_state["auth_user"] = authenticate(identifier, password)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
        except Exception as exc:
            LOGGER.exception("登录服务异常")
            st.error(login_service_message(exc))
            if os.getenv("APP_ENV", "").lower() in {"local", "dev", "development"}:
                st.caption("本地开发时如不需要会员登录，可临时设置 AUTH_ENABLED=false 后重新启动。")


auth_required = auth_enabled()
user = st.session_state.get("auth_user")
if auth_required:
    if user:
        try:
            from stock_quant.auth import refresh_user

            user = refresh_user(int(user["id"]))
            if user:
                st.session_state["auth_user"] = user
            else:
                st.session_state.pop("auth_user", None)
        except Exception:
            LOGGER.exception("账号状态检查异常")
            st.error("账号状态检查暂不可用，请稍后重试。")
            st.stop()
    if not user:
        st.navigation(
            [
                st.Page(
                    render_login,
                    title="登录",
                    url_path="",
                    default=True,
                ),
                st.Page(render_login, title="登录", url_path="methodology"),
                st.Page(render_login, title="登录", url_path="risk-disclosure"),
                st.Page(render_login, title="登录", url_path="recommendation-review"),
                st.Page(render_login, title="登录", url_path="admin-accounts"),
            ],
            position="hidden",
        ).run()
        st.stop()

pages = [
    st.Page(
        "dashboard.py",
        title="量化推荐",
        url_path="",
        default=True,
    ),
    st.Page(
        "pages/methodology.py",
        title="名词与方法",
        url_path="methodology",
    ),
    st.Page(
        "pages/risk_disclosure.py",
        title="风险与合规",
        url_path="risk-disclosure",
    ),
    st.Page(
        "pages/recommendation_review.py",
        title="推荐复盘",
        url_path="recommendation-review",
    ),
]
if user and user.get("is_admin"):
    pages.append(
        st.Page(
            "pages/admin_accounts.py",
            title="账号管理",
            url_path="admin-accounts",
        )
    )
personal_trading_enabled = os.getenv("ENABLE_PERSONAL_TRADING", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
if personal_trading_enabled and ((user and user.get("is_admin")) or not auth_required):
    pages.append(
        st.Page(
            "pages/personal_trading.py",
            title="个人实盘决策台",
            url_path="personal-trading",
        )
    )

navigation = st.navigation(pages, position="sidebar")
inject_mobile_css()
if user:
    with st.sidebar:
        st.caption(f"{user['real_name']} · {user['mobile']}")
        st.caption(f"到期：{format_utc_datetime(user['service_expires_at'])}")
        if st.button("退出登录", width="stretch"):
            for key in list(st.session_state):
                if key.startswith("position_input_") or key in {
                    "position_defaults_owner",
                    "stock_report",
                    "stock_history",
                    "position_plan",
                    "position_news_errors",
                }:
                    st.session_state.pop(key, None)
            st.session_state.pop("auth_user", None)
            st.rerun()
navigation.run()
