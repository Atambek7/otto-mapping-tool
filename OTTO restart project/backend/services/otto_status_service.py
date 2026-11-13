"""Pure analysis of OTTO Marketplace Status responses.

This module performs no I/O and does not infer a restriction from SKU, account,
or an imported period hint. Live retrieval is implemented in a later stage.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Literal, Mapping, Optional

CODE_3M = "602016 - Nonseller"
CODE_12M = "602016 - LOW_PERFORMER"

ClassificationKind = Literal[
    "EXACT_CODE", "TITLE_ONLY", "AMBIGUOUS", "NONE", "UNCHECKABLE"
]
Period = Literal["3M", "12M"]

_CODE_PERIODS = {
    "602016 - nonseller": "3M",
    "602016 - low_performer": "12M",
}
_PERIOD_PATTERNS: tuple[tuple[Period, re.Pattern[str]], ...] = (
    ("3M", re.compile(r"\b(?:drei|3)\s+monaten?\b", re.IGNORECASE)),
    ("12M", re.compile(r"\b(?:zwölf|zwoelf|12)\s+monaten?\b", re.IGNORECASE)),
)
_NO_NET_SALES_PATTERNS = (
    re.compile(r"\bkein(?:e[rmns]?)?\b.{0,100}\bnettoumsatz\b", re.IGNORECASE),
    re.compile(r"\bohne\b.{0,100}\bnettoumsatz\b", re.IGNORECASE),
    re.compile(r"\bnettoumsatz\b.{0,100}\b(?:kein(?:e[rmns]?)?|nicht|ohne)\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class StatusClassification:
    """Classification plus only the evidence present in the supplied response."""

    classification: ClassificationKind
    period: Optional[Period]
    reason: str
    http_status: Optional[int] = None
    sku: Optional[str] = None
    marketplace_status: Any = None
    active: Optional[bool] = None
    last_modified: Optional[str] = None
    status: Any = None
    errors: tuple[Any, ...] = ()
    matched_codes: tuple[str, ...] = ()
    matched_titles: tuple[str, ...] = ()
    unknown_codes: tuple[str, ...] = ()


def _normalize_code(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    normalized = re.sub(r"\s+", " ", normalized.strip())
    normalized = re.sub(r"\s*-\s*", " - ", normalized)
    return normalized.casefold()


def _title_periods(title: str) -> set[Period]:
    has_no_net_sales = any(pattern.search(title) for pattern in _NO_NET_SALES_PATTERNS)
    if not has_no_net_sales:
        return set()
    matches = [period for period, pattern in _PERIOD_PATTERNS if pattern.search(title)]
    return set(matches)


def _result(
    classification: ClassificationKind,
    period: Optional[Period],
    reason: str,
    *,
    errors: tuple[Any, ...] = (),
    matched_codes: tuple[str, ...] = (),
    matched_titles: tuple[str, ...] = (),
    unknown_codes: tuple[str, ...] = (),
) -> StatusClassification:
    return StatusClassification(
        classification=classification,
        period=period,
        reason=reason,
        errors=errors,
        matched_codes=matched_codes,
        matched_titles=matched_titles,
        unknown_codes=unknown_codes,
    )


def classify_errors(errors: Any) -> StatusClassification:
    """Classify all ``errors[]`` using exact codes and corroborating titles.

    Unknown codes and the original error objects are retained for diagnostics.
    A shared numeric prefix, partial code, or period phrase without a net-sales
    reason is never enough to classify a 3M/12M restriction.
    """
    if not isinstance(errors, list):
        return _result("UNCHECKABLE", None, "errors must be an array")
    if any(not isinstance(item, Mapping) for item in errors):
        return _result(
            "UNCHECKABLE", None, "an errors entry is not an object", errors=tuple(errors),
        )

    preserved: tuple[Any, ...] = tuple(errors)
    exact_periods: set[Period] = set()
    title_periods: set[Period] = set()
    matched_codes: list[str] = []
    matched_titles: list[str] = []
    unknown_codes: list[str] = []

    for item in errors:
        raw_code = item.get("code")
        raw_title = item.get("title")
        if raw_code is not None and not isinstance(raw_code, str):
            return _result(
                "UNCHECKABLE", None, "an error code is not text", errors=preserved,
            )
        if raw_title is not None and not isinstance(raw_title, str):
            return _result(
                "UNCHECKABLE", None, "an error title is not text", errors=preserved,
            )

        if isinstance(raw_code, str) and raw_code.strip():
            code = _normalize_code(raw_code)
            code_period = _CODE_PERIODS.get(code)
            if code_period:
                exact_periods.add(code_period)
                matched_codes.append(raw_code)
            else:
                unknown_codes.append(raw_code)

        if isinstance(raw_title, str) and raw_title.strip():
            detected_title_periods = _title_periods(raw_title)
            if detected_title_periods:
                title_periods.update(detected_title_periods)
                matched_titles.append(raw_title)

    all_periods = exact_periods | title_periods
    if len(all_periods) > 1:
        return _result(
            "AMBIGUOUS", None, "conflicting 3M/12M signals in errors",
            errors=preserved, matched_codes=tuple(matched_codes),
            matched_titles=tuple(matched_titles), unknown_codes=tuple(unknown_codes),
        )
    if exact_periods:
        period = next(iter(exact_periods))
        return _result(
            "EXACT_CODE", period, "recognized complete OTTO error code",
            errors=preserved, matched_codes=tuple(matched_codes),
            matched_titles=tuple(matched_titles), unknown_codes=tuple(unknown_codes),
        )
    if title_periods:
        period = next(iter(title_periods))
        return _result(
            "TITLE_ONLY", period, "net-sales title signal without a recognized code",
            errors=preserved, matched_titles=tuple(matched_titles),
            unknown_codes=tuple(unknown_codes),
        )
    return _result(
        "NONE", None, "no confirmed 3M/12M signal",
        errors=preserved, unknown_codes=tuple(unknown_codes),
    )


def classify_marketplace_status_response(
    response: Any,
    expected_sku: str,
    http_status: Optional[int] = 200,
) -> StatusClassification:
    """Validate response identity/status and classify its error array.

    Missing response fields remain ``None``. In particular this does not infer
    marketplace status or active state from ``status`` or from restriction.
    """
    if not isinstance(response, Mapping):
        base = _result("UNCHECKABLE", None, "response body is not an object")
        return replace(base, http_status=http_status)

    body_sku = response.get("sku")
    errors = response.get("errors")
    context = {
        "http_status": http_status,
        "sku": body_sku if isinstance(body_sku, str) else None,
        "marketplace_status": response.get("marketPlaceStatus"),
        "active": response.get("active") if isinstance(response.get("active"), bool) else None,
        "last_modified": response.get("lastModified") if isinstance(response.get("lastModified"), str) else None,
        "status": response.get("status"),
    }
    uncheckable_reason = None
    if http_status != 200:
        uncheckable_reason = "HTTP response was not successful"
    elif not isinstance(expected_sku, str) or not expected_sku:
        uncheckable_reason = "expected SKU is missing"
    elif not isinstance(body_sku, str) or not body_sku:
        uncheckable_reason = "response SKU is missing"
    elif body_sku != expected_sku:
        uncheckable_reason = "response SKU does not match requested SKU"

    if uncheckable_reason:
        base = classify_errors(errors)
        return replace(
            base, classification="UNCHECKABLE", period=None,
            reason=uncheckable_reason, **context,
        )

    classified = classify_errors(errors)
    return replace(classified, **context)


class OttoStatusService:
    """Compatibility facade; it analyzes supplied responses but performs no lookup."""

    def analyze_response(
        self,
        response: Any,
        expected_sku: str,
        http_status: Optional[int] = 200,
    ) -> StatusClassification:
        return classify_marketplace_status_response(response, expected_sku, http_status)

    def get_status(
        self,
        account: str,
        sku_or_ean: str,
        hint_period: Optional[str] = None,
    ) -> dict[str, Any]:
        """Keep the legacy lookup contract fail-closed until S06 adds an adapter."""
        del hint_period
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
