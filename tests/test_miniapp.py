from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pandas as pd

from stock_quant.miniapp_api import LOGIN_MAX_ATTEMPTS, LOGIN_LIMITER, LoginLimiter, MiniappApiHandler
from stock_quant.miniapp_auth import (
    ACCESS_PURPOSE,
    MiniappAuthError,
    MiniappConfigurationError,
    MiniappEntitlementError,
    MiniappTokenError,
    issue_token,
    login_with_account,
    login_with_cloudbase_wechat,
    login_with_wechat,
    require_active_service,
    validate_api_startup,
    verify_token,
    wechat_configured,
)
from stock_quant.miniapp_membership import subscription_summary
from stock_quant.miniapp_service import _recommendation_record, json_safe
from stock_quant.miniapp_service import (
    create_alert,
    create_quick_alerts,
    generate_paper_trades,
    list_alerts,
    screen_recommendations,
)
from stock_quant.product import evaluate_alert_rules


TOKEN_ENV = {
    "APP_ENV": "test",
    "MINIAPP_ALLOW_LOCAL_DEV": "true",
    "MINIAPP_TOKEN_SECRET": "miniapp-test-secret-with-at-least-32-chars",
}


class MiniappAuthTests(unittest.TestCase):
    def test_token_round_trip_and_tamper_detection(self) -> None:
        with patch.dict(os.environ, TOKEN_ENV, clear=False):
            token = issue_token({"sub": 7}, ACCESS_PURPOSE, 3600)
            self.assertEqual(verify_token(token, ACCESS_PURPOSE)["sub"], 7)
            encoded, signature = token.split(".", 1)
            replacement = "A" if signature[0] != "A" else "B"
            with self.assertRaises(MiniappTokenError):
                verify_token(f"{encoded}.{replacement}{signature[1:]}", ACCESS_PURPOSE)

    def test_expired_and_malformed_expiry_are_rejected_cleanly(self) -> None:
        with patch.dict(os.environ, TOKEN_ENV, clear=False):
            with patch("stock_quant.miniapp_auth.time.time", return_value=100):
                token = issue_token({"sub": 7}, ACCESS_PURPOSE, 60)
            with patch("stock_quant.miniapp_auth.time.time", return_value=161):
                with self.assertRaisesRegex(MiniappTokenError, "已过期"):
                    verify_token(token, ACCESS_PURPOSE)

            claims = {"sub": 7, "purpose": ACCESS_PURPOSE, "exp": "不是时间"}
            encoded = base64.urlsafe_b64encode(
                json.dumps(claims, ensure_ascii=False).encode("utf-8")
            ).rstrip(b"=").decode("ascii")
            signature = hmac.new(
                TOKEN_ENV["MINIAPP_TOKEN_SECRET"].encode("utf-8"),
                encoded.encode("ascii"),
                hashlib.sha256,
            ).digest()
            signed = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
            with self.assertRaisesRegex(MiniappTokenError, "有效期无效"):
                verify_token(f"{encoded}.{signed}", ACCESS_PURPOSE)

    def test_expired_member_can_login_but_premium_access_is_blocked(self) -> None:
        expired = {
            "id": 9,
            "login_name": "member",
            "roles": ["MEMBER"],
            "is_admin": False,
            "service_status": "expired",
            "service_valid": False,
        }
        with (
            patch.dict(os.environ, TOKEN_ENV, clear=False),
            patch("stock_quant.miniapp_auth.auth_enabled", return_value=True),
            patch("stock_quant.miniapp_auth.authenticate", return_value=expired),
        ):
            session = login_with_account("member", "password")
            self.assertTrue(session["token"])
            with self.assertRaisesRegex(MiniappEntitlementError, "已到期"):
                require_active_service(expired)

    def test_wechat_first_login_can_create_trial_user(self) -> None:
        created = {
            "id": 12,
            "roles": ["MEMBER"],
            "is_admin": False,
            "service_status": "active",
            "service_valid": True,
        }
        with (
            patch.dict(os.environ, TOKEN_ENV, clear=False),
            patch("stock_quant.miniapp_auth.auth_enabled", return_value=True),
            patch(
                "stock_quant.miniapp_auth.exchange_wechat_code",
                return_value={"app_id": "wx-test", "open_id": "openid", "union_id": ""},
            ),
            patch("stock_quant.miniapp_auth._find_bound_user", return_value=None),
            patch("stock_quant.miniapp_auth.wechat_auto_register_enabled", return_value=True),
            patch("stock_quant.miniapp_auth._create_wechat_user", return_value=created),
        ):
            result = login_with_wechat("temporary-code")
        self.assertEqual(result["status"], "authenticated")
        self.assertTrue(result["is_new_user"])
        self.assertEqual(result["user"]["id"], 12)

    def test_cloudbase_identity_login_reuses_wechat_account_flow(self) -> None:
        existing = {
            "id": 18,
            "roles": ["MEMBER"],
            "is_admin": False,
            "service_status": "active",
            "service_valid": True,
        }
        environment = {
            **TOKEN_ENV,
            "AUTH_ENABLED": "true",
            "MINIAPP_TRUST_CLOUDBASE_IDENTITY": "true",
            "WECHAT_MINIAPP_APP_ID": "wx-test",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("stock_quant.miniapp_auth.auth_enabled", return_value=True),
            patch("stock_quant.miniapp_auth._find_bound_user", return_value=existing),
        ):
            result = login_with_cloudbase_wechat("wx-test", "openid-test", "union-test")
        self.assertEqual(result["status"], "authenticated")
        self.assertEqual(result["user"]["id"], 18)

    def test_cloudbase_identity_rejects_wrong_app_id(self) -> None:
        with patch.dict(
            os.environ,
            {
                **TOKEN_ENV,
                "MINIAPP_TRUST_CLOUDBASE_IDENTITY": "true",
                "WECHAT_MINIAPP_APP_ID": "wx-expected",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(MiniappAuthError, "AppID"):
                login_with_cloudbase_wechat("wx-other", "openid-test")

    def test_cloudbase_mode_does_not_require_app_secret(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MINIAPP_TRUST_CLOUDBASE_IDENTITY": "true",
                "WECHAT_MINIAPP_APP_ID": "wx-test",
            },
            clear=True,
        ):
            self.assertTrue(wechat_configured())

    def test_required_cloud_storage_rejects_unmounted_directory(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                **TOKEN_ENV,
                "AUTH_ENABLED": "true",
                "MINIAPP_REQUIRE_PERSISTENT_STORAGE": "true",
            },
            clear=True,
        ), patch("stock_quant.miniapp_auth.DATA_DIR", Path(directory)), patch(
            "stock_quant.miniapp_auth.auth_enabled",
            return_value=True,
        ):
            with self.assertRaisesRegex(MiniappConfigurationError, "尚未挂载"):
                validate_api_startup("0.0.0.0")

    def test_cloudbase_api_uses_injected_identity_headers(self) -> None:
        handler = object.__new__(MiniappApiHandler)
        handler.headers = {
            "X-WX-APPID": "wx-test",
            "X-WX-OPENID": "openid-test",
            "X-WX-UNIONID": "union-test",
        }
        handler.client_address = ("127.0.0.1", 10000)
        handler._body = Mock()
        handler._ok = Mock()
        cloud_result = {"status": "authenticated", "token": "token"}
        with (
            patch("stock_quant.miniapp_api.cloudbase_identity_enabled", return_value=True),
            patch.object(LOGIN_LIMITER, "allow", return_value=True),
            patch(
                "stock_quant.miniapp_api.login_with_cloudbase_wechat",
                return_value=cloud_result,
            ) as login_mock,
        ):
            handler._wechat_login()
        login_mock.assert_called_once_with("wx-test", "openid-test", "union-test")
        handler._body.assert_not_called()
        handler._ok.assert_called_once_with(cloud_result)

    def test_production_rejects_weak_or_disabled_auth_configuration(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "AUTH_ENABLED": "false",
                "MINIAPP_TOKEN_SECRET": "miniapp-test-secret-with-at-least-32-chars",
            },
            clear=False,
        ):
            with patch("stock_quant.miniapp_auth.auth_enabled", return_value=False):
                with self.assertRaises(MiniappConfigurationError):
                    validate_api_startup("127.0.0.1")


class MiniappServiceTests(unittest.TestCase):
    def test_subscription_summary_has_four_periods_and_personal_payment_checklist(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    **TOKEN_ENV,
                    "AUTH_ENABLED": "false",
                    "WECHAT_MINIAPP_SUBJECT_TYPE": "individual",
                    "MINIAPP_PAYMENT_MODE": "disabled",
                },
                clear=False,
            ),
            patch("stock_quant.miniapp_membership.auth_enabled", return_value=False),
        ):
            result = subscription_summary(
                {
                    "id": 0,
                    "real_name": "本机调试用户",
                    "roles": ["ADMIN"],
                    "is_admin": True,
                    "service_status": "active",
                    "service_valid": True,
                }
            )
        self.assertEqual(
            [item["duration_months"] for item in result["plans"][:4]],
            [1, 3, 6, 12],
        )
        self.assertFalse(result["payment"]["enabled"])
        self.assertFalse(result["payment"]["eligible"])
        self.assertEqual(result["payment"]["state"], "eligibility_pending")
        self.assertEqual(len(result["payment"]["checklist"]), 4)

    def test_personal_virtual_payment_can_reach_integration_ready_state(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "WECHAT_MINIAPP_SUBJECT_TYPE": "individual",
                    "WECHAT_MINIAPP_CATEGORY_TOOLS": "true",
                    "WECHAT_MINIAPP_VERIFIED": "true",
                    "WECHAT_MINIAPP_ICP_FILED": "true",
                    "MINIAPP_PAYMENT_MODE": "wechat_virtual",
                    "WECHAT_MINIAPP_APP_ID": "wx-test",
                    "WECHAT_VIRTUAL_PAY_OFFER_ID": "offer-test",
                    "WECHAT_VIRTUAL_PAY_APP_KEY": "secret-test",
                    "MINIAPP_PAYMENT_LIVE_ENABLED": "false",
                },
                clear=False,
            ),
            patch("stock_quant.miniapp_membership.auth_enabled", return_value=False),
        ):
            result = subscription_summary(
                {
                    "id": 8,
                    "real_name": "微信用户",
                    "roles": ["MEMBER"],
                    "is_admin": False,
                    "service_status": "active",
                    "service_valid": True,
                }
            )
        self.assertFalse(result["payment"]["enabled"])
        self.assertTrue(result["payment"]["eligible"])
        self.assertEqual(result["payment"]["state"], "ready_for_integration")

    def test_json_safe_normalizes_pandas_and_non_finite_values(self) -> None:
        payload = {
            "date": date(2026, 9, 6),
            "number": pd.NA,
            "nested": [float("inf"), pd.Timestamp("2026-09-05")],
        }
        result = json_safe(payload)
        self.assertEqual(result["date"], "2026-09-06")
        self.assertIsNone(result["number"])
        self.assertIsNone(result["nested"][0])
        self.assertEqual(result["nested"][1], "2026-09-05T00:00:00")

    def test_recommendation_record_uses_chinese_labels_and_safe_defaults(self) -> None:
        result = _recommendation_record(
            {
                "symbol": "sh600000",
                "name": "浦发银行",
                "horizon": "short",
                "score": 88.6,
                "topics": '["银行", "高股息"]',
                "reasons": None,
            }
        )
        self.assertEqual(result["symbol"], "SH600000")
        self.assertEqual(result["horizon_label"], "短期")
        self.assertEqual(result["topics"], ["银行", "高股息"])
        self.assertEqual(result["credibility"]["label"], "样本积累")

    def test_mobile_screener_reuses_existing_condition_engine(self) -> None:
        recommendations = pd.DataFrame(
            [
                {
                    "id": 1,
                    "trade_date": "2026-09-05",
                    "symbol": "SH600000",
                    "name": "浦发银行",
                    "horizon": "short",
                    "priority": "重点",
                    "score": 82,
                    "base_score": 80,
                    "position_pct": 0.2,
                    "close": 10.0,
                    "buy_zone_low": 9.8,
                    "buy_zone_high": 10.2,
                    "credibility_score": 75,
                },
                {
                    "id": 2,
                    "trade_date": "2026-09-05",
                    "symbol": "SZ000001",
                    "name": "平安银行",
                    "horizon": "mid",
                    "priority": "观察",
                    "score": 68,
                    "base_score": 67,
                    "position_pct": 0.3,
                    "close": 12.0,
                    "buy_zone_low": 10.0,
                    "buy_zone_high": 10.5,
                    "credibility_score": 55,
                },
            ]
        )
        with (
            patch(
                "stock_quant.miniapp_service.latest_recommendation_context",
                return_value=({"trade_date": "2026-09-05"}, recommendations),
            ),
            patch("stock_quant.miniapp_service._record_activity"),
        ):
            result = screen_recommendations(
                {"id": 0},
                {
                    "conditions": ["技术评分较高", "处于买点附近", "市场主线方向"],
                    "min_score": 70,
                    "max_position": 0.35,
                },
            )
        self.assertEqual(result["pool_count"], 2)
        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["items"][0]["symbol"], "SH600000")

        with (
            patch(
                "stock_quant.miniapp_service.latest_recommendation_context",
                return_value=({"trade_date": "2026-09-05"}, recommendations),
            ),
            patch("stock_quant.miniapp_service._record_activity"),
        ):
            price_result = screen_recommendations(
                {"id": 0},
                {
                    "conditions": [],
                    "min_score": 50,
                    "max_position": 0.35,
                    "min_price": 11,
                    "max_price": 20,
                    "random_pick": True,
                    "random_count": 1,
                },
            )
        self.assertEqual(price_result["result_count"], 1)
        self.assertEqual(price_result["items"][0]["symbol"], "SZ000001")
        self.assertTrue(price_result["random_pick"])

    def test_paper_generation_respects_available_cash(self) -> None:
        recommendations = pd.DataFrame(
            [
                {
                    "id": 11,
                    "trade_date": "2026-09-05",
                    "symbol": "SH600000",
                    "name": "浦发银行",
                    "horizon": "short",
                    "strategy_type": "主线趋势股",
                    "score": 82,
                    "buy_zone_high": 10.0,
                    "close": 10.0,
                }
            ]
        )
        save_mock = Mock()
        with (
            patch(
                "stock_quant.miniapp_service.get_or_create_paper_account",
                return_value={"id": 3, "initial_cash": 20_000.0, "cash": 15_000.0},
            ),
            patch(
                "stock_quant.miniapp_service.latest_recommendation_context",
                return_value=({"id": 1}, recommendations),
            ),
            patch("stock_quant.miniapp_service.load_recommendation_outcomes", return_value=pd.DataFrame()),
            patch("stock_quant.miniapp_service.load_paper_trades", return_value=pd.DataFrame()),
            patch("stock_quant.miniapp_service.save_paper_trades", save_mock),
            patch("stock_quant.miniapp_service._record_activity"),
            patch(
                "stock_quant.miniapp_service.get_paper_account",
                return_value={"account": {}, "summary": {}, "equity_curve": [], "trades": []},
            ),
        ):
            result = generate_paper_trades(
                {"id": 0},
                "local",
                {"initial_cash": 20_000, "cash_per_trade": 10_000, "max_count": 5},
            )
        saved = save_mock.call_args.args[0]
        self.assertEqual(result["created"], 1)
        self.assertEqual(len(saved), 1)
        self.assertLessEqual(saved[0]["amount"] + saved[0]["fee"], 15_000)

    def test_quick_alerts_are_idempotent_for_existing_rules(self) -> None:
        recommendations = pd.DataFrame(
            [
                {
                    "symbol": "SH600000",
                    "name": "浦发银行",
                    "score": 82,
                    "buy_zone_high": 10.0,
                    "stop_loss": 9.0,
                }
            ]
        )
        existing = pd.DataFrame(
            [
                {"symbol": "SH600000", "alert_type": "到达买点"},
                {"symbol": "SH600000", "alert_type": "跌破止损"},
            ]
        )
        save_mock = Mock(return_value=1)
        with (
            patch("stock_quant.miniapp_service.load_alert_rules", return_value=existing),
            patch(
                "stock_quant.miniapp_service.latest_recommendation_context",
                return_value=({"id": 1}, recommendations),
            ),
            patch("stock_quant.miniapp_service.save_alert_rule", save_mock),
            patch("stock_quant.miniapp_service._record_activity"),
        ):
            result = create_quick_alerts(
                {"id": 0}, "local", {"scope": "recommendations", "limit": 5}
            )
        self.assertEqual(result["created"], 0)
        save_mock.assert_not_called()

    def test_alert_listing_and_manual_create_collapse_duplicates(self) -> None:
        existing = pd.DataFrame(
            [
                {"id": 2, "symbol": "SH600000", "alert_type": "到达买点", "enabled": 1},
                {"id": 1, "symbol": "SH600000", "alert_type": "到达买点", "enabled": 1},
            ]
        )
        with (
            patch("stock_quant.miniapp_service.load_alert_rules", return_value=existing),
            patch("stock_quant.miniapp_service.load_alert_events", return_value=pd.DataFrame()),
            patch("stock_quant.miniapp_service._record_activity"),
        ):
            listed = list_alerts({"id": 0}, "local")
        self.assertEqual(len(listed["rules"]), 1)
        self.assertEqual(listed["rules"][0]["id"], 2)

        save_mock = Mock()
        with (
            patch("stock_quant.miniapp_service.load_alert_rules", return_value=existing),
            patch("stock_quant.miniapp_service.resolve_stock", return_value=("SH600000", "浦发银行")),
            patch("stock_quant.miniapp_service.save_alert_rule", save_mock),
            patch("stock_quant.miniapp_service._record_activity"),
        ):
            duplicate = create_alert(
                {"id": 0},
                "local",
                {"alert_type": "到达买点", "symbol": "600000"},
            )
        self.assertFalse(duplicate["created"])
        self.assertEqual(duplicate["id"], 2)
        save_mock.assert_not_called()

    def test_manual_stop_alert_uses_threshold_without_recommendation(self) -> None:
        rules = pd.DataFrame(
            [
                {
                    "id": 1,
                    "symbol": "SH600000",
                    "name": "浦发银行",
                    "alert_type": "跌破止损",
                    "comparator": "<=",
                    "threshold_value": 9.0,
                }
            ]
        )
        spot = pd.DataFrame([{"code": "600000", "price": 8.8}])
        events = evaluate_alert_rules(
            rules,
            spot,
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            owner_key="local",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["severity"], "风险")
        self.assertIn("9.00", events[0]["message"])


class MiniappApiTests(unittest.TestCase):
    def test_login_limiter_blocks_after_configured_attempts(self) -> None:
        limiter = LoginLimiter()
        with patch("stock_quant.miniapp_api.time.monotonic", return_value=time.monotonic()):
            for _ in range(LOGIN_MAX_ATTEMPTS):
                self.assertTrue(limiter.allow("127.0.0.1"))
            self.assertFalse(limiter.allow("127.0.0.1"))


if __name__ == "__main__":
    unittest.main()
