"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  Database,
  Factory,
  FileText,
  Globe,
  Loader2,
  ScanSearch,
  Sparkles,
  Upload,
} from "lucide-react";
import { useRestartAccount } from "@/features/state/restart-account";
import { catalogDemoService } from "@/features/catalogs/service";
import { RestartApiClient } from "@/features/api/client";
import { describeImportError, describeImportRunError } from "@/features/catalogs/import-diagnostics";
import type { RestartDiscoveryCandidate, RestartDiscoveryRun, RestartDiscoverySchedule, RestartImportRunRow } from "@/features/api/types";
import { RuntimeModeGate } from "@/components/runtime-mode-gate";
import { getOttoRestartMode, isOttoRestartDemoMode } from "@/features/runtime-mode";
import "./discover.css";

const restartApi = new RestartApiClient();

export function DiscoverView() {
  const { account } = useRestartAccount();
  const mode = getOttoRestartMode();
  const isDemo = isOttoRestartDemoMode("discover");

  // Bulk Import State
  const [importText, setImportText] = useState("");
  const [importLoading, setImportLoading] = useState(false);
  const [importResult, setImportResult] = useState<any | null>(null);
  const [importRun, setImportRun] = useState<any | null>(null);
  const [baselineRun, setBaselineRun] = useState<any | null>(null);
  const [importRows, setImportRows] = useState<RestartImportRunRow[]>([]);
  const [importFilename, setImportFilename] = useState("paste_input.txt");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [scanRun, setScanRun] = useState<RestartDiscoveryRun | null>(null);
  const [scanCandidates, setScanCandidates] = useState<RestartDiscoveryCandidate[]>([]);
  const [scanLoading, setScanLoading] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [schedule, setSchedule] = useState<RestartDiscoverySchedule | null>(null);
  const [scheduleEnabledDraft, setScheduleEnabledDraft] = useState(false);
  const [scheduleSaving, setScheduleSaving] = useState(false);
  const [scheduleError, setScheduleError] = useState<string | null>(null);
  const [scanFromDate, setScanFromDate] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const importIdempotencyKey = useRef<string | null>(null);

  useEffect(() => {
    if (!scanFromDate) {
      setScanFromDate(new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10));
    }
  }, [scanFromDate]);

  useEffect(() => {
    setImportRun(null);
    setBaselineRun(null);
    setImportRows([]);
    setImportError(null);
    setScanRun(null);
    setScanCandidates([]);
    setScanError(null);
    setScheduleError(null);
    if (mode !== "PRODUCTION" || isDemo) {
      setSchedule(null);
      return;
    }
    const controller = new AbortController();
    void Promise.all([
      restartApi.getDiscoverySchedule(account, controller.signal),
      restartApi.listDiscoveryRuns(account, { limit: 1, signal: controller.signal }),
      restartApi.listImportRuns(account, 20, controller.signal),
    ]).then(async ([nextSchedule, runs, importRuns]) => {
      if (controller.signal.aborted) return;
      setSchedule(nextSchedule);
      setScheduleEnabledDraft(nextSchedule.enabled);
      const latestBaseline = importRuns.items.find((item) => item.runType === "FILE_IMPORT" && item.runMode === "BASELINE");
      setBaselineRun(latestBaseline ?? null);
      let rememberedRunId: string | null = null;
      try {
        rememberedRunId = window.localStorage.getItem(`otto_restart:last_import_run:${account}`);
      } catch {
        // The run remains available through the API even when browser storage is disabled.
      }
      if (rememberedRunId) {
        void restartApi.getImportRun(rememberedRunId, account, controller.signal).then(async (savedRun) => {
          if (controller.signal.aborted) return;
          setImportRun(savedRun);
        }).catch((error: any) => {
          if (!controller.signal.aborted) setImportError(`${error?.code ?? "RUN_STATUS_ERROR"}: ${error?.message ?? "Не удалось восстановить последний импорт."}`);
        });
      }
      const latest = runs.items[0];
      if (!latest) return;
      setScanRun(latest);
      const runId = latest.id ?? latest.runId;
      if (!runId) return;
      const [freshRun, candidates] = await Promise.all([
        restartApi.getDiscoveryRun(runId, account, controller.signal),
        restartApi.getDiscoveryCandidates(runId, account, { page: 1, pageSize: 100, signal: controller.signal }),
      ]);
      if (!controller.signal.aborted) {
        setScanRun(freshRun);
        setScanCandidates(candidates.items);
      }
    }).catch((error: any) => {
      if (!controller.signal.aborted) setScanError(`${error?.code ?? "DISCOVERY_STATUS_ERROR"}: ${error?.message ?? "Не удалось загрузить статус обнаружения."}`);
    });
    return () => controller.abort();
  }, [account, isDemo, mode]);

  function resetImportState() {
    setImportResult(null);
    setImportRun(null);
    setImportRows([]);
    setImportError(null);
    importIdempotencyKey.current = null;
  }

  function handleFillSample() {
    setImportText("DEMO-SKU-3M\nDEMO-SKU-12M");
    setImportFile(null);
    setImportFilename("paste_input.txt");
    resetImportState();
  }

  async function handleImportSubmit(e: React.FormEvent) {
    e.preventDefault();
    if ((!importText.trim() && !importFile) || importLoading) return;

    setImportLoading(true);
    setImportError(null);
    setImportResult(null);

    try {
      const filename = importFile?.name ?? importFilename;
      const res = isDemo
        ? await catalogDemoService.pasteImportContentAsync(account, importText, filename, true)
        : importFile
          ? await restartApi.previewSourceImportFile(account, importFile)
          : await restartApi.previewSourceImport(account, importText, filename);
      const status = String(res?.status ?? res?.summary?.status ?? res?.code ?? "");
      const message = String(res?.message ?? res?.error ?? res?.detail ?? "");
      if (res && typeof res === "object" && (res.summary || status)) {
        setImportResult({ ...res, status, message });
      } else {
        setImportError(`${status || "UNVERIFIED"}: ${message || "Backend не подтвердил результат."}`);
        return;
      }

      if (isDemo) return;
      if (res?.previewCompleted !== true) {
        setImportError("PREVIEW_UNVERIFIED: backend не подтвердил проверку файла.");
        return;
      }
      const validRows = Number(res?.counts?.valid ?? 0);
      if (!Number.isFinite(validRows) || validRows < 1) {
        setImportError("NO_VALID_ROWS: исправьте формат EAN/SKU перед импортом.");
        return;
      }

      if (!importIdempotencyKey.current) importIdempotencyKey.current = `manual-${crypto.randomUUID()}`;
      const accepted = importFile
        ? await restartApi.commitSourceImportFile(account, importFile, importIdempotencyKey.current)
        : await restartApi.commitSourceImport(account, importText, importIdempotencyKey.current, filename);
      try {
        window.localStorage.setItem(`otto_restart:last_import_run:${account}`, accepted.runId);
      } catch {
        // Do not turn an accepted server-side import into a UI error.
      }
      setImportResult(null);
      setImportRows([]);
      const initialRun = { ...accepted, id: accepted.runId, runState: accepted.status };
      setImportRun(initialRun);
      const freshRun = await restartApi.getImportRun(accepted.runId, account);
      setImportRun(freshRun);
    } catch (err: any) {
      setImportError(`LOOKUP_ERROR: ${err?.message || "Не удалось получить ответ backend."}`);
    } finally {
      setImportLoading(false);
    }
  }

  async function handleImportRunRefresh() {
    const runId = importRun?.id ?? importRun?.runId;
    if (!runId || importLoading) return;
    setImportLoading(true);
    setImportError(null);
    try {
      const result = await restartApi.getImportRun(runId, account);
      setImportRun(result);
      if (["SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"].includes(result.runState)) {
        const rows = await restartApi.getImportRunRows(runId, account, { page: 1, pageSize: 100 });
        setImportRows(rows.items);
        if (result.runMode === "BASELINE") setBaselineRun(result);
      }
    } catch (err: any) {
      setImportError(`${err?.code ?? "RUN_STATUS_ERROR"}: ${err?.message || "Не удалось обновить статус импорта."}`);
    } finally {
      setImportLoading(false);
    }
  }

  async function handleConfirmBaseline() {
    const runId = baselineRun?.runId ?? baselineRun?.id;
    if (!runId || !baselineRun?.runState || !["SUCCEEDED", "PARTIAL"].includes(baselineRun.runState) || importLoading) return;
    setImportLoading(true);
    setImportError(null);
    try {
      await restartApi.confirmDiscoveryBaseline(account, runId);
      const nextSchedule = await restartApi.getDiscoverySchedule(account);
      setSchedule(nextSchedule);
      setScheduleEnabledDraft(nextSchedule.enabled);
    } catch (error: any) {
      setImportError(`${error?.code ?? "BASELINE_CONFIRMATION_ERROR"}: ${error?.message ?? "Backend не подтвердил baseline."}`);
    } finally {
      setImportLoading(false);
    }
  }

  async function handleStartScan() {
    if (scanLoading || mode !== "PRODUCTION") return;
    if (!schedule?.baselineConfirmed && !scanFromDate) {
      setScanError("FROM_DATE_REQUIRED: укажите начальную UTC-дату ограниченного сканирования.");
      return;
    }
    setScanLoading(true);
    setScanError(null);
    setScanCandidates([]);
    try {
      const fromDate = schedule?.baselineConfirmed ? undefined : `${scanFromDate}T00:00:00Z`;
      const accepted = await restartApi.createDiscoveryScan(account, `manual-scan-${crypto.randomUUID()}`, fromDate);
      setScanRun(accepted);
      const runId = accepted.runId ?? accepted.id;
      if (runId) {
        const [freshRun, candidates] = await Promise.all([
          restartApi.getDiscoveryRun(runId, account),
          restartApi.getDiscoveryCandidates(runId, account, { page: 1, pageSize: 100 }),
        ]);
        setScanRun(freshRun);
        setScanCandidates(candidates.items);
      }
    } catch (error: any) {
      setScanError(`${error?.code ?? "SCAN_ERROR"}: ${error?.message ?? "Не удалось поставить обнаружение в очередь."}`);
    } finally {
      setScanLoading(false);
    }
  }

  async function handleRefreshScan() {
    const runId = scanRun?.id ?? scanRun?.runId;
    if (!runId || scanLoading) return;
    setScanLoading(true);
    setScanError(null);
    try {
      const [freshRun, candidates] = await Promise.all([
        restartApi.getDiscoveryRun(runId, account),
        restartApi.getDiscoveryCandidates(runId, account, { page: 1, pageSize: 100 }),
      ]);
      setScanRun(freshRun);
      setScanCandidates(candidates.items);
    } catch (error: any) {
      setScanError(`${error?.code ?? "SCAN_STATUS_ERROR"}: ${error?.message ?? "Не удалось обновить скан."}`);
    } finally {
      setScanLoading(false);
    }
  }

  async function handleSaveSchedule() {
    if (!schedule || scheduleSaving || mode !== "PRODUCTION") return;
    setScheduleSaving(true);
    setScheduleError(null);
    try {
      const next = await restartApi.updateDiscoverySchedule({
        account,
        enabled: scheduleEnabledDraft,
        timezone: schedule.timezone,
        weekday: schedule.weekday,
        localTime: schedule.localTime,
      });
      setSchedule(next);
      setScheduleEnabledDraft(next.enabled);
    } catch (error: any) {
      setScheduleError(`${error?.code ?? "SCHEDULE_ERROR"}: ${error?.message ?? "Не удалось сохранить расписание."}`);
    } finally {
      setScheduleSaving(false);
    }
  }

  function handleFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setImportFile(file);
    setImportFilename(file.name);
    resetImportState();

    const reader = new FileReader();
    reader.onload = (event) => {
      const text = event.target?.result;
      if (typeof text === "string") {
        setImportText(text);
        resetImportState();
      }
    };
    reader.readAsText(file, "utf-8");
    e.currentTarget.value = "";
  }

  useEffect(() => {
    const runId = importRun?.id ?? importRun?.runId;
    const state = String(importRun?.runState ?? "");
    if (!runId || ["SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"].includes(state)) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const fresh = await restartApi.getImportRun(runId, account);
        if (cancelled) return;
        setImportRun(fresh);
        if (["SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"].includes(fresh.runState)) {
          if (fresh.runMode === "BASELINE") setBaselineRun(fresh);
          return;
        }
      } catch (error: any) {
        if (!cancelled) setImportError(`${error?.code ?? "RUN_STATUS_ERROR"}: ${error?.message ?? "Не удалось обновить статус импорта."}`);
      }
      if (!cancelled) timer = setTimeout(poll, 2000);
    };
    timer = setTimeout(poll, 1200);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [account, importRun?.id, importRun?.runId, importRun?.runState]);

  // Terminal-state row loading has its own effect. Updating runState inside
  // polling cancels that poll, so its asynchronous row response must not own
  // the final result table.
  useEffect(() => {
    const runId = importRun?.id ?? importRun?.runId;
    if (!runId || !["SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"].includes(String(importRun?.runState))) return;
    const controller = new AbortController();
    restartApi.getImportRunRows(runId, account, { page: 1, pageSize: 100, signal: controller.signal })
      .then((rows) => { if (!controller.signal.aborted) setImportRows(rows.items); })
      .catch((error) => { if (!controller.signal.aborted) setImportError(error instanceof Error ? error.message : "Не удалось загрузить строки импорта."); });
    return () => controller.abort();
  }, [account, importRun?.id, importRun?.runId, importRun?.runState]);

  const importStatus = String(importResult?.status ?? importResult?.summary?.status ?? "UNVERIFIED");
  const importSummary = importResult?.summary ?? {};
  const isDemoPreview =
    isDemo &&
    importStatus === "DEMO_ONLY" &&
    importSummary.dataScope === "DEMO_ONLY" &&
    (importResult?.dryRun === true || importSummary.dryRun === true);

  return (
    <RuntimeModeGate title="Обнаружение 3M/12M недоступно" demoEnabled={isDemo || mode === "PRODUCTION" || mode === "TEST"} showDemoBanner={false}>
    <div className="discover-page">
      {/* 1. Header */}
      <header className="discover-header">
        <div className="discover-header-title">
          <h1>Выявить новые 3/12</h1>
          <p>
            Импорт baseline по списку и ограниченное account-scoped обнаружение 3M/12M.
            Маршрут Afterbuy или Direct OTTO фиксируется только после подтверждённых ответов API.
          </p>
        </div>
        <div className="discover-account-badge">
          <Database size={13} />
          <span>Аккаунт {account}</span>
        </div>
      </header>

      {/* 2. Account-scoped bounded discovery */}
      <section
        className="discover-scanner-card"
        aria-label="Панель управления сканером 3/12M"
      >
        <div className="discover-scanner-header">
          <div className="discover-scanner-intro">
            <div className="discover-scanner-icon">
              <ScanSearch size={24} />
            </div>
            <div className="discover-scanner-title">
              <h2>Обнаружение 3M/12M</h2>
              <div className="discover-scanner-status">
                <span>{schedule?.enabled ? "Недельное расписание включено для аккаунта" : "Автоматическое расписание выключено"}</span>
              </div>
            </div>
          </div>

          <button type="button" className="discover-trigger-btn" disabled={scanLoading || mode !== "PRODUCTION"} onClick={handleStartScan}>
            {scanLoading ? <Loader2 size={16} className="discover-spinner" /> : <ScanSearch size={16} />}
            <span>{scanLoading ? "Постановка в очередь…" : "Запустить ручное обнаружение"}</span>
          </button>
        </div>

        <p className="discover-scanner-desc">
          Скан читается только из выбранного OTTO аккаунта, использует cursor и overlap 48 часов. Полный каталог не сканируется.
          При отсутствии baseline укажите ограниченную начальную дату; максимум окна — 16 дней.
        </p>
        {!schedule?.baselineConfirmed ? (
          <label className="discover-scan-date">
            Начальная дата UTC
            <input type="date" value={scanFromDate} onChange={(event) => setScanFromDate(event.target.value)} disabled={scanLoading} />
          </label>
        ) : null}
        {scanRun ? (
          <div className="discover-progress-block" role="status">
            <span>Run {scanRun.id ?? scanRun.runId} · {scanRun.runState}</span>
            {scanRun.windowFrom ? <span> · from {new Date(scanRun.windowFrom).toLocaleString()}</span> : null}
            <button type="button" className="discover-sample-btn" onClick={handleRefreshScan} disabled={scanLoading}>
              {scanLoading ? "Обновление…" : "Обновить статус и товары"}
            </button>
          </div>
        ) : null}
        {scanError ? <div className="discover-progress-block discover-error-block" role="alert">{scanError}</div> : null}
        {scanCandidates.length ? (
          <div className="discover-candidate-table-wrap">
            <table className="catalog-product-table discover-candidate-table">
              <thead><tr><th>OTTO SKU</th><th>Статус</th><th>Источник / маршрут</th><th>Фабрика</th><th>Название</th><th>Причина</th></tr></thead>
              <tbody>{scanCandidates.map((candidate) => (
                <tr key={`${candidate.requestedSku}-${candidate.episodeId ?? "observation"}`}>
                  <td><code>{candidate.requestedSku}</code></td>
                  <td>{candidate.period ?? candidate.classification}</td>
                  <td>{candidate.sourceType ?? candidate.routingState ?? "UNVERIFIED"}</td>
                  <td>{candidate.factoryName ?? candidate.factoryId ?? "—"}</td>
                  <td>{candidate.title ?? "—"}</td>
                  <td>{candidate.routingErrorCode ?? candidate.reductionState ?? "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : null}
      </section>

      <section className="discover-schedule-card" aria-label="Базовый список и недельное расписание">
        <div className="discover-schedule-heading">
          <div>
            <h3>Baseline и недельная проверка</h3>
            <p>Для включения расписания сначала импортируйте полный список и подтвердите, что baseline охватывает нужные товары 3M/12M.</p>
          </div>
          <span className={`discover-schedule-badge ${schedule?.baselineConfirmed ? "is-confirmed" : ""}`}>
            {schedule?.baselineConfirmed ? "BASELINE ПОДТВЕРЖДЁН" : "BASELINE НЕ ПОДТВЕРЖДЁН"}
          </span>
        </div>
        {baselineRun ? (
          <div className="discover-progress-block">
            <span>Последний BASELINE · {baselineRun.id ?? baselineRun.runId} · {baselineRun.runState}</span>
            <span>Строк: {baselineRun.totalRows} · ошибки/неоднозначности: {baselineRun.errorCount}</span>
            {!schedule?.baselineConfirmed && ["SUCCEEDED", "PARTIAL"].includes(baselineRun.runState) ? (
              <button type="button" className="discover-sample-btn" onClick={handleConfirmBaseline} disabled={importLoading}>
                Подтвердить полноту baseline
              </button>
            ) : null}
          </div>
        ) : null}
        <div className="discover-schedule-controls">
          <label className="discover-schedule-toggle">
            <input
              type="checkbox"
              checked={scheduleEnabledDraft}
              onChange={(event) => setScheduleEnabledDraft(event.target.checked)}
              disabled={!schedule?.baselineConfirmed || scheduleSaving || mode !== "PRODUCTION"}
            />
            Запускать каждую неделю
          </label>
          <label>День недели
            <select value={schedule?.weekday ?? 0} onChange={(event) => setSchedule((current) => current ? { ...current, weekday: Number(event.target.value) } : current)} disabled={!schedule?.baselineConfirmed || scheduleSaving}>
              <option value={0}>Понедельник</option><option value={1}>Вторник</option><option value={2}>Среда</option>
              <option value={3}>Четверг</option><option value={4}>Пятница</option><option value={5}>Суббота</option><option value={6}>Воскресенье</option>
            </select>
          </label>
          <label>Время
            <input type="time" value={schedule?.localTime ?? "03:00"} onChange={(event) => setSchedule((current) => current ? { ...current, localTime: event.target.value } : current)} disabled={!schedule?.baselineConfirmed || scheduleSaving} />
          </label>
          <label>Часовой пояс
            <input value={schedule?.timezone ?? "Europe/Berlin"} readOnly aria-label="IANA часовой пояс" />
          </label>
          <button type="button" className="discover-sample-btn" onClick={handleSaveSchedule} disabled={!schedule || scheduleSaving || mode !== "PRODUCTION"}>
            {scheduleSaving ? "Сохранение…" : "Сохранить расписание"}
          </button>
        </div>
        <p className="discover-schedule-note">Расписание остаётся выключенным по умолчанию. Для автостарта нужны включённый server flag, Beat и worker; без них можно запускать ручное обнаружение.</p>
        {schedule?.nextAt ? <p className="discover-schedule-note">Следующий запуск: {new Date(schedule.nextAt).toLocaleString()} ({schedule.timezone})</p> : null}
        {scheduleError ? <div className="discover-progress-block discover-error-block" role="alert">{scheduleError}</div> : null}
      </section>

      {/* 3. Bulk Import Section (Phase A) */}
      <section className="discover-import-card" aria-label="Первоначальный массовый импорт">
        <div className="discover-import-header">
          <div className="discover-import-icon">
            <FileText size={22} />
          </div>
          <div className="discover-import-title">
            <h3>Массовый импорт списков (TXT / CSV)</h3>
            <p>
              Предпросмотр проверяет формат локально. Импорт BASELINE выполняет account-scoped GET OTTO и
              Aftercool и сохраняет только подтверждённые данные. Метки периода из файла игнорируются.
            </p>
          </div>
        </div>

        <form onSubmit={handleImportSubmit} style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
          <textarea
            className="discover-import-textarea"
            placeholder="Вставьте список EAN или SKU (по одному в строке, либо CSV: ean;period;factory_id)..."
            value={importText}
            onChange={(e) => {
              setImportText(e.target.value);
              setImportFile(null);
              setImportFilename("paste_input.txt");
                  if (fileInputRef.current) fileInputRef.current.value = "";
              resetImportState();
            }}
            disabled={importLoading}
          />

          <input
            type="file"
            ref={fileInputRef}
            style={{ display: "none" }}
            accept=".txt,.csv"
            onChange={handleFileSelected}
          />

          <div className="discover-import-controls">
            <div className="discover-import-options">
              <span className="discover-checkbox-label" role="status">
                <span>{isDemo ? "DEMO: только локальный предпросмотр" : "Сначала проверяется формат, затем запускается импорт для выбранного аккаунта"}</span>
              </span>

              <button
                type="button"
                className="discover-sample-btn"
                onClick={() => fileInputRef.current?.click()}
                disabled={importLoading}
              >
                <Upload size={13} style={{ display: "inline", marginRight: "6px" }} />
                Выбрать файл (.txt, .csv)
              </button>

              {isDemo ? <button
                type="button"
                className="discover-sample-btn"
                onClick={handleFillSample}
                disabled={importLoading}
              >
                Тестовый список
              </button> : null}
            </div>

            <div className="discover-import-actions">
              <button
                type="submit"
                className="discover-submit-import-btn"
                disabled={(!importText.trim() && (!importFile || isDemo)) || importLoading}
              >
                {importLoading ? (
                  <>
                    <Loader2 size={14} className="discover-spinner" />
                    <span>Обработка...</span>
                  </>
                ) : (
                  <>
                    <Sparkles size={14} />
                    <span>
                      {isDemo ? "Предпросмотр" : "Импортировать"}
                      {importFile ? ` · ${importFile.name}` : ` (${importText.split("\n").filter((l) => l.trim()).length})`}
                    </span>
                  </>
                )}
              </button>
            </div>
          </div>
        </form>

        {(importRun?.runId || importRun?.id) ? (
          <div className="discover-progress-block" role="status">
            <span>Run {importRun.runId ?? importRun.id} · {importRun.runState ?? importRun.status ?? "PENDING"}</span>
            {importRun.jobId ? <span> · Job {importRun.jobId}</span> : null}
            {importRun.processedCount !== undefined ? <span> · обработано {importRun.processedCount}/{importRun.totalRows}</span> : null}
            {importRun.afterbuyCount !== undefined ? <span> · Afterbuy {importRun.afterbuyCount}</span> : null}
            {importRun.directOttoCount !== undefined ? <span> · Direct OTTO {importRun.directOttoCount}</span> : null}
            {importRun.errorCount !== undefined ? <span> · нерешено/ошибки {importRun.errorCount}</span> : null}
            {!isDemo ? <button type="button" className="discover-sample-btn" onClick={handleImportRunRefresh} disabled={importLoading}>Обновить статус</button> : null}
          </div>
        ) : null}

        {importRun?.errors?.length ? (
          <ul className="discover-unverified-list" role="alert">
            {importRun.errors.map((error: any, index: number) => (
              <li key={`${error.code ?? "run-error"}-${index}`}>{describeImportRunError(error)}</li>
            ))}
          </ul>
        ) : null}

        {importRows.length ? (
          <div className="discover-candidate-table-wrap">
            <table className="catalog-product-table discover-candidate-table">
              <thead><tr><th>Строка</th><th>EAN / SKU</th><th>Статус</th><th>Маршрут</th><th>Фабрика / source_file</th><th>Причина</th></tr></thead>
              <tbody>{importRows.map((item) => {
                const row = item.row ?? {};
                const aftercool = row.aftercoolSelection as Record<string, unknown> | undefined;
                const factory = aftercool?.factory_name ?? aftercool?.factory_id ?? "—";
                const sourceFile = aftercool?.source_file;
                const excluded = aftercool?.excluded_factory_match_count;
                const errors = item.errors.map((error) => error.code).filter(Boolean).map(describeImportError).join("; ");
                return (
                  <tr key={item.id}>
                    <td>{item.rowNumber}</td>
                    <td><code>{item.normalizedIdentifier ?? item.rawIdentifier}</code></td>
                    <td>{item.rowState}</td>
                    <td>{String(row.routeStatus ?? "—")}</td>
                    <td>{[String(factory), sourceFile ? String(sourceFile) : null, excluded !== undefined ? `исключено совпадений: ${String(excluded)}` : null].filter(Boolean).join(" · ")}</td>
                    <td>{errors || String(row.errorCode ?? "—")}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        ) : null}

        {importError && (
          <div className="discover-progress-block" style={{ borderLeft: "3px solid #ef4444" }}>
            <span style={{ color: "#ef4444", fontSize: "13px" }}>{importError}</span>
          </div>
        )}

        {importResult && (
          <div className="discover-import-results">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontWeight: 700, fontSize: "13px", color: "var(--color-ink)" }}>
                {isDemoPreview
                  ? "DEMO · только локальный предпросмотр; данные не сохранены"
                  : `${importStatus} · локальная проверка формата`}
              </span>
              <span style={{ fontSize: "12px", color: "var(--color-muted)" }}>
                {importSummary.filename ? `Файл: ${importSummary.filename}` : "Задание ещё не создано"}
              </span>
            </div>

            {isDemoPreview ? <div className="discover-results-strip">
              <div className="discover-result-metric">
                <span>Демо-примеры:</span>
                <strong>{importSummary.uniqueValidCount ?? 0}</strong>
              </div>
              <div className="discover-result-metric">
                <span>3 месяца · DEMO:</span>
                <strong style={{ color: "#f59e0b" }}>{importSummary.period3mCount ?? 0}</strong>
              </div>
              <div className="discover-result-metric">
                <span>12 месяцев · DEMO:</span>
                <strong style={{ color: "#f43f5e" }}>{importSummary.period12mCount ?? 0}</strong>
              </div>
              <div className="discover-result-metric">
                <span>Источник:</span>
                <strong>DEMO_ONLY</strong>
              </div>
              {importSummary.skippedExistingCount > 0 && (
                <div className="discover-result-metric">
                  <span>Демо-дубликаты:</span>
                  <strong style={{ color: "var(--color-muted)" }}>{importSummary.skippedExistingCount}</strong>
                </div>
              )}
            </div> : <div className="discover-results-strip">
              <div className="discover-result-metric"><span>Статус классификации:</span><strong>UNVERIFIED</strong></div>
              <div className="discover-result-metric"><span>Не подтверждено строк:</span><strong>{importSummary.unverifiedCount ?? importSummary.totalParsed ?? "—"}</strong></div>
              <div className="discover-result-metric"><span>Причина:</span><strong>{importResult.message || "NOT_IMPLEMENTED"}</strong></div>
            </div>}

            {isDemoPreview && importSummary.samplePreview && importSummary.samplePreview.length > 0 && (
              <div style={{ marginTop: "8px", overflowX: "auto" }}>
                <table className="catalog-product-table" style={{ fontSize: "11px" }}>
                  <thead>
                    <tr>
                      <th>EAN / SKU</th>
                      <th>Период</th>
                      <th>Источник</th>
                      <th>Фабрика</th>
                      <th>Название</th>
                    </tr>
                  </thead>
                  <tbody>
                    {importSummary.samplePreview.slice(0, 5).map((p: any, idx: number) => (
                      <tr key={idx}>
                        <td><code>{p.identifier}</code></td>
                        <td>
                          <span className={`period-pill ${p.period === "3M" ? "pill-3m" : "pill-12m"}`}>
                            {p.period}
                          </span>
                        </td>
                        <td>{p.source_type === "AFTERBUY" ? "Afterbuy" : "Прямой OTTO"}</td>
                        <td>{p.factory_name || "—"}</td>
                        <td style={{ maxWidth: "250px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {p.title}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {!isDemoPreview && importSummary.unverifiedSample?.length > 0 ? (
              <ul className="discover-unverified-list">
                {importSummary.unverifiedSample.slice(0, 10).map((row: any, idx: number) => (
                  <li key={`${row.identifier ?? "row"}-${idx}`}>
                    <code>{row.identifier ?? "—"}</code> · {row.status ?? importStatus}: {row.reason ?? "Нет подтверждённых данных."}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        )}
      </section>

      {/* 4. Two Core Distribution Cards (Options) */}
      <section
        className="discover-options-section"
        aria-label="Варианты распределения товаров 3/12M"
      >
        <div className="discover-section-heading">
          <h2>Два контура каталога Restart</h2>
          <p>
            Примеры целевых контуров. До подтверждённого lookup данные не распределяются.
          </p>
        </div>

        <div className="discover-options-grid">
          {/* Card 1: Direct OTTO */}
          <article className="discover-option-card">
            <div className="discover-option-top">
              <div className="discover-option-badge-row">
                <span className="discover-source-chip chip-direct">
                  UNVERIFIED · Aftercool не проверен
                </span>
                <span className="discover-option-number">Вариант 1</span>
              </div>

              <div className="discover-option-header-content">
                <div className="discover-option-icon-box icon-box-direct">
                  <Globe size={22} />
                </div>
                <div className="discover-option-title-wrap">
                  <h3>Товары напрямую из ОТТО</h3>
                  <p>Каталог прямых карточек маркетплейса</p>
                </div>
              </div>

              <p className="discover-option-body">
                В этот контур можно направлять товар только после подтверждённого
                результата NOT_FOUND от полного lookup в правильном аккаунте.
              </p>

              <div className="discover-option-metrics">
                <div className="discover-metric-pill"><span>Маршрут:</span><strong>NOT_IMPLEMENTED</strong></div>
                <div className="discover-metric-pill"><span>Доказательство отсутствия:</span><strong>нет</strong></div>
              </div>
            </div>

            <Link href="/direct-otto" className="discover-option-btn">
              <span>Открыть товары напрямую из ОТТО</span>
              <ArrowRight size={15} />
            </Link>
          </article>

          {/* Card 2: Afterbuy / Aftercool */}
          <article className="discover-option-card">
            <div className="discover-option-top">
              <div className="discover-option-badge-row">
                <span className="discover-source-chip chip-afterbuy">
                  UNVERIFIED · Aftercool не проверен
                </span>
                <span className="discover-option-number">Вариант 2</span>
              </div>

              <div className="discover-option-header-content">
                <div className="discover-option-icon-box icon-box-afterbuy">
                  <Factory size={22} />
                </div>
                <div className="discover-option-title-wrap">
                  <h3>Товары из Afterbuy</h3>
                  <p>Распределение по фабрикам-производителям</p>
                </div>
              </div>

              <p className="discover-option-body">
                Привязка к фабрике возможна только по однозначному ответу
                Aftercool с подтверждённой product ID и фабрикой.
              </p>

              <div className="discover-option-metrics">
                <div className="discover-metric-pill"><span>Маршрут:</span><strong>NOT_IMPLEMENTED</strong></div>
                <div className="discover-metric-pill"><span>Источник:</span><strong>UNVERIFIED</strong></div>
              </div>
            </div>

            <Link href="/afterbuy" className="discover-option-btn">
              <span>Открыть каталоги фабрик Afterbuy</span>
              <ArrowRight size={15} />
            </Link>
          </article>
        </div>
      </section>

      {/* 5. Architecture & Pipeline Card */}
      <section
        className="discover-architecture-card"
        aria-label="Схема работы алгоритма выявления"
      >
        <div className="discover-architecture-header">
          <Sparkles size={18} style={{ color: "var(--color-accent)" }} />
          <h3>Планируемые шаги обнаружения · сейчас не выполняются</h3>
        </div>

        <div className="discover-steps-grid">
          <div className="discover-step-item">
            <div className="discover-step-head">
              <span className="discover-step-number">1</span>
              <span>Поиск в OTTO API</span>
            </div>
            <div className="discover-step-title">Выявление 3M/12M</div>
            <p className="discover-step-desc">
              S04 classifier и S06 read adapter ещё не реализованы. Метка периода
              из файла не является доказательством ограничения.
            </p>
            <code className="discover-endpoint-code">
              GET /v5/products/&#123;sku&#125;/marketplace-status
            </code>
          </div>

          <div className="discover-step-item">
            <div className="discover-step-head">
              <span className="discover-step-number">2</span>
              <span>Сверка с Aftercool</span>
            </div>
            <div className="discover-step-title">Проверка принадлежности</div>
            <p className="discover-step-desc">
              S07 read adapter ещё не реализован. Ошибка или отсутствие ответа не
              доказывает отсутствие товара в Aftercool.
            </p>
            <code className="discover-endpoint-code">
              GET /api/products/by-ean?ean=&#123;ean&#125;
            </code>
            <code className="discover-endpoint-code">
              GET /api/products?dataset=Lister
            </code>
          </div>

          <div className="discover-step-item">
            <div className="discover-step-head">
              <span className="discover-step-number">3</span>
              <span>Маршрутизация</span>
            </div>
            <div className="discover-step-title">Распределение по каталогам</div>
            <p className="discover-step-desc">
              Маршрут остаётся <code>UNVERIFIED</code>, пока не получено
              однозначное подтверждение присутствия или полного отсутствия.
            </p>
          </div>
        </div>
      </section>
    </div>
    </RuntimeModeGate>
  );
}
