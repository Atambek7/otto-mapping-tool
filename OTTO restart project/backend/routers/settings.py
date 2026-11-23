"""Account-scoped runtime configuration and local content defaults; no provider calls."""

from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from integrations.aftercool_client import AftercoolApiError, AftercoolCredentials
from integrations.openai_client import configuration_status
from integrations.otto_client import OttoApiError, OttoCredentials
from jobs.dispatcher import job_allowlist
from services.categories_repo import get_allowed_attribute_names, get_category
from services.constant_attributes import get_account_specific_defaults, get_defaults_for_account
from services.fulfillment_defaults import (
    DEFAULT_PROCESSING_TIME_DAYS, DEFAULT_SHIPPING_PROFILE_IDS,
    DEFAULT_SHIPPING_PROFILE_NAME, DEFAULT_STOCK_QUANTITY,
)
from services.preparation_service import calculate_price_from_cents

router = APIRouter(prefix="/api/restart", tags=["settings"])
Account = Literal["JV", "XL"]


def _principal(request: Request, account: str):
    principal = getattr(request.state, "principal", None)
    if principal is None or account not in principal.accounts:
        raise HTTPException(403, detail={"code": "FORBIDDEN", "message": "Account access is required."})
    return principal


@router.get("/settings")
def runtime_settings(request: Request, account: Account):
    principal = _principal(request, account)
    config = request.app.state.runtime_config
    try:
        OttoCredentials.from_environment(account, os.environ)
        otto_configured = True
    except OttoApiError:
        otto_configured = False
    try:
        AftercoolCredentials.from_environment(os.environ)
        aftercool_configured = True
    except AftercoolApiError:
        aftercool_configured = False
    price = calculate_price_from_cents(account, otto_price_cents=20000, source_type="OTTO_DIRECT")
    return {
        "account": account, "mode": config.mode.value,
        "operator": {"id": principal.subject, "accounts": sorted(principal.accounts)},
        "otto": {"configured": otto_configured, "readEnabled": config.otto_read_enabled},
        "aftercool": {"configured": aftercool_configured, "readEnabled": config.aftercool_read_enabled},
        "ai": configuration_status(os.environ, enabled=config.openai_enabled),
        "publication": {"productWriteEnabled": config.product_write_enabled,
                        "availabilityWriteEnabled": config.availability_write_enabled,
                        "jobTypes": list(job_allowlist(
                            availability_write_enabled=config.availability_write_enabled,
                        ))},
        "price": {"discountCents": price["discountCents"], "fractionCents": 99,
                  "source": "MAX_CONFIRMED_OTTO_AFTERCOOL", "directOttoSource": "OTTO_STANDARD_PRICE"},
        "fulfillment": {"quantity": DEFAULT_STOCK_QUANTITY, "processingDays": int(DEFAULT_PROCESSING_TIME_DAYS),
                        "shippingProfile": DEFAULT_SHIPPING_PROFILE_NAME,
                        "shippingProfileId": DEFAULT_SHIPPING_PROFILE_IDS[account]},
        "ean": {"digits": 13, "globallyUnique": True},
    }


@router.get("/defaults")
def content_defaults(request: Request, account: Account, category: str = Query(min_length=1, max_length=200)):
    _principal(request, account)
    if get_category(category) is None:
        raise HTTPException(422, detail={"code": "CATEGORY_SCHEMA_UNAVAILABLE", "message": "Choose a category from the OTTO catalogue first."})
    allowed = get_allowed_attribute_names(category) or set()
    attributes = {name: value for name, value in get_defaults_for_account(account).items() if name in allowed}
    return {"account": account, "category": category, "attributes": attributes,
            "accountAttributeNames": [name for name in get_account_specific_defaults(account) if name in attributes]}
