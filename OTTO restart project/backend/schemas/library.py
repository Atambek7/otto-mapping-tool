"""Typed production Library and Aftercool read-only contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from schemas.discovery import ContractModel

Account = Literal["JV", "XL"]


class LibraryPageDTO(ContractModel):
    page: int = Field(ge=1)
    page_size: int = Field(alias="pageSize", ge=1, le=200)
    total_items: int = Field(alias="totalItems", ge=0)
    total_pages: int = Field(alias="totalPages", ge=0)


class LibraryLinkedIdentifierDTO(ContractModel):
    ean: str
    target_sku: str | None = Field(alias="targetSku")
    link_id: str = Field(alias="linkId")
    link_status: str = Field(alias="linkStatus")
    linked_at: datetime = Field(alias="linkedAt")


class LibraryOnlineIdentifierDTO(ContractModel):
    ean: str
    sku: str
    status: Literal["ONLINE"]
    checked_at: datetime = Field(alias="checkedAt")
    observation_id: str = Field(alias="observationId")


class LibraryLinkDTO(ContractModel):
    id: str
    account: Account
    source_product_id: str = Field(alias="sourceProductId")
    source_type: Literal["AFTERCOOL", "AFTERBUY", "OTTO_DIRECT"] = Field(alias="sourceType")
    aftercool_product_id: str | None = Field(alias="aftercoolProductId")
    aftercool_sku: str | None = Field(alias="aftercoolSku")
    source_ean: str = Field(alias="sourceEan")
    new_ean: str = Field(alias="newEan")
    factory_id: str | None = Field(alias="factoryId")
    factory_name: str | None = Field(alias="factoryName")
    title: str | None
    image_url: str | None = Field(alias="imageUrl")
    status: str
    created_at: datetime = Field(alias="createdAt")
    link_status: str = Field(alias="linkStatus")
    operation_id: str | None = Field(alias="operationId")
    attempt_number: int | None = Field(alias="attemptNumber")
    operation_status: str | None = Field(alias="operationStatus")
    revision_number: int | None = Field(alias="revisionNumber")
    publication_state: str = Field(alias="publicationState")
    target_sku: str | None = Field(alias="targetSku")
    linked_identifier: LibraryLinkedIdentifierDTO = Field(alias="linkedIdentifier")
    confirmed_online_identifiers: list[LibraryOnlineIdentifierDTO] = Field(alias="confirmedOnlineIdentifiers")
    latest_marketplace_observation: dict[str, Any] | None = Field(alias="latestMarketplaceObservation")
    processing_confirmed: bool = Field(alias="processingConfirmed")
    online_confirmed: bool = Field(alias="onlineConfirmed")
    availability: dict[str, Any]


class LibraryLinkSummaryDTO(ContractModel):
    linked_records: int = Field(alias="linkedRecords", ge=0)
    confirmed_online_on_page: int = Field(alias="confirmedOnlineOnPage", ge=0)
    pool_available: int = Field(alias="poolAvailable", ge=0)
    pool_total: int = Field(alias="poolTotal", ge=0)
    events_count: int = Field(alias="eventsCount", ge=0)


class LibraryLinkListResponse(ContractModel):
    items: list[LibraryLinkDTO]
    page: LibraryPageDTO
    summary: LibraryLinkSummaryDTO


class AftercoolSourceProductDTO(ContractModel):
    id: str
    source_type: Literal["AFTERCOOL", "AFTERBUY", "OTTO_DIRECT"] = Field(alias="sourceType")
    aftercool_product_id: str | None = Field(alias="aftercoolProductId")
    sku: str | None
    ean: str | None
    factory_id: str | None = Field(alias="factoryId")
    factory_name: str | None = Field(alias="factoryName")
    title: str | None


class AftercoolLibraryLookupItemDTO(ContractModel):
    source_product: AftercoolSourceProductDTO = Field(alias="sourceProduct")
    linked_identifiers: list[LibraryLinkedIdentifierDTO] = Field(alias="linkedIdentifiers")
    confirmed_online_identifiers: list[LibraryOnlineIdentifierDTO] = Field(alias="confirmedOnlineIdentifiers")
    latest_marketplace_observations: list[dict[str, Any]] = Field(alias="latestMarketplaceObservations")
    history: list[dict[str, Any]]
    events: list[dict[str, Any]]
    history_truncated: bool = Field(alias="historyTruncated")
    checked_at: datetime | None = Field(alias="checkedAt")
    lookup_read_at: datetime = Field(alias="lookupReadAt")


class AftercoolLibraryLookupResponse(ContractModel):
    account: Account
    data_scope: Literal["PRODUCTION"] = Field(alias="dataScope")
    page: LibraryPageDTO
    checked_at: datetime | None = Field(alias="checkedAt")
    lookup_read_at: datetime = Field(alias="lookupReadAt")
    items: list[AftercoolLibraryLookupItemDTO]
