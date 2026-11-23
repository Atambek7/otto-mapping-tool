"""Typed import, status observation, candidate, and product read contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Account = Literal["JV", "XL"]
RunType = Literal["FILE_IMPORT", "INCREMENTAL_SCAN", "KNOWN_SKU_RECONCILIATION", "LEGACY"]
RunMode = Literal["BASELINE", "BASELINE_EXTEND", "INCREMENTAL"]
RunState = Literal[
    "PENDING", "RUNNING", "STAGED", "APPLYING", "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED",
    "LEGACY_UNVERIFIED",
]
Period = Literal["3M", "12M"]
Classification = Literal["EXACT_CODE", "TITLE_ONLY", "AMBIGUOUS", "NONE", "UNCHECKABLE"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ImportRunDTO(ContractModel):
    id: str
    account: Account
    run_type: RunType = Field(alias="runType")
    run_mode: RunMode | None = Field(default=None, alias="runMode")
    run_state: RunState = Field(alias="runState")
    filename: str | None
    total_rows: int = Field(alias="totalRows")
    processed_count: int = Field(alias="processedCount")
    afterbuy_count: int = Field(alias="afterbuyCount")
    direct_otto_count: int = Field(alias="directOttoCount")
    skipped_count: int = Field(alias="skippedCount")
    error_count: int = Field(alias="errorCount")
    status: str
    legacy_status: str = Field(alias="legacyStatus")
    summary: dict[str, Any]
    created_at: datetime = Field(alias="createdAt")
    started_at: datetime | None = Field(default=None, alias="startedAt")
    finished_at: datetime | None = Field(default=None, alias="finishedAt")


class ImportRunListResponse(ContractModel):
    items: list[ImportRunDTO]
    count: int


class ImportRowDTO(ContractModel):
    id: str
    run_id: str = Field(alias="runId")
    row_number: int = Field(alias="rowNumber")
    raw_identifier: str = Field(alias="rawIdentifier")
    normalized_identifier: str | None = Field(alias="normalizedIdentifier")
    period_hint: Period | None = Field(alias="periodHint")
    row_state: Literal[
        "PENDING", "VALID", "INVALID", "DUPLICATE", "IDENTITY_UNRESOLVED", "ERROR"
    ] = Field(alias="rowState")
    row: dict[str, Any]
    errors: list[dict[str, Any]]


class ImportRunDetailDTO(ImportRunDTO):
    parameters: dict[str, Any]
    coverage: dict[str, Any]
    errors: list[dict[str, Any]]
    window_from: datetime | None = Field(default=None, alias="windowFrom")
    cutoff_at: datetime | None = Field(default=None, alias="cutoffAt")


class ImportRunRowsResponse(ContractModel):
    items: list[ImportRowDTO]
    count: int
    page: int = Field(ge=1)
    page_size: int = Field(alias="pageSize", ge=1, le=500)
    total_pages: int = Field(alias="totalPages", ge=0)


class ImportPreviewRequest(ContractModel):
    account: Account
    raw_text: str = Field(min_length=1, max_length=20_000_000)
    filename: str = Field(default="paste_input.txt", max_length=255)
    run_mode: RunMode | None = Field(default=None, alias="runMode")
    dry_run: bool = Field(default=False, alias="dryRun")


class ImportCommitResponse(ContractModel):
    success: bool
    status: str
    run_id: str = Field(alias="runId")
    job_id: str = Field(alias="jobId")
    http_status: int = Field(alias="httpStatus")


class StatusObservationDTO(ContractModel):
    id: str
    account: Account
    requested_sku: str = Field(alias="requestedSku")
    response_sku: str | None = Field(alias="responseSku")
    classification: Classification
    period: Period | None
    http_status: int | None = Field(alias="httpStatus")
    status: str | None
    market_place_status: str | None = Field(alias="marketPlaceStatus")
    active: bool | None
    last_modified: datetime | None = Field(alias="lastModified")
    errors: list[dict[str, Any]]
    unknown_codes: list[str] = Field(alias="unknownCodes")
    reduction_status: str = Field(alias="reductionStatus")
    observed_at: datetime = Field(alias="observedAt")


class CandidateEpisodeDTO(ContractModel):
    id: str
    account: Account
    observed_sku: str = Field(alias="observedSku")
    canonical_identity: str = Field(alias="canonicalIdentity")
    source_product_id: str | None = Field(alias="sourceProductId")
    confirmed_ean: str | None = Field(alias="confirmedEan")
    moin: str | None
    period: Period
    classification: Literal["EXACT_CODE", "TITLE_ONLY"]
    origin: Literal["BASELINE", "NEW"]
    episode_state: Literal["OPEN", "CLOSED"] = Field(alias="episodeState")
    first_detected_at: datetime = Field(alias="firstDetectedAt")
    last_detected_at: datetime = Field(alias="lastDetectedAt")
    closed_at: datetime | None = Field(alias="closedAt")


class ProductReadDTO(ContractModel):
    """One account-scoped current/history candidate row; ``id`` is its episode ID."""

    id: str
    candidate_episode_id: str = Field(alias="candidateEpisodeId")
    source_product_id: str = Field(alias="sourceProductId")
    account: Account
    data_scope: Literal["LEGACY_UNVERIFIED", "PRODUCTION"] = Field(alias="dataScope")
    source_type: Literal["AFTERCOOL", "AFTERBUY", "OTTO_DIRECT"] = Field(alias="source")
    factory_id: str | None = Field(alias="factoryId")
    factory_name: str | None = Field(alias="factoryName")
    sku: str | None
    ean: str | None
    moin: str | None
    period: Period | None
    status: str = Field(description="Card completeness derived from category and attributes; operation status is separate.")
    classification: Literal["EXACT_CODE", "TITLE_ONLY"]
    origin: Literal["BASELINE", "NEW"]
    episode_state: Literal["OPEN", "CLOSED"] = Field(alias="episodeState")
    operation_id: str | None = Field(alias="operationId")
    operation_status: str | None = Field(alias="operationStatus")
    operation_version: int | None = Field(alias="operationVersion")
    current_revision_number: int | None = Field(alias="currentRevisionNumber")
    target_sku: str | None = Field(alias="targetSku")
    new_ean: str | None = Field(alias="newEan")
    title: str | None
    source_title: str | None = Field(default=None, alias="sourceTitle")
    draft_title: str | None = Field(default=None, alias="draftTitle")
    prepared_title: str | None = Field(default=None, alias="preparedTitle")
    saved_edit: dict[str, Any] | None = Field(default=None, alias="savedEdit")
    price_cents: int | None = Field(alias="priceCents")
    price_preview: dict[str, Any] | None = Field(default=None, alias="pricePreview")
    otto_source_price: dict[str, Any] | None = Field(default=None, alias="ottoSourcePrice")
    aftercool_source_price: dict[str, Any] | None = Field(default=None, alias="aftercoolSourcePrice")
    price_review_reason: str | None = Field(default=None, alias="priceReviewReason")
    target_price_status: Literal["CALCULATED", "PRICE_UNAVAILABLE", "PRICE_REVIEW_REQUIRED"] = Field(alias="targetPriceStatus")
    category: str | None
    image_url: str | None = Field(alias="imageUrl")
    attributes: list[dict[str, Any]] = Field(default_factory=list)
    source_data: dict[str, Any] = Field(default_factory=dict, alias="sourceData")
    errors: list[dict[str, Any]] = Field(default_factory=list)
    error_codes: list[str] = Field(default_factory=list, alias="errorCodes")
    error_titles: list[str] = Field(default_factory=list, alias="errorTitles")
    last_modified: datetime | None = Field(default=None, alias="lastModified")
    last_checked_at: datetime | None = Field(alias="lastCheckedAt")


class ProductPageDTO(ContractModel):
    page: int = Field(ge=1)
    page_size: int = Field(alias="pageSize", ge=1, le=200)
    total: int = Field(ge=0)
    total_pages: int = Field(alias="totalPages", ge=0)


class ProductCatalogMetricsDTO(ContractModel):
    """Account-wide counts over linked, currently open production episodes."""

    scope: Literal["ACCOUNT_OPEN_CANDIDATES", "CATALOG_OPEN_CANDIDATES"] = "ACCOUNT_OPEN_CANDIDATES"
    total: int = Field(ge=0)
    count_3m: int = Field(alias="count3m", ge=0)
    count_12m: int = Field(alias="count12m", ge=0)
    status_counts: dict[str, int] = Field(alias="statusCounts")
    over_70_count: int = Field(default=0, alias="over70Count", ge=0)
    categories: list[str] = Field(default_factory=list)


class ProductListResponseDTO(ContractModel):
    items: list[ProductReadDTO]
    page: ProductPageDTO
    metrics: ProductCatalogMetricsDTO | None = None
