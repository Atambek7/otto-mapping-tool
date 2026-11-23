"""Request/response contracts for bounded discovery runs."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any, Literal

from pydantic import Field

from schemas.discovery import ContractModel, Account


class IncrementalScanRequest(ContractModel):
    account: Account
    from_date: datetime | None = Field(default=None, alias="fromDate")


class KnownSkuReconciliationRequest(ContractModel):
    account: Account
    skus: list[str] = Field(min_length=1, max_length=500)


class ScanAcceptedResponse(ContractModel):
    run_id: str = Field(alias="runId")
    job_id: str = Field(alias="jobId")
    account: Account
    run_type: Literal["INCREMENTAL_SCAN", "KNOWN_SKU_RECONCILIATION"] = Field(alias="runType")
    run_state: Literal["PENDING", "RUNNING", "STAGED", "APPLYING", "SUCCEEDED", "PARTIAL"] = Field(alias="runState")
    window_from: datetime | None = Field(default=None, alias="windowFrom")
    cutoff_at: datetime = Field(alias="cutoffAt")
    sku_count: int | None = Field(default=None, alias="skuCount")
    http_status: Literal[202] = Field(alias="httpStatus")


class ScanRunDTO(ContractModel):
    id: str
    account: Account
    run_type: Literal["INCREMENTAL_SCAN", "KNOWN_SKU_RECONCILIATION"] = Field(alias="runType")
    run_state: str = Field(alias="runState")
    window_from: datetime | None = Field(alias="windowFrom")
    cutoff_at: datetime | None = Field(alias="cutoffAt")
    total_rows: int = Field(alias="totalRows")
    coverage: dict[str, Any]
    errors: list[dict[str, Any]]
    parameters: dict[str, Any] | None = None
    created_at: datetime = Field(alias="createdAt")
    started_at: datetime | None = Field(alias="startedAt")
    finished_at: datetime | None = Field(alias="finishedAt")


class ScanRunListResponse(ContractModel):
    items: list[ScanRunDTO]
    count: int
    limit: int
    offset: int


class BaselineConfirmRequest(ContractModel):
    account: Account
    run_id: str = Field(alias="runId", min_length=1, max_length=64)


class DiscoveryScheduleUpdateRequest(ContractModel):
    account: Account
    enabled: bool
    timezone: str = Field(default="Europe/Berlin", min_length=1, max_length=96)
    weekday: int = Field(default=0, ge=0, le=6)
    local_time: time = Field(default=time(3, 0), alias="localTime")


class DiscoveryScheduleDTO(ContractModel):
    account: Account
    baseline_confirmed: bool = Field(alias="baselineConfirmed")
    baseline_run_id: str | None = Field(alias="baselineRunId")
    baseline_confirmed_at: datetime | None = Field(alias="baselineConfirmedAt")
    enabled: bool
    timezone: str
    weekday: int
    local_time: str = Field(alias="localTime")
    next_at: datetime | None = Field(alias="nextAt")
    last_scheduled_slot_at: datetime | None = Field(alias="lastScheduledSlotAt")


class CandidateObservationDTO(ContractModel):
    requested_sku: str = Field(alias="requestedSku")
    classification: str
    period: str | None
    reduction_state: str | None = Field(alias="reductionState")
    episode_id: str | None = Field(alias="episodeId")
    episode_state: str | None = Field(alias="episodeState")
    origin: str | None
    routing_state: str | None = Field(alias="routingState")
    routing_error_code: str | None = Field(alias="routingErrorCode")
    source_product_id: str | None = Field(alias="sourceProductId")
    source_type: str | None = Field(alias="sourceType")
    factory_id: str | None = Field(alias="factoryId")
    factory_name: str | None = Field(alias="factoryName")
    title: str | None


class CandidateObservationListResponse(ContractModel):
    items: list[CandidateObservationDTO]
    count: int
    page: int
    page_size: int = Field(alias="pageSize")
    total_pages: int = Field(alias="totalPages")
