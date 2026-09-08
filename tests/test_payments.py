from __future__ import annotations

import hashlib
import hmac
import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from stock_quant.auth import _service_status
from stock_quant.health import (
    MYSQL_REQUIRED_COLUMNS,
    PAYMENT_MYSQL_REQUIRED_COLUMNS,
    mysql_schema_findings,
)
from stock_quant.payments import (
    CREEM_EVENT_TYPES,
    CreemConfig,
    PaymentConfigurationError,
    PaymentSignatureError,
    PaymentValidationError,
    _dispatch_creem_event,
    _event_object,
    _handle_subscription_paid,
    _metadata,
    _minor_to_amount,
    _product_details,
    _success_url_for_order,
    _validate_existing_payment_transaction,
    load_creem_config,
    parse_creem_datetime,
    process_creem_webhook,
    verify_creem_signature,
)


class PaymentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = CreemConfig(
            environment="test",
            base_url="https://test-api.creem.io/v1",
            api_key="test_key",
            webhook_secret="test_secret",
            success_url="http://127.0.0.1:8501/subscription",
            live_enabled=False,
        )

    def test_signature_accepts_plain_and_prefixed_digest(self) -> None:
        payload = b'{"id":"evt_test"}'
        digest = hmac.new(b"secret", payload, hashlib.sha256).hexdigest()
        self.assertTrue(verify_creem_signature(payload, digest, "secret"))
        self.assertTrue(verify_creem_signature(payload, f"sha256={digest}", "secret"))
        self.assertFalse(verify_creem_signature(payload, "bad", "secret"))
        self.assertFalse(verify_creem_signature(payload, digest, ""))

    def test_creem_datetime_is_normalized_to_naive_utc(self) -> None:
        parsed = parse_creem_datetime("2026-07-29T16:30:00+08:00")
        self.assertEqual(parsed, datetime(2026, 7, 29, 8, 30, 0))
        aware = datetime(2026, 7, 29, 8, 30, tzinfo=timezone.utc)
        self.assertEqual(parse_creem_datetime(aware), datetime(2026, 7, 29, 8, 30))
        self.assertIsNone(parse_creem_datetime("not-a-date"))

    def test_success_url_preserves_existing_query_and_adds_order(self) -> None:
        url = _success_url_for_order(
            "http://127.0.0.1:8501/subscription?source=member",
            "CR20260729TEST",
        )
        self.assertIn("source=member", url)
        self.assertIn("checkout=success", url)
        self.assertIn("order=CR20260729TEST", url)

    def test_test_config_and_live_safety_guard(self) -> None:
        test_env = {
            "CREEM_MODE": "test",
            "CREEM_TEST_API_KEY": "key_test",
            "CREEM_TEST_WEBHOOK_SECRET": "secret_test",
            "CREEM_SUCCESS_URL": "http://127.0.0.1:8501/subscription",
        }
        with patch.dict(os.environ, test_env, clear=False):
            config = load_creem_config(
                require_api_key=True,
                require_webhook_secret=True,
            )
        self.assertEqual(config.environment, "test")
        self.assertEqual(config.base_url, "https://test-api.creem.io/v1")

        live_env = {
            "CREEM_MODE": "live",
            "CREEM_LIVE_ENABLED": "false",
            "CREEM_SUCCESS_URL": "https://example.com/subscription",
        }
        with patch.dict(os.environ, live_env, clear=False):
            with self.assertRaises(PaymentConfigurationError):
                load_creem_config()

    def test_live_config_rejects_insecure_success_url(self) -> None:
        live_env = {
            "CREEM_MODE": "live",
            "CREEM_LIVE_ENABLED": "true",
            "CREEM_SUCCESS_URL": "http://127.0.0.1:8501/subscription",
        }
        with patch.dict(os.environ, live_env, clear=False):
            with self.assertRaises(PaymentConfigurationError):
                load_creem_config()

    def test_expired_account_can_login_for_renewal_but_is_not_valid(self) -> None:
        now = datetime(2026, 7, 29, 12, 0, 0)
        user = {
            "status": "active",
            "deleted_at": None,
            "service_started_at": datetime(2026, 6, 1),
            "service_expires_at": datetime(2026, 7, 1),
        }
        self.assertEqual(_service_status(user, now), "expired")
        user["service_expires_at"] = datetime(2026, 8, 1)
        self.assertEqual(_service_status(user, now), "active")

    def test_only_paid_event_grants_service(self) -> None:
        paid_event = {
            "object": {
                "object": "subscription",
                "id": "sub_test",
            }
        }
        with (
            patch("stock_quant.payments._handle_subscription_paid") as paid_handler,
            patch("stock_quant.payments._sync_subscription") as sync_handler,
        ):
            handled = _dispatch_creem_event(
                "subscription.paid",
                paid_event,
                self.config,
            )
        self.assertTrue(handled)
        paid_handler.assert_called_once()
        sync_handler.assert_not_called()

        with (
            patch("stock_quant.payments._handle_subscription_paid") as paid_handler,
            patch("stock_quant.payments._sync_subscription") as sync_handler,
        ):
            handled = _dispatch_creem_event(
                "subscription.active",
                paid_event,
                self.config,
            )
        self.assertTrue(handled)
        paid_handler.assert_not_called()
        sync_handler.assert_called_once()

    def test_paid_event_requires_amount_and_currency_before_database_access(self) -> None:
        paid_object = {
            "object": "subscription",
            "id": "sub_test",
            "last_transaction_id": "tran_test",
            "product": {"id": "prod_test"},
            "current_period_start_date": "2026-07-29T00:00:00Z",
            "current_period_end_date": "2026-08-29T00:00:00Z",
        }
        with patch("stock_quant.payments._connect") as connect:
            with self.assertRaises(PaymentValidationError):
                _handle_subscription_paid(paid_object, self.config)
        connect.assert_not_called()

    def test_documented_paid_payload_shape_is_extracted(self) -> None:
        event = {
            "id": "evt_test",
            "eventType": "subscription.paid",
            "object": {
                "object": "subscription",
                "id": "sub_test",
                "product": {
                    "id": "prod_test",
                    "price": 990,
                    "currency": "CNY",
                },
                "metadata": {
                    "order_no": "CR20260729TEST",
                    "user_id": "12",
                },
            },
        }
        obj = _event_object(event)
        self.assertEqual(_product_details(obj), ("prod_test", 990, "CNY"))
        self.assertEqual(_metadata(obj)["order_no"], "CR20260729TEST")

    def test_existing_transaction_must_match_member_plan_and_amount(self) -> None:
        transaction = {
            "user_id": 12,
            "plan_id": 3,
            "amount": "9.90",
            "currency": "CNY",
            "channel": "Creem测试支付",
            "status": "success",
        }
        _validate_existing_payment_transaction(
            transaction,
            user_id=12,
            plan_id=3,
            expected_amount=_minor_to_amount(990),
            expected_currency="CNY",
            expected_channel="Creem测试支付",
        )
        for field, bad_value in (
            ("user_id", 99),
            ("plan_id", 7),
            ("amount", "0.01"),
            ("currency", "USD"),
            ("channel", "其他渠道"),
            ("status", "pending"),
        ):
            altered = dict(transaction)
            altered[field] = bad_value
            with self.subTest(field=field):
                with self.assertRaises(PaymentValidationError):
                    _validate_existing_payment_transaction(
                        altered,
                        user_id=12,
                        plan_id=3,
                        expected_amount=_minor_to_amount(990),
                        expected_currency="CNY",
                        expected_channel="Creem测试支付",
                    )

    def test_all_documented_subscription_states_are_recognized(self) -> None:
        expected = {
            "subscription.active",
            "subscription.paid",
            "subscription.canceled",
            "subscription.scheduled_cancel",
            "subscription.past_due",
            "subscription.expired",
            "subscription.trialing",
            "subscription.paused",
        }
        self.assertTrue(expected.issubset(CREEM_EVENT_TYPES))

    def test_webhook_rejects_invalid_signature_before_database_access(self) -> None:
        payload = b'{"id":"evt_bad","eventType":"subscription.paid"}'
        with (
            patch("stock_quant.payments.load_creem_config", return_value=self.config),
            patch("stock_quant.payments._acquire_webhook_event") as acquire,
        ):
            with self.assertRaises(PaymentSignatureError):
                process_creem_webhook(payload, "invalid")
        acquire.assert_not_called()

    def test_unknown_webhook_is_recorded_and_ignored(self) -> None:
        event = {"id": "evt_unknown", "eventType": "future.event", "object": {}}
        payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(
            self.config.webhook_secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()
        with (
            patch("stock_quant.payments.load_creem_config", return_value=self.config),
            patch("stock_quant.payments._acquire_webhook_event", return_value="acquired"),
            patch("stock_quant.payments._finish_webhook_event") as finish,
            patch("stock_quant.payments.LOGGER.exception"),
        ):
            result = process_creem_webhook(payload, signature)
        self.assertEqual(result.status, "ignored")
        finish.assert_called_once_with(self.config, "evt_unknown", "ignored", "")

    def test_duplicate_webhook_does_not_dispatch_twice(self) -> None:
        event = {"id": "evt_duplicate", "eventType": "subscription.paid", "object": {}}
        payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(
            self.config.webhook_secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()
        with (
            patch("stock_quant.payments.load_creem_config", return_value=self.config),
            patch("stock_quant.payments._acquire_webhook_event", return_value="processed"),
            patch("stock_quant.payments._dispatch_creem_event") as dispatch,
            patch("stock_quant.payments._finish_webhook_event") as finish,
        ):
            result = process_creem_webhook(payload, signature)
        self.assertEqual(result.status, "duplicate")
        dispatch.assert_not_called()
        finish.assert_not_called()

    def test_failed_known_event_is_marked_failed(self) -> None:
        event = {"id": "evt_invalid", "eventType": "subscription.active"}
        payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(
            self.config.webhook_secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()
        with (
            patch("stock_quant.payments.load_creem_config", return_value=self.config),
            patch("stock_quant.payments._acquire_webhook_event", return_value="acquired"),
            patch("stock_quant.payments._finish_webhook_event") as finish,
            patch("stock_quant.payments.LOGGER.exception"),
        ):
            with self.assertRaises(PaymentValidationError):
                process_creem_webhook(payload, signature)
        finish.assert_called_once()
        self.assertEqual(finish.call_args.args[2], "failed")

    def test_payment_schema_contract_matches_schema_and_migration(self) -> None:
        root = Path(__file__).resolve().parents[1]
        schema = (root / "database" / "mysql57_schema.sql").read_text(encoding="utf-8")
        migration = (
            root / "database" / "migrations" / "20260729_creem_subscriptions.sql"
        ).read_text(encoding="utf-8")
        for table, columns in PAYMENT_MYSQL_REQUIRED_COLUMNS.items():
            self.assertIn(f"CREATE TABLE IF NOT EXISTS `{table}`", schema)
            self.assertIn(f"CREATE TABLE IF NOT EXISTS `{table}`", migration)
            for column in columns:
                self.assertIn(f"`{column}`", schema)
                self.assertIn(f"`{column}`", migration)

        tables = set(MYSQL_REQUIRED_COLUMNS) | set(PAYMENT_MYSQL_REQUIRED_COLUMNS)
        columns_by_table = {
            table: set(columns)
            for table, columns in {
                **MYSQL_REQUIRED_COLUMNS,
                **PAYMENT_MYSQL_REQUIRED_COLUMNS,
            }.items()
        }
        self.assertEqual(
            mysql_schema_findings(
                tables,
                set(),
                columns_by_table,
                include_payment=True,
            )[0],
            "MySQL缺少视图：v_user_account_status",
        )
        self.assertIn("`uk_payment_transactions_no` (`transaction_no`)", schema)
        self.assertIn("`uk_payment_webhook_event`", schema)


if __name__ == "__main__":
    unittest.main()
