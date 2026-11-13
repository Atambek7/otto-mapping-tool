"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  Clock,
  Database,
  FileText,
  Layers,
  ShieldCheck,
  Tag,
} from "lucide-react";
import {
  defaultLibraryFilters,
  exportLibraryLinks,
  filterLibraryLinks,
  LibraryControls,
  LibraryLinksSkeleton,
  LibraryLinksTable,
  LibrarySummaryCards,
  LibraryToolbar,
  type LibraryFilters,
} from "@/components/catalogs/library-operations";
import { ApiState } from "@/components/api-state";
import { useRestartAccount } from "@/features/state/restart-account";
import { useRestartTimezone } from "@/features/state/restart-timezone";
import { useTranslation } from "@/features/i18n";
import { RestartApiClient, RestartApiError } from "@/features/api/client";
import type {
  RestartEanEntry,
  RestartAvailabilityAction,
  RestartEvent,
  RestartLibraryLink,
  RestartPageInfo,
} from "@/features/api/types";

const api = new RestartApiClient();
type LibraryTab = "links" | "pool" | "history";

function getEventIcon(eventType: string) {
  switch (eventType) {
    case "EAN_POOL_IMPORTED":
      return <Database size={16} />;
    case "EAN_ASSIGNED":
      return <Tag size={16} />;
    case "RESTART_QC_COMPLETED":
      return <ShieldCheck size={16} />;
    case "RESTART_REVISION_CREATED":
      return <FileText size={16} />;
    case "DRIP_BATCH_DRAFT_CREATED":
    case "DRIP_DISPATCHED":
      return <Layers size={16} />;
    default:
      return <Activity size={16} />;
  }
}

function getPoolStatusLabel(status: string | undefined, t: (key: string) => string): string {
  switch (status?.toUpperCase()) {
    case "AVAILABLE":
      return t("library.statusAvailable");
    case "RESERVED":
      return t("library.statusReserved");
    case "USED":
      return t("library.statusUsed");
    case "QUARANTINED":
      return t("library.statusQuarantined");
    case "EXHAUSTED":
      return t("library.statusExhausted");
    default:
      return status ?? "—";
  }
}

function getEligibilityLabel(eligibility: string | undefined, t: (key: string) => string): string {
  switch (eligibility?.toUpperCase()) {
    case "ELIGIBLE":
      return t("library.eligibilityEligible");
    case "INELIGIBLE":
      return t("library.eligibilityIneligible");
    case "UNVERIFIED":
      return t("library.eligibilityUnverified");
    case "EXEMPT":
      return t("library.eligibilityExempt");
    default:
      return eligibility ?? "—";
  }
}

function getProvenanceLabel(provenance: string | undefined, t: (key: string) => string): string {
  switch (provenance?.toUpperCase()) {
    case "MANUAL_IMPORT":
      return t("library.provenanceManualImport");
    case "GS1_CERTIFICATE":
      return t("library.provenanceGs1Certificate");
    case "OPERATOR_POOL":
      return t("library.provenanceOperatorPool");
    case "EXTERNAL_SYNC":
      return t("library.provenanceExternalSync");
    case "LEGACY_UNVERIFIED":
      return t("library.provenanceLegacyUnverified");
    default:
      return provenance ?? "—";
  }
}

function formatEventMeta(event: RestartEvent, t: (key: string, opts?: any) => string): string {
  if (event.eventType === "EAN_POOL_IMPORTED") {
    const count = (event.metadata?.count as number) ?? 5;
    const rawStatus = (event.metadata?.status as string) ?? "AVAILABLE";
    const status = getPoolStatusLabel(rawStatus, t);
    return t("library.sublinePoolImported", { count, status });
  }
  if (event.eventType === "EAN_ASSIGNED") {
    return t("library.sublineLinkCreated");
  }
  if (event.eventType.startsWith("RESTART_QC")) {
    return t("library.sublineQcCompleted");
  }
  if (event.eventType.startsWith("DRIP_")) {
    return t("library.sublineDripDispatched");
  }
  if (event.eventType.startsWith("OTTO_AVAILABILITY_")) {
    const action = event.metadata?.action === "QUANTITY" ? t("library.stock")
      : event.metadata?.action === "DELIVERY_INFORMATION" ? t("library.delivery") : "";
    const resolution = event.metadata?.resolution === "APPLIED" ? t("library.resolutionApplied")
      : event.metadata?.resolution === "NOT_APPLIED" ? t("library.resolutionNotApplied") : "";
    const details = [action, resolution].filter(Boolean);
    if (event.metadata?.retryNumber) details.push(t("library.retryNumber", { count: event.metadata.retryNumber }));
    if (event.metadata?.httpStatus) details.push(`HTTP ${event.metadata.httpStatus}`);
    if (event.metadata?.previousHttpStatus) details.push(`HTTP ${event.metadata.previousHttpStatus}`);
    if (event.metadata?.errorCode) details.push(String(event.metadata.errorCode));
    if (event.metadata?.previousErrorCode) details.push(String(event.metadata.previousErrorCode));
    if (event.metadata?.evidenceReference) details.push(`${t("library.evidence")}: ${String(event.metadata.evidenceReference).slice(0, 160)}`);
    if (event.metadata?.reason) details.push(String(event.metadata.reason).slice(0, 240));
    return details.join(" · ") || `${event.entityType} · ${event.entityId ?? ""}`;
  }
  if (event.entityId && !event.entityId.includes("T")) {
    return `${event.entityType} · ID: ${event.entityId} · Scope: PRODUCTION`;
  }
  return `${event.entityType} · Scope: PRODUCTION`;
}

function formatEventSummary(event: RestartEvent, t: (key: string, opts?: any) => string): string {
  if (event.eventType === "EAN_POOL_IMPORTED") {
    const count = (event.metadata?.count as number) ?? 5;
    return t("library.eventAddedEanPool", { count });
  }
  return event.summary;
}

function PoolPage({ items }: { items: RestartEanEntry[] }) {
  const { formatDate } = useRestartTimezone();
  const { t } = useTranslation();
  return items.length ? (
    <div className="restart-table-wrap library-table-wrap">
      <table className="restart-table library-table">
        <thead><tr><th>{t("library.colEanId")}</th><th>{t("library.colPoolStatus")}</th><th>{t("library.colEligibility")}</th><th>{t("library.colProvenance")}</th><th>{t("library.colVerification")}</th></tr></thead>
        <tbody>{items.map((item) => (
          <tr key={item.ean}>
            <td className="mono-value tabular-nums">{item.ean}</td>
            <td><span className="status-pill">{getPoolStatusLabel(item.status, t)}</span></td>
            <td>{getEligibilityLabel(item.eligibilityStatus, t)}</td>
            <td>{getProvenanceLabel(item.provenanceSource, t)}</td>
            <td className="tabular-nums">{formatDate(item.verifiedAt)}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  ) : <ApiState kind="empty" title={t("library.emptyPool")} description={t("library.emptyPoolDesc")} />;
}

function HistoryPage({ items }: { items: RestartEvent[] }) {
  const { formatEventTime } = useRestartTimezone();
  const { t } = useTranslation();
  return items.length ? (
    <div className="library-history-container">
      {items.map((event) => {
        const timeInfo = formatEventTime(event.createdAt);
        return (
          <div key={event.id} className="library-history-item">
            <div className="library-history-left">
              <div className="library-history-icon-box">
                {getEventIcon(event.eventType)}
              </div>
              <div className="library-history-details">
                <div className="library-history-headline">
                  <span className="library-history-title">{formatEventSummary(event, t)}</span>
                  <span className="library-history-type-pill">{event.eventType}</span>
                </div>
                <div className="library-history-meta">
                  {formatEventMeta(event, t)}
                </div>
              </div>
            </div>
            <div className="library-history-time-col">
              <div className="library-history-time-badge" title={timeInfo.full}>
                <Clock size={13} className="library-history-clock-icon" />
                <span className="library-history-date">{timeInfo.date}</span>
                <span className="library-history-dot">·</span>
                <span className="library-history-clock">{timeInfo.time}</span>
                <span className="library-history-tz">{timeInfo.tzBadge}</span>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  ) : (
    <ApiState kind="empty" title={t("library.emptyHistory")} description={t("library.emptyHistoryDesc")} />
  );
}

export function ProductionLibraryView() {
  const { account, ready } = useRestartAccount();
  const { t } = useTranslation();
  const [tab, setTab] = useState<LibraryTab>("links");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<LibraryFilters>(defaultLibraryFilters);
  const [page, setPage] = useState(1);
  const [links, setLinks] = useState<RestartLibraryLink[]>([]);
  const [pool, setPool] = useState<RestartEanEntry[]>([]);
  const [events, setEvents] = useState<RestartEvent[]>([]);
  const [linkPage, setLinkPage] = useState<RestartPageInfo | null>(null);
  const [poolPage, setPoolPage] = useState<RestartPageInfo | null>(null);
  const [eventPage, setEventPage] = useState<RestartPageInfo | null>(null);
  const [linkSummary, setLinkSummary] = useState({ linkedRecords: 0, poolAvailable: 0, poolTotal: 0, eventsCount: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [availabilityWriteEnabled, setAvailabilityWriteEnabled] = useState(false);
  const [workingAction, setWorkingAction] = useState<string | null>(null);
  const [actionFeedback, setActionFeedback] = useState<{ kind: "success" | "error"; message: string } | null>(null);
  const [resolutionTarget, setResolutionTarget] = useState<{ operationId: string; action: RestartAvailabilityAction } | null>(null);
  const [resolution, setResolution] = useState<"APPLIED" | "NOT_APPLIED">("APPLIED");
  const [evidenceReference, setEvidenceReference] = useState("");
  const [resolutionReason, setResolutionReason] = useState("");

  // EAN Pool Import Modal State
  const [isImportOpen, setIsImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [isImporting, setIsImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);

  async function handleImportEans() {
    const rawLines = importText.split(/[\r\n,;\s]+/).map((s) => s.trim()).filter(Boolean);
    if (!rawLines.length) {
      setImportError("Введите хотя бы один EAN-13.");
      return;
    }
    setIsImporting(true);
    setImportError(null);
    try {
      await api.importEanPool(account, rawLines);
      setImportText("");
      setIsImportOpen(false);
      setReload((v) => v + 1);
    } catch (err: unknown) {
      setImportError(err instanceof Error ? err.message : "Не удалось импортировать EAN в пул.");
    } finally {
      setIsImporting(false);
    }
  }

  const load = useCallback(async (signal: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      const [linkResult, poolResult, eventResult] = await Promise.all([
        api.listLibraryLinks(account, { page, pageSize: 50, query, signal }),
        api.listEanPool(account, { page, pageSize: 50, query, signal }),
        api.listEvents(account, { page, pageSize: 50, query, signal }),
      ]);
      setLinks(linkResult.items);
      setLinkPage(linkResult.page);
      setLinkSummary(linkResult.summary);
      setPool(poolResult.items);
      setPoolPage(poolResult.page);
      setEvents(eventResult.items);
      setEventPage(eventResult.page);
      void api.getSettings(account, signal).then((settings) => {
        if (!signal.aborted) setAvailabilityWriteEnabled(settings.publication.availabilityWriteEnabled);
      }).catch(() => {
        if (!signal.aborted) setAvailabilityWriteEnabled(false);
      });
    } catch (cause) {
      if (signal.aborted) return;
      setLinks([]);
      setPool([]);
      setEvents([]);
      setError(cause instanceof RestartApiError ? `${cause.code}: ${cause.message}` : cause instanceof Error ? cause.message : "Не удалось загрузить OttoRestart Library.");
    } finally {
      if (!signal.aborted) setLoading(false);
    }
  }, [account, page, query]);

  async function handleRetryAvailability(operationId: string, action: RestartAvailabilityAction) {
    const key = `${operationId}:${action}`;
    if (workingAction) return;
    setWorkingAction(key);
    setActionFeedback(null);
    try {
      const requestId = globalThis.crypto?.randomUUID?.();
      if (!requestId) throw new Error(t("library.secureKeyUnavailable"));
      const result = await api.retryAvailability(account, operationId, action, `availability-retry-${requestId}`);
      setActionFeedback({ kind: "success", message: t("library.retryQueued", { count: result.retryNumber }) });
      setReload((value) => value + 1);
    } catch (cause) {
      setActionFeedback({ kind: "error", message: cause instanceof Error ? cause.message : t("library.retryFailed") });
    } finally {
      setWorkingAction(null);
    }
  }

  async function handleResolveAvailability(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!resolutionTarget || workingAction) return;
    const { operationId, action } = resolutionTarget;
    const key = `${operationId}:${action}`;
    setWorkingAction(key);
    setActionFeedback(null);
    try {
      const requestId = globalThis.crypto?.randomUUID?.();
      if (!requestId) throw new Error(t("library.secureKeyUnavailable"));
      await api.resolveAvailability(account, operationId, action, resolution, evidenceReference.trim(), resolutionReason.trim(), `availability-resolution-${requestId}`);
      setResolutionTarget(null);
      setEvidenceReference("");
      setResolutionReason("");
      setActionFeedback({ kind: "success", message: t("library.resolutionSaved") });
      setReload((value) => value + 1);
    } catch (cause) {
      setActionFeedback({ kind: "error", message: cause instanceof Error ? cause.message : t("library.resolutionFailed") });
    } finally {
      setWorkingAction(null);
    }
  }

  function showOperationHistory(operationId: string) {
    setSearch(operationId);
    setQuery(operationId);
    setPage(1);
    setTab("history");
  }

  useEffect(() => {
    if (!ready) return;
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [ready, load, reload]);

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPage(1);
    setQuery(search.trim());
  }

  const tabs: { id: LibraryTab; label: string }[] = [
    { id: "links", label: t("library.tabLinks") },
    { id: "pool", label: t("library.tabPool") },
    { id: "history", label: t("library.tabHistory") },
  ];

  const currentPage = tab === "links" ? linkPage : tab === "pool" ? poolPage : eventPage;
  const visibleLinks = useMemo(() => filterLibraryLinks(links, filters), [links, filters]);
  const hasActiveFilters = Object.values(filters).some((value) => value !== "all");
  const tabCounts = {
    links: linkPage?.totalItems ?? linkSummary.linkedRecords,
    pool: poolPage?.totalItems ?? linkSummary.poolTotal,
    history: eventPage?.totalItems ?? linkSummary.eventsCount,
  };

  return (
    <section className="page-section library-page library-production-workspace">
      <div className="page-heading library-page-heading"><div className="library-page-title"><span><Layers size={20} /></span><div><h1>{t("library.title")}</h1><p>{t("library.subtitle", { account })}</p></div></div></div>
      <LibrarySummaryCards items={links} linkedRecords={linkPage?.totalItems ?? linkSummary.linkedRecords} availableEans={linkSummary.poolAvailable} eventCount={linkSummary.eventsCount} loading={loading} />
      <section className="surface-card library-card">
        <LibraryToolbar onExport={() => exportLibraryLinks(visibleLinks, account)} onRefresh={() => setReload((value) => value + 1)} onUpload={() => setIsImportOpen(true)} loading={loading} visibleCount={visibleLinks.length} showLinkFilters={tab === "links"} />
        <div className="library-tabs" role="tablist" aria-label={t("library.title")}>
          {tabs.map((value) => <button type="button" role="tab" aria-selected={tab === value.id} key={value.id} onClick={() => { setTab(value.id); setPage(1); }}>
            {value.label}<span className="tabular-nums">{tabCounts[value.id]}</span>
          </button>)}
        </div>
        <LibraryControls account={account} search={search} onSearchChange={setSearch} onClearSearch={() => { setSearch(""); setQuery(""); setPage(1); }} onSearch={submitSearch} items={links} visibleCount={visibleLinks.length} showLinkFilters={tab === "links"} filters={filters} onFiltersChange={setFilters} />
        {loading ? tab === "links" ? <LibraryLinksSkeleton /> : <ApiState kind="loading" label={t("library.loadingLibrary")} /> : null}
        {error ? <ApiState kind="error" title={t("library.errorUnavailable")} description={error} onRetry={() => setReload((value) => value + 1)} /> : null}
        {!loading && !error ? <>
          {actionFeedback && !resolutionTarget ? <div className={`library-action-feedback ${actionFeedback.kind}`} role="status">{actionFeedback.message}</div> : null}
          {tab === "links" ? <LibraryLinksTable key={`${account}:${reload}`} items={visibleLinks} account={account} availabilityWriteEnabled={availabilityWriteEnabled} workingAction={workingAction} onRetryAvailability={(operationId, action) => void handleRetryAvailability(operationId, action)} onResolveAvailability={(operationId, action) => { setActionFeedback(null); setResolutionTarget({ operationId, action }); setResolution("APPLIED"); setEvidenceReference(""); setResolutionReason(""); }} onShowOperationHistory={showOperationHistory} emptyTitle={query || hasActiveFilters ? t("library.noFilteredResults") : t("library.emptyLinks")} emptyDescription={query || hasActiveFilters ? t("library.noFilteredResultsDesc") : t("library.emptyLinksDesc")} onClearFilters={() => { setFilters(defaultLibraryFilters); setSearch(""); setQuery(""); setPage(1); }} /> : null}
          {tab === "pool" ? <PoolPage items={pool} /> : null}
          {tab === "history" ? <HistoryPage items={events} /> : null}
          {currentPage && currentPage.totalPages > 1 ? <div className="pagination-bar"><span>{t("common.page")} {currentPage.page} {t("common.of")} {currentPage.totalPages} · {currentPage.totalItems} {t("common.records")}</span><div>
            <button type="button" aria-label={t("common.prevPage")} disabled={page <= 1 || loading} onClick={() => setPage((value) => Math.max(1, value - 1))}><ArrowLeft size={14} /></button>
            <button type="button" aria-label={t("common.nextPage")} disabled={page >= currentPage.totalPages || loading} onClick={() => setPage((value) => value + 1)}><ArrowRight size={14} /></button>
          </div></div> : null}
        </> : null}
      </section>

      {isImportOpen ? (
        <div className="drip-modal-backdrop" onClick={() => setIsImportOpen(false)} role="dialog" aria-modal="true">
          <div className="drip-modal-window" style={{ maxWidth: 520 }} onClick={(e) => e.stopPropagation()}>
            <div className="drip-modal-header">
              <h2 className="drip-title-h2">{t("library.importModalTitle", { account })}</h2>
              <button type="button" className="drip-close-btn" onClick={() => setIsImportOpen(false)}>✕</button>
            </div>
            <div className="drip-modal-body" style={{ padding: 20 }}>
              <p style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 12 }}>
                {t("library.importModalDesc", { account })}
              </p>
              <textarea
                rows={8}
                value={importText}
                onChange={(e) => setImportText(e.target.value)}
                placeholder="4012345678901&#10;4012345678918&#10;4012345678925"
                style={{
                  width: "100%",
                  padding: 10,
                  borderRadius: 8,
                  border: "1px solid var(--border-color)",
                  background: "var(--surface-sunken)",
                  color: "inherit",
                  fontFamily: "monospace",
                  fontSize: 13,
                  boxSizing: "border-box",
                }}
              />
              {importError ? <p style={{ color: "#ef4444", fontSize: 12, marginTop: 8 }}>{importError}</p> : null}
            </div>
            <div className="drip-modal-footer" style={{ padding: 16, display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <button type="button" className="drip-btn-secondary" onClick={() => setIsImportOpen(false)}>{t("common.cancel")}</button>
              <button
                type="button"
                className="drip-btn-primary"
                disabled={isImporting || !importText.trim()}
                onClick={handleImportEans}
              >
                {isImporting ? t("common.loading") : t("library.importSubmit")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {resolutionTarget ? (
        <div className="drip-modal-backdrop" onClick={() => { if (!workingAction) setResolutionTarget(null); }} role="dialog" aria-modal="true" aria-labelledby="library-availability-resolution-title">
          <form className="drip-modal-window" style={{ maxWidth: 560 }} onSubmit={(event) => void handleResolveAvailability(event)} onClick={(event) => event.stopPropagation()}>
            <div className="drip-modal-header">
              <h2 id="library-availability-resolution-title" className="drip-title-h2">{t("library.resolveModalTitle", { action: resolutionTarget.action === "QUANTITY" ? t("library.stock") : t("library.delivery") })}</h2>
              <button type="button" className="drip-close-btn" disabled={Boolean(workingAction)} onClick={() => setResolutionTarget(null)}>✕</button>
            </div>
            <div className="drip-modal-body" style={{ padding: 20, display: "grid", gap: 12 }}>
              <p style={{ fontSize: 13, color: "var(--text-muted)" }}>{t("library.resolveModalDesc")}</p>
              <label>{t("library.resolutionLabel")}<select value={resolution} onChange={(event) => setResolution(event.target.value as "APPLIED" | "NOT_APPLIED")}>
                <option value="APPLIED">{t("library.resolutionApplied")}</option>
                <option value="NOT_APPLIED">{t("library.resolutionNotApplied")}</option>
              </select></label>
              <label>{t("library.evidenceReference")}<input required maxLength={300} value={evidenceReference} onChange={(event) => setEvidenceReference(event.target.value)} placeholder={t("library.evidencePlaceholder")} /></label>
              <label>{t("library.resolutionReason")}<textarea required minLength={20} maxLength={2000} rows={4} value={resolutionReason} onChange={(event) => setResolutionReason(event.target.value)} /></label>
              {actionFeedback?.kind === "error" ? <p role="alert" style={{ color: "#ef4444", fontSize: 12 }}>{actionFeedback.message}</p> : null}
            </div>
            <div className="drip-modal-footer" style={{ padding: 16, display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <button type="button" className="drip-btn-secondary" disabled={Boolean(workingAction)} onClick={() => setResolutionTarget(null)}>{t("common.cancel")}</button>
              <button type="submit" className="demo-button" disabled={Boolean(workingAction) || !evidenceReference.trim() || resolutionReason.trim().length < 20}>{workingAction ? t("library.resolutionSaving") : t("library.saveResolution")}</button>
            </div>
          </form>
        </div>
      ) : null}
    </section>
  );
}
