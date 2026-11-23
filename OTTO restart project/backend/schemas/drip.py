"""Typed contracts for persistent Drip Feed scheduling."""

from __future__ import annotations

from datetime import date, time, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DripContract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DripScheduleInput(DripContract):
    account: Literal["JV", "XL"]
    name: str = Field(min_length=1, max_length=160)
    operation_ids: list[str] = Field(min_length=1, max_length=500, alias="operationIds")
    start_date: date = Field(alias="startDate")
    daily_time: time = Field(alias="dailyTime")
    timezone_name: str = Field(min_length=1, max_length=100, alias="timezone")
    total_days: int = Field(ge=1, le=30, alias="totalDays")


class DripBatchActionInput(DripContract):
    account: Literal["JV", "XL"]
    expected_version: int = Field(ge=1, alias="expectedVersion")


class DripTriggerNowResponse(DripContract):
    batch_id: str = Field(alias="batchId")
    job_id: str | None = Field(alias="jobId")
    status: Literal["QUEUED", "NO_DUE_ITEMS", "REQUESTED", "ALREADY_REQUESTED", "NO_ELIGIBLE_ITEMS"]
    selected_count: int | None = Field(default=None, alias="selectedCount", ge=0)
    preparing_count: int | None = Field(default=None, alias="preparingCount", ge=0)
    ready_count: int | None = Field(default=None, alias="readyCount", ge=0)
    due_item_count: int = Field(alias="dueItemCount", ge=0)
    cutoff_at: datetime = Field(alias="cutoffAt")


class DripBatchDTO(DripContract):
    id: str
    account: Literal["JV", "XL"]
    period: Literal["3M", "12M"]
    name: str
    status: Literal["DRAFT", "SCHEDULED", "ACTIVE", "PAUSED", "COMPLETED", "CANCELLED"]
    item_count: int = Field(ge=0, alias="itemCount")
    created_at: datetime = Field(alias="createdAt")
    factory_id: str | None = Field(alias="factoryId")
    local_start_date: date | None = Field(alias="localStartDate")
    daily_time: str | None = Field(alias="dailyTime")
    timezone: str | None
    scheduled_start_at: datetime | None = Field(alias="scheduledStartAt")
    total_days: int | None = Field(alias="totalDays")
    version: int = Field(ge=1)
    started_at: datetime | None = Field(alias="startedAt")
    paused_at: datetime | None = Field(alias="pausedAt")
    cancelled_at: datetime | None = Field(alias="cancelledAt")
    item_counts: dict[str, int] | None = Field(default=None, alias="itemCounts")
    item_group_counts: dict[str, int] | None = Field(default=None, alias="itemGroupCounts")
    queue_item_counts: dict[str, int] | None = Field(default=None, alias="queueItemCounts")


class DripPageInfo(DripContract):
    page: int = Field(ge=1)
    page_size: int = Field(alias="pageSize", ge=1, le=200)
    total_items: int = Field(alias="totalItems", ge=0)
    total_pages: int = Field(alias="totalPages", ge=0)


class DripBatchListResponse(DripContract):
    items: list[DripBatchDTO]
    page: DripPageInfo


class DripBatchManifestItemDTO(DripContract):
    row_number: int = Field(alias="rowNumber", ge=1)
    candidate_episode_id: str = Field(alias="candidateEpisodeId")
    period: Literal["3M", "12M"]
    source_ean: str | None = Field(alias="sourceEan")
    source_sku: str | None = Field(alias="sourceSku")
    source_type: str = Field(alias="sourceType")
    factory_id: str | None = Field(alias="factoryId")
    factory_name: str | None = Field(alias="factoryName")
    title: str | None
    image_url: str | None = Field(alias="imageUrl")
    operation_id: str = Field(alias="operationId")
    operation_status: str = Field(alias="operationStatus")
    target_sku: str | None = Field(alias="targetSku")
    new_ean: str | None = Field(alias="newEan")
    category: str | None = None
    scheduled_at: datetime | None = Field(alias="scheduledAt")
    queue_status: str | None = Field(alias="queueStatus")
    queue_error_code: str | None = Field(alias="queueErrorCode")


class DripBatchManifestResponse(DripContract):
    items: list[DripBatchManifestItemDTO]
    page: DripPageInfo


class DripScheduleDistributionItem(DripContract):
    day_number: int = Field(alias="dayNumber", ge=1)
    scheduled_at: datetime = Field(alias="scheduledAt")
    operation_ids: list[str] = Field(alias="operationIds")
    count: int = Field(ge=0)


class DripSchedulePreview(DripContract):
    account: Literal["JV", "XL"]
    factory_id: str | None = Field(alias="factoryId")
    period: Literal["3M", "12M"]
    timezone: str
    total_days: int = Field(alias="totalDays")
    item_count: int = Field(alias="itemCount", ge=1)
    distribution: list[DripScheduleDistributionItem]
    external_services_called: list[Any] = Field(alias="externalServicesCalled")
    publication_enabled: Literal[False] = Field(alias="publicationEnabled")


class DripBatchItemDTO(DripContract):
    id: int
    batch_id: str = Field(alias="batchId")
    account: Literal["JV", "XL"]
    source_ean: str = Field(alias="sourceEan")
    row_number: int = Field(alias="rowNumber")
    operation_id: str = Field(alias="operationId")
    candidate_episode_id: str | None = Field(default=None, alias="candidateEpisodeId")
    revision_id: str | None = Field(alias="revisionId")
    qc_result_id: str | None = Field(alias="qcResultId")
    payload_hash: str | None = Field(alias="payloadHash", min_length=64, max_length=64)
    scheduled_at: datetime = Field(alias="scheduledAt")
    status: str
    queue_status: str | None = Field(default=None, alias="queueStatus")
    publication_state: str | None = Field(default=None, alias="publicationState")
    marketplace_observation_id: str | None = Field(default=None, alias="marketplaceObservationId")
    release_attempts: int = Field(alias="releaseAttempts", ge=0)
    last_error_code: str | None = Field(alias="lastErrorCode")
    ready_at: datetime | None = Field(alias="readyAt")
    target_sku: str | None = Field(alias="targetSku")
    source_sku: str | None = Field(alias="sourceSku")
    category: str | None = None
    revision_number: int | None = Field(alias="revisionNumber", ge=1)
    total_attempts: int = Field(alias="totalAttempts", ge=0)
    title: str | None = None
    image_url: str | None = Field(default=None, alias="imageUrl")
    source_type: str | None = Field(default=None, alias="sourceType")
    factory_name: str | None = Field(default=None, alias="factoryName")
    operation_status: str | None = Field(default=None, alias="operationStatus")
    submission_state: str | None = Field(default=None, alias="submissionState")
    online_status: str | None = Field(default=None, alias="onlineStatus")
    online_observed_at: datetime | None = Field(default=None, alias="onlineObservedAt")
    qc_outcome: str | None = Field(default=None, alias="qcOutcome")
    qc_issues: list[dict[str, Any]] = Field(default_factory=list, alias="qcIssues")
    qc_created_at: datetime | None = Field(default=None, alias="qcCreatedAt")
    preparation_errors: list[str | dict[str, Any]] = Field(default_factory=list, alias="preparationErrors")
    submission_status: str | None = Field(default=None, alias="submissionStatus")
    submission_http_status: int | None = Field(default=None, alias="submissionHttpStatus")
    submission_error_code: str | None = Field(default=None, alias="submissionErrorCode")
    submission_attempt_number: int | None = Field(default=None, alias="submissionAttemptNumber", ge=1)
    submission_started_at: datetime | None = Field(default=None, alias="submissionStartedAt")
    submission_completed_at: datetime | None = Field(default=None, alias="submissionCompletedAt")
    processing_state: str | None = Field(default=None, alias="processingState")
    task_state: str | None = Field(default=None, alias="taskState")
    processing_error_code: str | None = Field(default=None, alias="processingErrorCode")
    processing_updated_at: datetime | None = Field(default=None, alias="processingUpdatedAt")
    scheduled_day: int | None = Field(default=None, alias="scheduledDay")
    stock_status: str = Field(default="NOT_STARTED", alias="stockStatus")
    stock_verified: bool = Field(default=False, alias="stockVerified")
    delivery_status: str = Field(default="NOT_STARTED", alias="deliveryStatus")
    delivery_verified: bool = Field(default=False, alias="deliveryVerified")


class DripBatchItemsResponse(DripContract):
    items: list[DripBatchItemDTO]
    page: DripPageInfo


class DripPlanRequestInput(DripContract):
    account: Literal["JV", "XL"]
    name: str = Field(min_length=1, max_length=160)
    candidate_ids: list[str] | None = Field(default=None, alias="candidateIds", min_length=1, max_length=50000)
    filters: dict[str, Any] | None = None
    start_date: date = Field(alias="startDate")
    daily_time: time = Field(alias="dailyTime")
    timezone_name: str = Field(default="Europe/Berlin", alias="timezone")
    total_days: int = Field(alias="totalDays", ge=1, le=30)
    preview_hash: str | None = Field(default=None, alias="previewHash", pattern=r"^[a-f0-9]{64}$")


class DripPlanningDistributionItem(DripContract):
    day_number: int = Field(alias="dayNumber", ge=1)
    scheduled_at: datetime = Field(alias="scheduledAt")
    count: int = Field(ge=0)
    categories: dict[str, int]


class DripPlanningPreview(DripContract):
    account: Literal["JV", "XL"]
    item_count: int = Field(alias="itemCount", ge=1)
    ready_count: int = Field(alias="readyCount", ge=0)
    needs_correction_count: int = Field(alias="needsCorrectionCount", ge=0)
    group_count: int = Field(alias="groupCount", ge=1)
    average_per_day: float = Field(alias="averagePerDay", ge=0)
    first_run_at: datetime | None = Field(alias="firstRunAt")
    selection_hash: str = Field(alias="selectionHash", min_length=64, max_length=64)
    distribution: list[DripPlanningDistributionItem]
    external_services_called: list[Any] = Field(alias="externalServicesCalled")
