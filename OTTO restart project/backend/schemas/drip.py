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


class DripPageInfo(DripContract):
    page: int = Field(ge=1)
    page_size: int = Field(alias="pageSize", ge=1, le=200)
    total_items: int = Field(alias="totalItems", ge=0)
    total_pages: int = Field(alias="totalPages", ge=0)


class DripBatchListResponse(DripContract):
    items: list[DripBatchDTO]
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
    revision_id: str = Field(alias="revisionId")
    qc_result_id: str = Field(alias="qcResultId")
    payload_hash: str = Field(alias="payloadHash", min_length=64, max_length=64)
    scheduled_at: datetime = Field(alias="scheduledAt")
    status: str
    release_attempts: int = Field(alias="releaseAttempts", ge=0)
    last_error_code: str | None = Field(alias="lastErrorCode")
    ready_at: datetime | None = Field(alias="readyAt")
    target_sku: str | None = Field(alias="targetSku")
    source_sku: str | None = Field(alias="sourceSku")
    revision_number: int = Field(alias="revisionNumber", ge=1)
    total_attempts: int = Field(alias="totalAttempts", ge=0)


class DripBatchItemsResponse(DripContract):
    items: list[DripBatchItemDTO]
    page: DripPageInfo
