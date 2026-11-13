"use client";

import { useMemo, useState } from "react";
import type { FormEvent } from "react";
import {
  Activity,
  AlertCircle,
  Archive,
  ArrowDownToLine,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Clock3,
  Database,
  FileClock,
  FileText,
  Filter,
  Image as ImageIcon,
  Layers3,
  PackageCheck,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Truck,
  XCircle,
} from "lucide-react";
import { ApiState } from "@/components/api-state";
import { RestartApiClient } from "@/features/api/client";
import type {
  RestartAccount,
  RestartAvailabilityAction,
  RestartEvent,
  RestartLibraryLink,
  RestartLibraryLinkStatus,
} from "@/features/api/types";
import { useRestartTimezone } from "@/features/state/restart-timezone";
import { useTranslation } from "@/features/i18n";

const api = new RestartApiClient();

export interface LibraryFilters {
  status: string;
  factory: string;
  operation: string;
  period: string;
}

const EMPTY_FILTERS: LibraryFilters = { status: "all", factory: "all", operation: "all", period: "all" };

function publicationTone(item: RestartLibraryLink): string {
  if (item.onlineConfirmed) return "online";
  const state = `${item.publicationState} ${item.operationStatus ?? ""}`.toUpperCase();
  if (/FAILED|REJECT|ERROR|NEEDS_REVIEW|BLOCKED|RECONCILIATION_REQUIRED/.test(state)) return "failed";
  if (/DRAFT|NOT_PREPARED|NOT_STARTED/.test(state)) return "draft";
  if (/SUBMITTED|PROCESSING|AWAITING|QUEUED|IN_FLIGHT|UNKNOWN/.test(state)) return "pending";
  return "neutral";
}

function publicationLabel(item: RestartLibraryLink, t: (key: string) => string): string {
  if (item.onlineConfirmed) return t("library.stateOnline");
  const state = `${item.publicationState} ${item.operationStatus ?? ""}`.toUpperCase();
  if (/FAILED|REJECT|ERROR|NEEDS_REVIEW|BLOCKED|RECONCILIATION_REQUIRED/.test(state)) return t("library.stateNeedsAttention");
  if (/DRAFT|NOT_PREPARED|NOT_STARTED/.test(state)) return t("library.stateDraft");
  if (/SUBMITTED|PROCESSING|AWAITING|QUEUED|IN_FLIGHT|UNKNOWN/.test(state)) return t("library.stateProcessing");
  return item.publicationState || item.operationStatus || t("library.stateOther");
}

function linkStatusLabel(status: RestartLibraryLinkStatus, t: (key: string) => string): string {
  switch (status) {
    case "ACTIVE": return t("library.linkActive");
    case "PENDING_PUBLISH": return t("library.linkPending");
    case "PUBLISHED": return t("library.linkConfirmed");
    case "DEACTIVATED": return t("library.linkDeactivated");
  }
}

function safeImageUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function csvCell(value: unknown): string {
  let text = value == null ? "" : String(value);
  if (/^[\s\u0000-\u001f]*[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

export function exportLibraryLinks(items: RestartLibraryLink[], account: RestartAccount) {
  const columns: Array<[string, (item: RestartLibraryLink) => unknown]> = [
    ["Account", (item) => item.account],
    ["Product", (item) => item.title],
    ["Source type", (item) => item.sourceType],
    ["Factory", (item) => item.factoryName ?? item.factoryId],
    ["Source SKU", (item) => item.aftercoolSku],
    ["Source EAN", (item) => item.sourceEan],
    ["OTTO EAN", (item) => item.newEan],
    ["OTTO SKU", (item) => item.targetSku],
    ["Link status", (item) => item.linkStatus],
    ["Publication state", (item) => item.publicationState],
    ["Operation status", (item) => item.operationStatus],
    ["Operation ID", (item) => item.operationId],
    ["Attempt", (item) => item.attemptNumber],
    ["Revision", (item) => item.revisionNumber],
    ["Stock status", (item) => item.availability?.stock?.status],
    ["Stock target", (item) => item.availability?.stock?.target],
    ["Delivery status", (item) => item.availability?.delivery?.status],
    ["Online confirmed", (item) => item.onlineConfirmed ? "yes" : "no"],
    ["Created at", (item) => item.createdAt],
  ];
  const content = [
    columns.map(([label]) => csvCell(label)).join(","),
    ...items.map((item) => columns.map(([, value]) => csvCell(value(item))).join(",")),
  ].join("\r\n");
  const blob = new Blob(["\uFEFF", content], { type: "text/csv;charset=utf-8" });
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = `otto-library-${account.toLowerCase()}-${new Date().toISOString().slice(0, 10)}.csv`;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(href), 1000);
}

export function LibrarySummaryCards({
  items,
  linkedRecords,
  availableEans,
  eventCount,
  loading,
}: {
  items: RestartLibraryLink[];
  linkedRecords: number;
  availableEans: number;
  eventCount: number;
  loading: boolean;
}) {
  const { t } = useTranslation();
  const online = items.filter((item) => item.onlineConfirmed).length;
  const drafts = items.filter((item) => publicationTone(item) === "draft").length;
  const other = Math.max(items.length - online - drafts, 0);
  const total = Math.max(items.length, 1);
  const onlinePercent = (online / total) * 100;
  const draftPercent = (drafts / total) * 100;

  if (loading && !linkedRecords && !items.length) {
    return <div className="library-summary-grid" aria-label={t("library.loadingLibrary")}>
      {Array.from({ length: 4 }, (_, index) => <div className="library-kpi-skeleton" key={index}><i /><i /><i /></div>)}
    </div>;
  }

  return (
    <div className="library-summary-grid">
      <article className="library-summary-card library-kpi library-kpi-links">
        <div className="library-kpi-icon"><Layers3 size={19} /></div>
        <div className="library-kpi-copy"><span>{t("library.linkedRecords")}</span><strong>{linkedRecords.toLocaleString()}</strong><small>{t("library.linkedRecordsNote")}</small></div>
      </article>
      <article className="library-summary-card library-kpi library-kpi-pool">
        <div className="library-kpi-icon"><Database size={19} /></div>
        <div className="library-kpi-copy"><span>{t("library.poolAvailable")}</span><strong>{availableEans.toLocaleString()}</strong><small>{t("library.poolAvailableNote")}</small></div>
      </article>
      <article className="library-summary-card library-kpi library-kpi-events">
        <div className="library-kpi-icon"><Activity size={19} /></div>
        <div className="library-kpi-copy"><span>{t("library.productionEvents")}</span><strong>{eventCount.toLocaleString()}</strong><small>{t("library.productionEventsNote")}</small></div>
      </article>
      <article className="library-summary-card library-kpi library-kpi-states">
        <div className="library-kpi-status-title"><span>{t("library.recordStatuses")}</span><small>{t("library.statusesOnPage")}</small></div>
        <div className="library-status-summary">
          <div className="library-status-donut" style={{ background: `conic-gradient(#16a875 0 ${onlinePercent}%, #f59e0b ${onlinePercent}% ${onlinePercent + draftPercent}%, #65728b ${onlinePercent + draftPercent}% 100%)` }}>
            <span>{items.length}</span>
          </div>
          <div className="library-status-legend">
            <span><i className="online" />{t("library.stateOnline")}<b>{online}</b></span>
            <span><i className="draft" />{t("library.stateDraft")}<b>{drafts}</b></span>
            <span><i className="other" />{t("library.stateOther")}<b>{other}</b></span>
          </div>
        </div>
      </article>
    </div>
  );
}

export function LibraryLinksSkeleton() {
  return <div className="library-skeleton-table" aria-hidden="true">
    <div className="library-skeleton-head"><i /><i /><i /><i /><i /><i /><i /><i /></div>
    {Array.from({ length: 5 }, (_, index) => <div className="library-skeleton-row" key={index}><div><i /><i /></div><i /><i /><i /><i /><i /><i /><i /></div>)}
  </div>;
}

export function LibraryToolbar({
  onExport,
  onRefresh,
  onUpload,
  loading,
  visibleCount,
  showLinkFilters,
}: {
  onExport: () => void;
  onRefresh: () => void;
  onUpload: () => void;
  loading: boolean;
  visibleCount: number;
  showLinkFilters: boolean;
}) {
  const { t } = useTranslation();

  return <div className="library-toolbar">
    <div className="library-toolbar-heading">
      <h2>{t("library.cardTitle")}</h2>
      <p>{t("library.cardSubtitle")}</p>
    </div>
    <div className="library-toolbar-actions">
      {showLinkFilters ? <button type="button" className="library-secondary-button" disabled={!visibleCount} onClick={onExport} title={t("library.exportVisibleHint")}><ArrowDownToLine size={15} />{t("library.export")}</button> : null}
      <button type="button" className="library-icon-button" aria-label={t("library.refresh")} disabled={loading} onClick={onRefresh}><RefreshCw size={15} className={loading ? "spin" : ""} /></button>
      <button type="button" className="library-primary-button" onClick={onUpload}><Plus size={16} />{t("library.uploadPoolBtn")}</button>
    </div>
  </div>;
}

export function LibraryControls({
  account,
  search,
  onSearchChange,
  onClearSearch,
  onSearch,
  items,
  visibleCount,
  showLinkFilters,
  filters,
  onFiltersChange,
}: {
  account: RestartAccount;
  search: string;
  onSearchChange: (value: string) => void;
  onClearSearch: () => void;
  onSearch: (event: FormEvent<HTMLFormElement>) => void;
  items: RestartLibraryLink[];
  visibleCount: number;
  showLinkFilters: boolean;
  filters: LibraryFilters;
  onFiltersChange: (next: LibraryFilters) => void;
}) {
  const { t } = useTranslation();
  const factories = useMemo(() => {
    const values = new Map<string, string>();
    for (const item of items) values.set(item.factoryId ?? "", item.factoryName ?? item.factoryId ?? t("library.factoryUnknown"));
    return [...values.entries()];
  }, [items, t]);
  const statuses = useMemo(() => [...new Set(items.map((item) => item.publicationState || item.operationStatus || "DRAFT"))].sort(), [items]);
  const operations = useMemo(() => [...new Set(items.map((item) => item.operationStatus ?? "NO_OPERATION"))].sort(), [items]);
  const setFilter = (key: keyof LibraryFilters, value: string) => onFiltersChange({ ...filters, [key]: value });

  return <>
    <div className={`library-controls${showLinkFilters ? "" : " without-filters"}`}>
      <form className="library-search" onSubmit={onSearch}>
        <Search size={16} aria-hidden="true" />
        <input aria-label={t("library.searchPlaceholder")} placeholder={t("library.searchPlaceholder")} value={search} onChange={(event) => onSearchChange(event.target.value)} />
        {search ? <button type="button" aria-label={t("common.clear")} onClick={onClearSearch}><XCircle size={15} /></button> : null}
        <button type="submit" className="library-search-submit">{t("library.searchBtn")}</button>
      </form>
      {showLinkFilters ? <div className="library-filters" aria-label={t("library.quickFilters")}>
        <label><span>{t("library.filterStatus")}</span><select value={filters.status} onChange={(event) => setFilter("status", event.target.value)}><option value="all">{t("common.all")}</option>{statuses.map((status) => <option value={status} key={status}>{status}</option>)}</select></label>
        <label><span>{t("library.filterFactory")}</span><select value={filters.factory} onChange={(event) => setFilter("factory", event.target.value)}><option value="all">{t("common.all")}</option>{factories.map(([id, name]) => <option value={id} key={id || "unknown"}>{name}</option>)}</select></label>
        <label><span>{t("library.filterOperation")}</span><select value={filters.operation} onChange={(event) => setFilter("operation", event.target.value)}><option value="all">{t("common.all")}</option>{operations.map((status) => <option value={status} key={status}>{status === "NO_OPERATION" ? t("library.operationNotCreated") : status}</option>)}</select></label>
        <label><span>{t("library.filterPeriod")}</span><select value={filters.period} onChange={(event) => setFilter("period", event.target.value)}><option value="all">{t("library.periodAll")}</option><option value="7">{t("library.period7Days")}</option><option value="30">{t("library.period30Days")}</option><option value="90">{t("library.period90Days")}</option></select></label>
        <button type="button" className="library-reset-filters" onClick={() => onFiltersChange(EMPTY_FILTERS)}><Filter size={14} />{t("library.resetFilters")}</button>
      </div> : null}
    </div>
    {showLinkFilters ? <p className="library-filter-scope">{t("library.filterScope", { count: visibleCount, total: items.length, account })}</p> : null}
  </>;
}

function eventWhen(events: RestartEvent[], predicate: (event: RestartEvent) => boolean): RestartEvent | null {
  return events.find(predicate) ?? null;
}

type TimelineState = "done" | "current" | "pending" | "failed";
interface TimelineStep { id: string; label: string; state: TimelineState; at?: string | null; detail?: string }

function timelineSteps(item: RestartLibraryLink, events: RestartEvent[], t: (key: string) => string): TimelineStep[] {
  const reserved = eventWhen(events, (event) => event.eventType === "EAN_ASSIGNED");
  const prepared = eventWhen(events, (event) => /REVISION_CREATED|PREPARATION_COMPLETED|PAYLOAD_PREPARED/.test(event.eventType));
  const latestQc = eventWhen(events, (event) => /QC_COMPLETED/.test(event.eventType));
  const revisionId = String(prepared?.metadata?.revisionId ?? "");
  const qcRevisionId = String(latestQc?.metadata?.revisionId ?? "");
  const qc = latestQc && revisionId && qcRevisionId === revisionId ? latestQc : null;
  const submission = eventWhen(events, (event) => event.eventType === "OTTO_SUBMISSION_RESULT_RECORDED");
  const failedSubmission = eventWhen(events, (event) => /SUBMISSION.*(FAILED|REJECTED|NEEDS_REVIEW)|PROCESSING_NEEDS_REVIEW/.test(event.eventType));
  const qcPassed = Boolean(qc && /PASSED_AUTO|PASSED/.test(qc.summary));
  const submissionAttemptStatus = String(submission?.metadata?.attemptStatus ?? "").toUpperCase();
  const acceptedState = ["SUBMITTED_AWAITING_PROCESSING", "PROCESSING_CONFIRMED_AWAITING_ONLINE", "ONLINE_CONFIRMED"].includes(item.publicationState);
  const submissionDone = item.processingConfirmed || item.onlineConfirmed || acceptedState || submissionAttemptStatus === "ACCEPTED";
  const publicationFailed = Boolean(failedSubmission)
    || /FAILED|REJECTED|ERROR|NEEDS_REVIEW|BLOCKED|RECONCILIATION_REQUIRED/.test(`${item.operationStatus ?? ""} ${item.publicationState}`)
    || ["FAILED", "REJECTED"].includes(submissionAttemptStatus);
  const availabilityFailure = [item.availability?.stock?.status, item.availability?.delivery?.status].some((status) => status === "REJECTED" || status === "NEEDS_REVIEW");
  const availabilityDone = [item.availability?.stock, item.availability?.delivery].every((step) => step?.status === "ACCEPTED" || step?.manualResolution === "APPLIED");
  const published = item.processingConfirmed || /PROCESSING_CONFIRMED|PUBLISHED|AWAITING_ONLINE|ONLINE_CONFIRMED/.test(item.publicationState);
  const current = !prepared ? "preparation" : !qcPassed ? "qc" : !submissionDone ? "publication" : !item.onlineConfirmed ? "online" : !availabilityDone ? "availability" : "complete";
  return [
    { id: "lineage", label: t("library.timelineEanReserved"), state: "done", at: reserved?.createdAt ?? item.createdAt, detail: reserved?.summary },
    { id: "preparation", label: t("library.timelinePreparation"), state: prepared ? "done" : current === "preparation" ? "current" : "pending", at: prepared?.createdAt ?? null, detail: prepared?.eventType },
    { id: "qc", label: t("library.timelineQc"), state: qcPassed ? "done" : qc ? "failed" : current === "qc" ? "current" : "pending", at: qc?.createdAt ?? null, detail: qc?.summary },
    { id: "publication", label: t("library.timelinePublication"), state: publicationFailed ? "failed" : submissionDone ? "done" : current === "publication" ? "current" : "pending", at: submission?.createdAt ?? null, detail: submission?.summary },
    { id: "online", label: t("library.timelineOnline"), state: item.onlineConfirmed ? "done" : published ? "current" : current === "online" ? "current" : "pending", at: item.confirmedOnlineIdentifiers?.[0]?.checkedAt ?? item.latestMarketplaceObservation?.checkedAt ?? null, detail: item.latestMarketplaceObservation?.marketPlaceStatus ?? undefined },
    { id: "availability", label: t("library.timelineComplete"), state: availabilityFailure ? "failed" : availabilityDone && item.onlineConfirmed ? "done" : current === "availability" ? "current" : "pending", at: availabilityDone ? item.availability?.stock?.lastAttemptAt : null, detail: availabilityDone ? t("library.timelineAvailabilityDone") : undefined },
  ];
}

function StepIcon({ state }: { state: TimelineState }) {
  if (state === "done") return <Check size={14} />;
  if (state === "failed") return <XCircle size={14} />;
  if (state === "current") return <Clock3 size={13} />;
  return <Circle size={12} />;
}

function StatusBadge({ item, t }: { item: RestartLibraryLink; t: (key: string) => string }) {
  const tone = publicationTone(item);
  return <span className={`library-state-badge is-${tone}`}><i />{publicationLabel(item, t)}</span>;
}

function LinkStatusBadge({ status, t }: { status: RestartLibraryLinkStatus; t: (key: string) => string }) {
  const tone = status === "PUBLISHED" ? "published" : status === "DEACTIVATED" ? "deactivated" : status === "PENDING_PUBLISH" ? "pending" : "active";
  return <span className={`library-link-status is-${tone}`}><i />{linkStatusLabel(status, t)}</span>;
}

function AvailabilityCard({
  title,
  icon,
  value,
  formatDate,
  action,
  operationId,
  enabled,
  workingAction,
  onRetry,
  onResolve,
  t,
}: {
  title: string;
  icon: "stock" | "delivery";
  value: RestartLibraryLink["availability"]["stock"] | RestartLibraryLink["availability"]["delivery"] | undefined;
  formatDate: (value: string | null | undefined) => string;
  action: RestartAvailabilityAction;
  operationId: string | null;
  enabled: boolean;
  workingAction: string | null;
  onRetry: (operationId: string, action: RestartAvailabilityAction) => void;
  onResolve: (operationId: string, action: RestartAvailabilityAction) => void;
  t: (key: string, options?: any) => string;
}) {
  const status = value?.status ?? "NOT_STARTED";
  const workKey = operationId ? `${operationId}:${action}` : "";
  const stockTarget = icon === "stock" ? (value as RestartLibraryLink["availability"]["stock"] | undefined)?.target : null;
  const deliveryTarget = icon === "delivery" ? (value as RestartLibraryLink["availability"]["delivery"] | undefined)?.target : null;
  const statusLabel = status === "ACCEPTED" ? value?.manualResolution === "APPLIED" ? t("library.availabilityManualApplied") : t("library.availabilityAccepted")
    : status === "REJECTED" ? value?.manualResolution === "NOT_APPLIED" ? t("library.availabilityManualRejected") : t("library.availabilityRejected")
      : status === "UNKNOWN" ? t("library.availabilityUnknown")
        : status === "NEEDS_REVIEW" ? t("library.availabilityNeedsReview")
          : status === "DISPATCHING" ? t("library.availabilityDispatching")
            : status === "PREPARED" ? t("library.availabilityQueued") : t("library.availabilityNotStarted");
  return <article className="library-detail-card">
    <div className="library-detail-card-title">{icon === "stock" ? <PackageCheck size={15} /> : <Truck size={15} />}<strong>{title}</strong><span className={`library-mini-status is-${status.toLowerCase()}`}>{statusLabel}</span></div>
    {icon === "stock" ? <p>{t("library.targetQuantity")}: <b>{stockTarget ?? "—"}</b></p> : <p>{t("library.shippingProfile")}: <b>{deliveryTarget?.shippingProfileName ?? deliveryTarget?.shippingProfile ?? "—"}</b></p>}
    {icon === "delivery" ? <p>{t("library.processingTime")}: <b>{deliveryTarget?.processingTime ? `${deliveryTarget.processingTime} ${t("library.days")}` : "—"}</b></p> : null}
    {icon === "delivery" && deliveryTarget?.classificationBasis ? <p>{t("library.originBasis")}: <b>{deliveryTarget.classificationBasis}</b></p> : null}
    <p>{t("library.attempts")}: <b>{value?.attemptCount ?? 0}</b><span> · </span>{value?.lastAttemptAt ? formatDate(value.lastAttemptAt) : t("library.notSent")}</p>
    {value?.httpStatus ? <p>HTTP <b>{value.httpStatus}</b></p> : null}
    {value?.errorCode ? <p className="library-detail-error"><AlertCircle size={13} />{value.errorCode}</p> : null}
    {operationId && status === "UNKNOWN" ? <button type="button" className="library-secondary-button" disabled={Boolean(workingAction)} onClick={() => onResolve(operationId, action)}>{t("library.resolveUnknown")}</button> : null}
    {operationId && (status === "REJECTED" || status === "NEEDS_REVIEW") ? <button type="button" className="library-secondary-button" disabled={!enabled || Boolean(workingAction)} title={!enabled ? t("library.retryDisabled") : undefined} onClick={() => onRetry(operationId, action)}>{workingAction === workKey ? t("library.retryPending") : status === "NEEDS_REVIEW" ? t("library.retryQueueFailure") : t("library.retryRejected")}</button> : null}
  </article>;
}

export function LibraryLinksTable({
  items,
  account,
  availabilityWriteEnabled,
  workingAction,
  onRetryAvailability,
  onResolveAvailability,
  onShowOperationHistory,
  emptyTitle,
  emptyDescription,
  onClearFilters,
}: {
  items: RestartLibraryLink[];
  account: RestartAccount;
  availabilityWriteEnabled: boolean;
  workingAction: string | null;
  onRetryAvailability: (operationId: string, action: RestartAvailabilityAction) => void;
  onResolveAvailability: (operationId: string, action: RestartAvailabilityAction) => void;
  onShowOperationHistory: (operationId: string) => void;
  emptyTitle: string;
  emptyDescription: string;
  onClearFilters: () => void;
}) {
  const { t } = useTranslation();
  const { formatDate } = useRestartTimezone();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [eventCache, setEventCache] = useState<Record<string, RestartEvent[]>>({});
  const [loadingEventsFor, setLoadingEventsFor] = useState<string | null>(null);
  const [eventErrors, setEventErrors] = useState<Record<string, string>>({});

  async function toggleDetails(item: RestartLibraryLink) {
    const cacheKey = `${account}:${item.id}`;
    if (expandedId === cacheKey) {
      setExpandedId(null);
      return;
    }
    setExpandedId(cacheKey);
    if (eventCache[cacheKey] || loadingEventsFor === cacheKey) return;
    setLoadingEventsFor(cacheKey);
    setEventErrors((previous) => ({ ...previous, [cacheKey]: "" }));
    try {
      const queries = [...new Set([item.operationId, item.id].filter((value): value is string => Boolean(value)))];
      const pages = await Promise.all(queries.map((query) => api.listEvents(account, { page: 1, pageSize: 50, query })));
      const uniqueEvents = new Map<string, RestartEvent>();
      for (const page of pages) for (const event of page.items) uniqueEvents.set(event.id, event);
      const orderedEvents = [...uniqueEvents.values()].sort((left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt));
      setEventCache((previous) => ({ ...previous, [cacheKey]: orderedEvents }));
    } catch (cause) {
      setEventErrors((previous) => ({ ...previous, [cacheKey]: cause instanceof Error ? cause.message : t("library.eventsLoadFailed") }));
    } finally {
      setLoadingEventsFor((current) => current === cacheKey ? null : current);
    }
  }

  if (!items.length) {
    return <div className="library-empty-results"><ApiState kind="empty" title={emptyTitle} description={emptyDescription} /><button type="button" className="library-secondary-button" onClick={onClearFilters}>{t("library.resetFilters")}</button></div>;
  }

  return <div className="restart-table-wrap library-table-wrap"><table className="restart-table library-table library-operations-table">
    <thead><tr><th>{t("library.colSourceProduct")}</th><th>{t("library.colFactory")}</th><th>{t("library.colSourceEan")}</th><th>{t("library.colTargetEan")}</th><th>{t("library.colOperationRevision")}</th><th>{t("library.colInitialAvailability")}</th><th>{t("library.colOnlineEvidence")}</th><th><span className="library-visually-hidden">{t("library.details")}</span></th></tr></thead>
    <tbody>{items.map((item) => {
      const cacheKey = `${account}:${item.id}`;
      const isExpanded = expandedId === cacheKey;
      const imageUrl = safeImageUrl(item.imageUrl);
      const events = eventCache[cacheKey] ?? [];
      const steps = timelineSteps(item, events, t);
      const confirmed = item.confirmedOnlineIdentifiers?.[0];
      const latest = item.latestMarketplaceObservation;
      const stock = item.availability?.stock;
      const delivery = item.availability?.delivery;
      const onlineLabel = confirmed ? t("library.evidenceConfirmed") : latest ? t("library.evidencePending") : t("library.evidenceMissing");
      return <LibraryLinkRows key={item.id} item={item} isExpanded={isExpanded} imageUrl={imageUrl} events={events} steps={steps} confirmed={confirmed} latest={latest} stock={stock} delivery={delivery} onlineLabel={onlineLabel} loadingEvents={loadingEventsFor === cacheKey} eventError={eventErrors[cacheKey]} formatDate={formatDate} onToggle={() => void toggleDetails(item)} availabilityWriteEnabled={availabilityWriteEnabled} workingAction={workingAction} onRetryAvailability={onRetryAvailability} onResolveAvailability={onResolveAvailability} onShowOperationHistory={onShowOperationHistory} t={t} />;
    })}</tbody>
  </table></div>;
}

function LibraryLinkRows({
  item,
  isExpanded,
  imageUrl,
  events,
  steps,
  confirmed,
  latest,
  stock,
  delivery,
  onlineLabel,
  loadingEvents,
  eventError,
  formatDate,
  onToggle,
  availabilityWriteEnabled,
  workingAction,
  onRetryAvailability,
  onResolveAvailability,
  onShowOperationHistory,
  t,
}: {
  item: RestartLibraryLink;
  isExpanded: boolean;
  imageUrl: string | null;
  events: RestartEvent[];
  steps: TimelineStep[];
  confirmed: RestartLibraryLink["confirmedOnlineIdentifiers"][number] | undefined;
  latest: RestartLibraryLink["latestMarketplaceObservation"];
  stock: RestartLibraryLink["availability"]["stock"] | undefined;
  delivery: RestartLibraryLink["availability"]["delivery"] | undefined;
  onlineLabel: string;
  loadingEvents: boolean;
  eventError: string | undefined;
  formatDate: (value: string | null | undefined) => string;
  onToggle: () => void;
  availabilityWriteEnabled: boolean;
  workingAction: string | null;
  onRetryAvailability: (operationId: string, action: RestartAvailabilityAction) => void;
  onResolveAvailability: (operationId: string, action: RestartAvailabilityAction) => void;
  onShowOperationHistory: (operationId: string) => void;
  t: (key: string, options?: any) => string;
}) {
  const timelineStateText: Record<TimelineState, string> = { done: t("library.stepDone"), current: t("library.stepCurrent"), pending: t("library.stepPending"), failed: t("library.stepFailed") };
  const [failedImageUrl, setFailedImageUrl] = useState<string | null>(null);
  return <>
    <tr className={`library-product-row${isExpanded ? " is-expanded" : ""}`}>
      <td><div className="library-product-cell">
        <div className="library-thumbnail">{imageUrl && imageUrl !== failedImageUrl ? <img src={imageUrl} alt="" loading="lazy" onError={() => setFailedImageUrl(imageUrl)} /> : <ImageIcon size={19} aria-hidden="true" />}</div>
        <div className="library-product-copy"><strong title={item.title ?? undefined}>{item.title || item.aftercoolProductId || item.sourceProductId}</strong><small>{item.account} · {item.sourceType} · SKU {item.aftercoolSku ?? "—"}</small></div>
      </div></td>
      <td><span className="library-secondary-value">{item.factoryName ?? item.factoryId ?? "—"}</span></td>
      <td><code className="library-ean-value" title={item.sourceEan}>{item.sourceEan}</code></td>
      <td><code className="library-ean-value is-target" title={item.newEan}>{item.newEan}</code><small className="library-cell-subline">SKU {item.targetSku ?? t("library.targetSkuNotConfirmed")}</small></td>
      <td><StatusBadge item={item} t={t} /><small className="library-cell-subline">{t("library.attemptNumber")} {item.attemptNumber ?? "—"} · {t("library.revisionNumber")} {item.revisionNumber ?? "—"}</small><small className="library-cell-subline library-mono-subline" title={item.operationId ?? undefined}>{item.operationId ? `#${item.operationId.slice(0, 8)}` : t("library.operationNotCreated")}</small></td>
      <td><div className="library-availability-summary"><span><PackageCheck size={13} />{t("library.stock")}: {stock?.status ?? "—"}</span><span><Truck size={13} />{t("library.delivery")}: {delivery?.status ?? "—"}</span></div></td>
      <td><span className={`library-evidence-badge is-${confirmed ? "confirmed" : latest ? "pending" : "missing"}`}>{confirmed ? <CheckCircle2 size={14} /> : latest ? <Clock3 size={14} /> : <AlertCircle size={14} />}{onlineLabel}</span>{confirmed ? <small className="library-cell-subline">{formatDate(confirmed.checkedAt)}</small> : latest ? <small className="library-cell-subline">{latest.marketPlaceStatus ?? t("library.marketplaceStatusMissing")} · {latest.identityVerified ? t("library.skuConfirmed") : t("library.skuMismatch")}</small> : null}</td>
      <td><button type="button" className="library-expand-button" aria-expanded={isExpanded} aria-label={`${isExpanded ? t("library.collapseDetails") : t("library.expandDetails")}: ${item.title ?? item.newEan}`} onClick={onToggle}>{isExpanded ? <ChevronDown size={17} /> : <ChevronRight size={17} />}</button></td>
    </tr>
    {isExpanded ? <tr className="library-details-row"><td colSpan={8}>
      <div className="library-details-panel">
        <section className="library-timeline-section">
          <div className="library-details-heading"><div><FileClock size={16} /><h3>{t("library.operationTimeline")}</h3></div><span>{events.length} {t("library.timelineEvents")}</span></div>
          {loadingEvents ? <div className="library-inline-loading"><i />{t("library.loadingEvents")}</div> : null}
          {eventError ? <div className="library-inline-error" role="alert"><AlertCircle size={14} />{eventError}</div> : null}
          <ol className="library-timeline">{steps.map((step) => <li key={step.id} className={`is-${step.state}`}>
            <span className="library-timeline-marker" aria-label={timelineStateText[step.state]}><StepIcon state={step.state} /></span>
            <div><strong>{step.label}</strong><small>{step.at ? formatDate(step.at) : step.state === "current" ? timelineStateText.current : timelineStateText[step.state]}</small>{step.detail ? <small className="library-timeline-detail">{step.detail}</small> : null}</div>
          </li>)}</ol>
          {events.length ? <details className="library-event-disclosure"><summary>{t("library.rawEvents", { count: events.length })}<ChevronDown size={14} /></summary><div>{events.map((event) => <article key={event.id}><span className="library-event-dot" /><div><strong>{event.summary}</strong><small>{event.eventType} · {event.entityType} {event.entityId}</small></div><time>{formatDate(event.createdAt)}</time></article>)}</div></details> : null}
        </section>
        <aside className="library-revision-section">
          <div className="library-details-heading"><div><Layers3 size={16} /><h3>{t("library.revisionInformation")}</h3></div></div>
          <dl className="library-revision-list">
            <div><dt><FileText size={14} />{t("library.revisionNumber")}</dt><dd>{item.revisionNumber ?? "—"}</dd></div>
            <div><dt><Activity size={14} />{t("library.operationLabel")}</dt><dd title={item.operationId ?? undefined}>{item.operationId ?? t("library.operationNotCreated")}</dd></div>
            <div><dt><Clock3 size={14} />{t("library.updatedAt")}</dt><dd>{formatDate(events[0]?.createdAt ?? item.createdAt)}</dd></div>
            <div><dt><ShieldCheck size={14} />{t("library.recordStatus")}</dt><dd><LinkStatusBadge status={item.linkStatus} t={t} /></dd></div>
            <div><dt><Database size={14} />{t("library.sourceRecord")}</dt><dd title={item.sourceProductId}>{item.sourceProductId}</dd></div>
          </dl>
          <div className="library-availability-details">
            <AvailabilityCard title={t("library.stockStep")} icon="stock" value={stock} formatDate={formatDate} action="QUANTITY" operationId={item.operationId} enabled={availabilityWriteEnabled} workingAction={workingAction} onRetry={onRetryAvailability} onResolve={onResolveAvailability} t={t} />
            <AvailabilityCard title={t("library.deliveryStep")} icon="delivery" value={delivery} formatDate={formatDate} action="DELIVERY_INFORMATION" operationId={item.operationId} enabled={availabilityWriteEnabled} workingAction={workingAction} onRetry={onRetryAvailability} onResolve={onResolveAvailability} t={t} />
          </div>
          {item.operationId ? <button type="button" className="library-history-link" onClick={() => onShowOperationHistory(item.operationId!)}><Archive size={14} />{t("library.openFullHistory")}</button> : null}
        </aside>
      </div>
    </td></tr> : null}
  </>;
}

export function filterLibraryLinks(items: RestartLibraryLink[], filters: LibraryFilters): RestartLibraryLink[] {
  const cutoff = filters.period === "all" ? null : Date.now() - Number(filters.period) * 24 * 60 * 60 * 1000;
  return items.filter((item) => {
    const status = item.publicationState || item.operationStatus || "DRAFT";
    const operation = item.operationStatus ?? "NO_OPERATION";
    const created = Date.parse(item.createdAt);
    return (filters.status === "all" || status === filters.status)
      && (filters.factory === "all" || (item.factoryId ?? "") === filters.factory)
      && (filters.operation === "all" || operation === filters.operation)
      && (cutoff === null || (!Number.isNaN(created) && created >= cutoff));
  });
}

export const defaultLibraryFilters = EMPTY_FILTERS;
