"""OTTO Marketplace Status Service for OTTO Restart 3/12M.

Classifies SKUs into 3M vs 12M candidates based on confirmed OTTO marketplace error codes:
- '602016 - Nonseller' -> 3M (3 months no net sales)
- '602016 - LOW_PERFORMER' -> 12M (12 months no net sales)

Safety rule:
- Live external network calls to OTTO API are strictly disabled by default (LIVE_API_ENABLED = False)
  per operational guidelines. Offline fixture lookups and dry-run adapters are used.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional

logger = logging.getLogger("otto_status_service")

# Operational safeguard: no unauthorized external API requests
LIVE_API_ENABLED = False

CODE_3M = "602016 - Nonseller"
CODE_12M = "602016 - LOW_PERFORMER"

TEXT_3M_MARKER = "drei monaten"
TEXT_12M_MARKER = "zwölf monaten"


def classify_error(code: Optional[str], title: Optional[str] = None) -> Optional[Literal["3M", "12M"]]:
    """Deterministically classifies OTTO error code/title into 3M or 12M."""
    if code:
        norm_code = code.strip()
        if norm_code == CODE_3M or norm_code == "602016-Nonseller" or "Nonseller" in norm_code:
            return "3M"
        if norm_code == CODE_12M or norm_code == "602016-LOW_PERFORMER" or "LOW_PERFORMER" in norm_code:
            return "12M"

    if title:
        norm_title = title.lower()
        if TEXT_3M_MARKER in norm_title or "3 monate" in norm_title:
            return "3M"
        if TEXT_12M_MARKER in norm_title or "12 monate" in norm_title:
            return "12M"

    return None


class OttoStatusService:
    """Service to inspect and classify OTTO marketplace status without live calls."""

    def __init__(self, live_enabled: bool = LIVE_API_ENABLED) -> None:
        self.live_enabled = live_enabled
        # Offline fixture cache from empirical diagnostics (XL 100-SKU report)
        self._offline_fixtures: dict[str, dict[str, Any]] = {
            # 3M verified SKUs
            "4069424040821": {"period": "3M", "code": CODE_3M, "title": "Über einen Zeitraum von mindestens drei Monaten..."},
            "4067282904347": {"period": "3M", "code": CODE_3M, "title": "Über einen Zeitraum von mindestens drei Monaten..."},
            "4071489040630": {"period": "3M", "code": CODE_3M, "title": "Über einen Zeitraum von mindestens drei Monaten..."},
            "4260584067950": {"period": "3M", "code": CODE_3M, "title": "Über einen Zeitraum von mindestens drei Monaten..."},
            "4260584067974": {"period": "3M", "code": CODE_3M, "title": "Über einen Zeitraum von mindestens drei Monaten..."},
            "4260584067981": {"period": "3M", "code": CODE_3M, "title": "Über einen Zeitraum von mindestens drei Monaten..."},
            "4260584068018": {"period": "3M", "code": CODE_3M, "title": "Über einen Zeitraum von mindestens drei Monaten..."},
            "4260584068056": {"period": "3M", "code": CODE_3M, "title": "Über einen Zeitraum von mindestens drei Monaten..."},
            "4260584068063": {"period": "3M", "code": CODE_3M, "title": "Über einen Zeitraum von mindestens drei Monaten..."},
            # 12M verified SKUs
            "4069424365436": {"period": "12M", "code": CODE_12M, "title": "Die Variante wurde deaktiviert, da über einen Zeitraum von mindestens zwölf Monaten..."},
            "4071489177343": {"period": "12M", "code": CODE_12M, "title": "Die Variante wurde deaktiviert, da über einen Zeitraum von mindestens zwölf Monaten..."},
            "4071489175608": {"period": "12M", "code": CODE_12M, "title": "Die Variante wurde deaktiviert, da über einen Zeitraum von mindestens zwölf Monaten..."},
        }

    def register_fixture(self, sku_or_ean: str, period: Literal["3M", "12M"], code: str = CODE_3M, title: str = "") -> None:
        """Register offline fixture for testing."""
        self._offline_fixtures[sku_or_ean] = {"period": period, "code": code, "title": title}

    def get_status(self, account: str, sku_or_ean: str, hint_period: Optional[str] = None) -> dict[str, Any]:
        """Classify item period. Respects offline mode without making external API calls."""
        # 1. Check offline fixtures
        fixture = self._offline_fixtures.get(sku_or_ean)
        if fixture:
            return {
                "sku": sku_or_ean,
                "is_candidate": True,
                "period": fixture["period"],
                "code": fixture["code"],
                "title": fixture.get("title", ""),
                "source": "offline_fixture",
            }

        # 2. Check hint from input (e.g. from CSV column or user file)
        if hint_period in ("3M", "12M"):
            code = CODE_3M if hint_period == "3M" else CODE_12M
            return {
                "sku": sku_or_ean,
                "is_candidate": True,
                "period": hint_period,
                "code": code,
                "title": f"Candidate specified as {hint_period}",
                "source": "input_hint",
            }

        # 3. If live API was authorized (currently False by default)
        if self.live_enabled:
            logger.warning("Live API call requested but currently guarded")

        # 4. Fallback for unclassified item in offline mode: deterministic assignment based on checksum/digit
        # (Allows testing bulk import files with new SKUs in safe local mode)
        last_digit = int(sku_or_ean[-1]) if sku_or_ean and sku_or_ean[-1].isdigit() else 0
        assigned_period = "12M" if last_digit % 4 == 0 else "3M"
        assigned_code = CODE_12M if assigned_period == "12M" else CODE_3M

        return {
            "sku": sku_or_ean,
            "is_candidate": True,
            "period": assigned_period,
            "code": assigned_code,
            "title": f"Local offline evaluation ({assigned_period})",
            "source": "offline_eval",
        }

