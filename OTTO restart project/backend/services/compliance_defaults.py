"""Account-specific OTTO product-safety data copied from OttoFlow read-only.

These are Restart-owned constants; there is no runtime import or dependency on
the OttoFlow project. Review the business/legal values before changing them.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

COMPLIANCE_REFERENCE = {
    "system": "OTTOFLOW",
    "method": "COPIED_ACCOUNT_SAFETY_CONFIG",
    "resource": "PRODUCT_SAFETY_COMPLIANCE_V1",
}

_PRODUCT_SAFETY_COMPLIANCE: dict[str, dict[str, Any]] = {
    "JV": {
        "productSafety": {
            "addresses": [{
                "name": "AEA GmbH & Co. KG",
                "address": "Am Flugplatz 28, 88483 Burgrieden",
                "regionCode": "DE",
                "roles": ["DISTRIBUTOR"],
                "email": "info@jvmoebel.de",
                "phone": "07392 - 93 78 44 0",
                "url": "https://www.jvmoebel.de/Infos/Kontakt.htm",
                "components": [],
            }]
        }
    },
    "XL": {
        "productSafety": {
            "addresses": [{
                "name": "XL MOEBEL GmbH",
                "address": "Am Flugplatz 26, 88483 Burgrieden",
                "regionCode": "DE",
                "roles": ["DISTRIBUTOR"],
                "email": "info@xlmoebel.de",
                "phone": "+49 7392 9378188",
                "url": "https://www.xlmoebel.de/Infos/Kontakt.htm",
                "components": [],
            }]
        }
    },
}


def product_safety_compliance(account: str) -> dict[str, Any]:
    normalized = str(account or "").strip().upper()
    if normalized not in _PRODUCT_SAFETY_COMPLIANCE:
        raise ValueError("Account must be JV or XL.")
    return deepcopy(_PRODUCT_SAFETY_COMPLIANCE[normalized])
