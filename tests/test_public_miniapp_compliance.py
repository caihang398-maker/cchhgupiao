from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from scripts.check_public_miniapp_compliance import collect_violations
from stock_quant.miniapp_api import _personal_records_route_allowed
from stock_quant.miniapp_auth import public_user
from stock_quant.miniapp_membership import subscription_summary


class PublicMiniappComplianceTests(unittest.TestCase):
    def test_public_source_has_no_research_or_dealing_features(self) -> None:
        self.assertEqual(collect_violations(), [])

    def test_personal_records_service_exposes_only_public_routes(self) -> None:
        self.assertTrue(_personal_records_route_allowed("POST", "/api/v1/auth/wechat"))
        self.assertTrue(_personal_records_route_allowed("GET", "/api/v1/me"))
        self.assertFalse(_personal_records_route_allowed("GET", "/api/v1/recommendations"))
        self.assertFalse(_personal_records_route_allowed("POST", "/api/v1/screener"))
        self.assertFalse(_personal_records_route_allowed("POST", "/api/v1/auth/login"))

    def test_public_user_recognizes_cloudbase_wechat_identity(self) -> None:
        user = public_user(
            {
                "id": 8,
                "mobile": "",
                "real_name": "微信用户",
                "identity_type": "cloudbase_personal",
            }
        )
        self.assertTrue(user["is_wechat_user"])
        self.assertIsNone(user["mobile"])

    def test_personal_records_mode_never_uses_legacy_plan_copy(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "MINIAPP_PRODUCT_MODE": "personal_records",
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
                    "id": 8,
                    "real_name": "微信用户",
                    "identity_type": "cloudbase_personal",
                    "roles": ["ADMIN"],
                    "is_admin": True,
                    "service_status": "active",
                    "service_valid": True,
                }
            )
        features = " ".join(
            feature for plan in result["plans"] for feature in plan["features"]
        )
        self.assertIn("云端备份", features)
        self.assertNotIn("买卖", features)


if __name__ == "__main__":
    unittest.main()
