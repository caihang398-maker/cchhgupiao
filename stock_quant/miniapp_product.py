from __future__ import annotations

import os


PRODUCT_MODE_RESEARCH = "research"
PRODUCT_MODE_PERSONAL_RECORDS = "personal_records"
VALID_PRODUCT_MODES = {PRODUCT_MODE_RESEARCH, PRODUCT_MODE_PERSONAL_RECORDS}


def miniapp_product_mode() -> str:
    value = os.getenv("MINIAPP_PRODUCT_MODE", PRODUCT_MODE_RESEARCH).strip().lower()
    return value if value in VALID_PRODUCT_MODES else PRODUCT_MODE_RESEARCH


def personal_records_mode_enabled() -> bool:
    return miniapp_product_mode() == PRODUCT_MODE_PERSONAL_RECORDS

