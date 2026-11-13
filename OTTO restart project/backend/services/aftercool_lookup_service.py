"""Fail-closed, account-scoped Aftercool lookup for OttoRestart."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any, Mapping, Optional

from integrations.aftercool_client import AftercoolApiError, AftercoolClient
from runtime_mode import RuntimeConfig, RuntimeMode


class AftercoolLookupService:
    """Use explicit DEMO fixtures or an enabled production read adapter.

    The legacy ``live_enabled`` argument is accepted for source compatibility,
    but cannot enable network access. Production reads require both explicit
    PRODUCTION mode and ``OTTORESTART_AFTERCOOL_READ_ENABLED=true``.
    """

    def __init__(
        self,
        live_enabled: bool = False,
        runtime_mode: RuntimeMode | str | None = None,
        fixtures: Optional[dict[tuple[str, str], dict[str, Any]]] = None,
        *,
        runtime_config: RuntimeConfig | None = None,
        client: AftercoolClient | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        # Do not use the old argument as a feature-flag bypass.
        del live_enabled
        self._environment = dict(os.environ if environment is None else environment)
        config = runtime_config or RuntimeConfig.from_environment(self._environment)
        effective_mode = RuntimeMode(runtime_mode) if runtime_mode is not None else config.mode
        self.runtime_config = replace(config, mode=effective_mode)
        self.runtime_mode = effective_mode
        self._client = client
        self._fixtures: dict[tuple[str, str], dict[str, Any]] = dict(fixtures or {})

    def register_fixture(
        self,
        account: str,
        ean: str,
        factory_id: Optional[str] = None,
        factory_name: Optional[str] = None,
        title: str = "",
        price_cents: Optional[int] = None,
        category: Optional[str] = None,
        is_direct_otto: bool = False,
    ) -> None:
        """Register one explicit demo result, including confirmed absence."""
        if self.runtime_mode not in {RuntimeMode.DEMO, RuntimeMode.TEST}:
            raise RuntimeError("Aftercool fixtures are available only in DEMO or TEST mode.")
        normalized_account = str(account).strip().upper()
        if normalized_account not in {"JV", "XL"}:
            raise ValueError("account must be JV or XL")

        if is_direct_otto:
            result = {
                "status": "DEMO_NOT_FOUND_CONFIRMED",
                "found": False,
                "source_type": "OTTO_DIRECT",
                "factory_id": None,
                "factory_name": None,
                "aftercool_product_id": None,
                "title": title or None,
                "price_cents": price_cents,
                "category": category,
                "verified": False,
                "data_scope": "DEMO_ONLY",
            }
        else:
            if not factory_id:
                raise ValueError("factory_id is required for an Aftercool demo fixture")
            result = {
                "status": "DEMO_FOUND",
                "found": True,
                "source_type": "AFTERBUY",
                "factory_id": factory_id,
                "factory_name": factory_name,
                "aftercool_product_id": f"DEMO-{normalized_account}-{factory_id}-{ean[-4:]}",
                "title": title or None,
                "price_cents": price_cents,
                "category": category,
                "verified": False,
                "data_scope": "DEMO_ONLY",
            }
        self._fixtures[(normalized_account, ean)] = result

    def _production_client(self) -> AftercoolClient:
        if self._client is None:
            self._client = AftercoolClient.from_environment(
                environment=self._environment,
                runtime_config=self.runtime_config,
            )
        return self._client

    @staticmethod
    def _unverified_result(
        account: str,
        identifier: str,
        status: str,
        reason: str,
        *,
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "account": account,
            "ean": None,
            "lookup_identifier": identifier,
            "status": status,
            "found": None,
            "source_type": None,
            "product_id": None,
            "aftercool_product_id": None,
            "sku": None,
            "factory_id": None,
            "factory_name": None,
            "title": None,
            "category": None,
            "price_cents": None,
            "image_url": None,
            "image_urls": [],
            "verified": False,
            "data_scope": "UNVERIFIED",
            "reason": reason,
        }
        if reason_code:
            result["reason_code"] = reason_code
        return result

    def lookup_product(
        self,
        account: str,
        ean: str,
        factory_hint: Optional[str] = None,
    ) -> dict[str, Any]:
        """Look up an EAN without treating an error or hint as absence."""
        normalized_account = str(account or "").strip().upper()
        normalized_ean = str(ean or "").strip()

        if self.runtime_mode in {RuntimeMode.DEMO, RuntimeMode.TEST}:
            fixture = self._fixtures.get((normalized_account, normalized_ean))
            if fixture:
                return {"account": normalized_account, "ean": normalized_ean, **fixture}
            return self._unverified_result(
                normalized_account,
                normalized_ean,
                "NOT_IMPLEMENTED",
                "No explicit Aftercool demo fixture is registered.",
            )

        if self.runtime_mode != RuntimeMode.PRODUCTION:
            return self._unverified_result(
                normalized_account,
                normalized_ean,
                "NOT_IMPLEMENTED",
                "Aftercool lookup is available only in explicit PRODUCTION mode.",
            )
        if not self.runtime_config.aftercool_read_enabled:
            return self._unverified_result(
                normalized_account,
                normalized_ean,
                "NOT_IMPLEMENTED",
                "Aftercool read integration is disabled.",
                reason_code="AFTERCOOL_READ_DISABLED",
            )

        try:
            result = self._production_client().lookup_product(
                normalized_account,
                normalized_ean,
                factory_hint=factory_hint,
            )
        except AftercoolApiError as error:
            if error.code in {"ACCOUNT_MISMATCH", "IDENTITY_CONFLICT", "FACTORY_AMBIGUOUS"}:
                return self._unverified_result(
                    normalized_account,
                    normalized_ean,
                    "AMBIGUOUS",
                    "Aftercool returned conflicting account or identity evidence.",
                    reason_code=error.code,
                )
            return self._unverified_result(
                normalized_account,
                normalized_ean,
                "LOOKUP_ERROR",
                "Aftercool lookup did not complete successfully.",
                reason_code=error.code,
            )
        except Exception:
            # Never include exception text: it may contain private request data.
            return self._unverified_result(
                normalized_account,
                normalized_ean,
                "LOOKUP_ERROR",
                "Aftercool lookup failed unexpectedly.",
                reason_code="UNEXPECTED_ERROR",
            )

        if not isinstance(result, Mapping):
            return self._unverified_result(
                normalized_account,
                normalized_ean,
                "LOOKUP_ERROR",
                "Aftercool adapter returned an invalid result.",
                reason_code="INVALID_ADAPTER_RESULT",
            )

        status = result.get("status")
        result_account = result.get("account")
        if result_account is not None and str(result_account).strip().upper() != normalized_account:
            return self._unverified_result(
                normalized_account,
                normalized_ean,
                "AMBIGUOUS",
                "Aftercool adapter returned data for a different account.",
                reason_code="ACCOUNT_MISMATCH",
            )
        if status == "FOUND":
            valid = (
                result.get("found") is True
                and result.get("verified") is True
                and result.get("data_scope") == "PRODUCTION"
                and result.get("source_type") == "AFTERCOOL"
                and bool(result.get("product_id"))
                and result.get("ean") == normalized_ean
                and bool(result.get("factory_id"))
            )
            if not valid:
                return self._unverified_result(
                    normalized_account,
                    normalized_ean,
                    "AMBIGUOUS",
                    "Aftercool result lacks required verified product identity.",
                    reason_code="IDENTITY_INCOMPLETE",
                )
        elif status == "NOT_FOUND_CONFIRMED":
            valid = (
                result.get("found") is False
                and result.get("verified") is True
                and result.get("data_scope") == "PRODUCTION"
                and result.get("source_type") == "OTTO_DIRECT"
                and result.get("lookup_identifier") == normalized_ean
                and result.get("ean") is None
            )
            if not valid:
                return self._unverified_result(
                    normalized_account,
                    normalized_ean,
                    "LOOKUP_ERROR",
                    "Aftercool absence result lacks complete-search evidence.",
                    reason_code="INVALID_ABSENCE_EVIDENCE",
                )
        elif status in {"AMBIGUOUS", "LOOKUP_ERROR", "NOT_IMPLEMENTED"}:
            reason_code = str(result.get("reason_code") or status)
            reasons = {
                "AMBIGUOUS": "Aftercool returned conflicting or incomplete identity evidence.",
                "LOOKUP_ERROR": "Aftercool lookup did not complete successfully.",
                "NOT_IMPLEMENTED": "Aftercool lookup is not implemented or enabled.",
            }
            if status == "AMBIGUOUS" and reason_code == "PROTECTED_FACTORY_MATCH":
                reasons["AMBIGUOUS"] = "All exact Aftercool matches belong to protected factories; identity remains unresolved."
            unverified = self._unverified_result(
                normalized_account,
                normalized_ean,
                status,
                reasons[status],
                reason_code=reason_code,
            )
            excluded_ids = result.get("excluded_factory_ids")
            if isinstance(excluded_ids, list) and all(str(value).isdigit() for value in excluded_ids):
                unverified["excluded_factory_ids"] = sorted({str(value) for value in excluded_ids})
            policy_hash = result.get("factory_exclusion_policy_sha256")
            if isinstance(policy_hash, str) and len(policy_hash) == 64 and all(
                character in "0123456789abcdef" for character in policy_hash
            ):
                unverified["factory_exclusion_policy_sha256"] = policy_hash
            return unverified
        else:
            return self._unverified_result(
                normalized_account,
                normalized_ean,
                "LOOKUP_ERROR",
                "Aftercool adapter returned an unknown status.",
                reason_code="INVALID_ADAPTER_STATUS",
            )

        return {"account": normalized_account, **dict(result)}
