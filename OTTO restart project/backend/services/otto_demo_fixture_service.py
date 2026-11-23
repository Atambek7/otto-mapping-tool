"""Explicit account-scoped OTTO status fixtures for DEMO/TEST only."""

from __future__ import annotations

from typing import Any, Literal, Optional

from runtime_mode import RuntimeMode

CODE_3M = "602016 - Nonseller"
CODE_12M = "602016 - LOW_PERFORMER"


class OttoDemoFixtureService:
    """Provide marked demo data without performing network lookups."""

    def __init__(
        self,
        runtime_mode: RuntimeMode | str,
        fixtures: Optional[dict[tuple[str, str], dict[str, Any]]] = None,
    ) -> None:
        self.runtime_mode = RuntimeMode(runtime_mode)
        if self.runtime_mode not in {RuntimeMode.DEMO, RuntimeMode.TEST}:
            raise RuntimeError("OTTO status fixtures are available only in DEMO or TEST mode.")
        self._fixtures = dict(fixtures or {})

    def register_fixture(
        self,
        account: str,
        sku_or_ean: str,
        period: Literal["3M", "12M"],
        code: Optional[str] = None,
        title: str = "",
    ) -> None:
        if account not in {"JV", "XL"}:
            raise ValueError("account must be JV or XL")
        if period not in {"3M", "12M"}:
            raise ValueError("period must be 3M or 12M")
        self._fixtures[(account, sku_or_ean)] = {
            "period": period,
            "code": code or (CODE_3M if period == "3M" else CODE_12M),
            "title": title,
        }

    def get_status(
        self,
        account: str,
        sku_or_ean: str,
        hint_period: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return a marked fixture; a period hint never creates a fixture."""
        del hint_period
        fixture = self._fixtures.get((account, sku_or_ean))
        if fixture:
            return {
                "sku": sku_or_ean,
                "account": account,
                "status": "DEMO_ONLY",
                "is_candidate": True,
                "period": fixture["period"],
                "code": fixture["code"],
                "title": fixture.get("title", ""),
                "source": "DEMO_FIXTURE",
                "verified": False,
                "data_scope": "DEMO_ONLY",
            }
        return {
            "sku": sku_or_ean,
            "account": account,
            "status": "UNVERIFIED",
            "is_candidate": False,
            "period": None,
            "code": None,
            "title": None,
            "source": "NOT_IMPLEMENTED",
            "verified": False,
            "data_scope": "UNVERIFIED",
            "reason": "No current OTTO Marketplace Status response is available.",
        }
