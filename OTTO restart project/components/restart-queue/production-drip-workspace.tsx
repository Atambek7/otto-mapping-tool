"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CalendarClock, ChevronLeft, ChevronRight, CirclePause, CirclePlay, ListChecks,
  Play, RefreshCw, ShieldCheck, X,
} from "lucide-react";
import { ApiState } from "@/components/api-state";
import { WorkflowStepper } from "@/components/workflow-stepper";
import { RestartApiClient, RestartApiError } from "@/features/api/client";
import type {
  RestartDripBatch, RestartDripBatchItem, RestartDripBatchStatus,
  RestartDripSchedulePreview, RestartDripScheduleRequest, RestartProduct,
  RestartQueuePage, RestartSettings,
} from "@/features/api/types";
import { useRestartAccount } from "@/features/state/restart-account";
import "./production-drip-workspace.css";

const api = new RestartApiClient();
const PAGE_SIZE = 50;
const batchStatusLabels: Record<RestartDripBatchStatus, string> = {
  DRAFT: "Черновик",
  SCHEDULED: "Запланирована",
  ACTIVE: "Активна",
  PAUSED: "Приостановлена",
  COMPLETED: "Завершена",
  CANCELLED: "Отменена",
};

function errorText(error: unknown): string {
  if (error instanceof RestartApiError) return `${error.message} (${error.code})`;
  return error instanceof Error ? error.message : "Не удалось выполнить запрос OttoRestart.";
}

function idempotencyKey(action: string): string {
  if (!globalThis.crypto?.randomUUID) throw new Error("Браузер не поддерживает безопасные ключи повторного запроса.");
  return `drip-${action}-${globalThis.crypto.randomUUID()}`;
}

function formattedDate(value: string, timezone?: string | null): string {
  try {
    return new Intl.DateTimeFormat("ru-RU", {
      dateStyle: "medium", timeStyle: "short", ...(timezone ? { timeZone: timezone } : {}),
    }).format(new Date(value));
  } catch {
    return value;
  }
}

function itemStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    SCHEDULED: "Ожидает времени",
    READY_TO_SUBMIT: "Готов к следующему этапу",
    NEEDS_REVIEW: "Нужна проверка",
    CANCELLED: "Отменена",
  };
  return labels[status] ?? status;
}

function queueStateLabel(state: string): string {
  const labels: Record<string, string> = {
    PENDING: "Ожидает",
    RUNNING: "Выполняется",
    RETRY_WAIT: "Ожидает повтора",
    SUCCEEDED: "Завершено",
    DEAD: "Остановлено с ошибкой",
    CANCELLED: "Отменено",
  };
  return labels[state] ?? state;
}

function DurableBackgroundJobs({ account }: { account: "JV" | "XL" }) {
  const [data, setData] = useState<RestartQueuePage | null>(null);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api.getQueue(account, { page, pageSize: 20, signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted) setData(result);
    }).catch((cause) => {
      if (!controller.signal.aborted) setError(errorText(cause));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [account, page, reload]);

  return <section className="surface-card production-drip-card">
    <div className="card-heading"><div><h2>Фоновые задания · {account}</h2>
      <p>Сохранённые jobs OttoRestart. Просмотр только читает метаданные; payload и ключи идемпотентности не выдаются.</p></div>
      <button type="button" className="review-secondary" disabled={loading} onClick={() => setReload((value) => value + 1)}><RefreshCw size={14} /> Обновить</button>
    </div>
    {data ? <div className="production-drip-section-heading"><div>
      <span className={`status-pill${data.status === "NEEDS_ATTENTION" ? " status-failed" : ""}`}>Состояние: {data.status}</span>
      <p>Ожидают {data.counts.pending} · выполняются {data.counts.running} · повтор {data.counts.retryWait} · завершены {data.counts.succeeded} · ошибки {data.counts.dead} · dispatch errors {data.counts.dispatchErrors}</p>
    </div></div> : null}
    {loading && !data ? <ApiState kind="loading" label="Читаем очередь фоновых заданий…" /> : null}
    {error ? <ApiState kind="error" description={error} onRetry={() => setReload((value) => value + 1)} /> : null}
    {!loading && !error && data?.items.length === 0 ? <ApiState kind="empty" title="Фоновых заданий нет" description="Для этого аккаунта backend вернул ноль сохранённых jobs." /> : null}
    {data?.items.length ? <div className="restart-table-wrap production-drip-table-wrap"><table className="restart-table">
      <thead><tr><th>Задание</th><th>Тип</th><th>Состояние</th><th>Попытки</th><th>Создано / доступно</th><th>Ошибка</th></tr></thead>
      <tbody>{data.items.map((job) => <tr key={job.id}>
        <td className="mono-value">{job.id.slice(0, 8)}</td><td>{job.jobType}</td><td><span className="status-pill">{queueStateLabel(job.state)}</span></td>
        <td>{job.totalAttempts} / {job.maxAttempts}</td><td>{formattedDate(job.createdAt)}<small>доступно {formattedDate(job.availableAt)}</small></td>
        <td>{job.lastErrorCode || job.lastDispatchErrorCode || "—"}</td>
      </tr>)}</tbody>
    </table></div> : null}
    {data && data.page.totalPages > 1 ? <div className="pagination-bar production-drip-pagination"><span>Страница {data.page.page} из {data.page.totalPages} · всего {data.page.totalItems.toLocaleString("ru-RU")}</span><div>
      <button type="button" aria-label="Предыдущая страница заданий" disabled={page <= 1 || loading} onClick={() => setPage((value) => value - 1)}><ChevronLeft size={16} /></button>
      <button type="button" aria-label="Следующая страница заданий" disabled={page >= data.page.totalPages || loading} onClick={() => setPage((value) => value + 1)}><ChevronRight size={16} /></button>
    </div></div> : null}
  </section>;
}

export function ProductionDripWorkspace() {
  const { account, ready: accountReady } = useRestartAccount();
  const [candidatePage, setCandidatePage] = useState(1);
  const [candidateData, setCandidateData] = useState<Awaited<ReturnType<typeof api.listProducts>> | null>(null);
  const [batches, setBatches] = useState<RestartDripBatch[]>([]);
  const [batchPage, setBatchPage] = useState(1);
  const [batchPageData, setBatchPageData] = useState<Awaited<ReturnType<typeof api.listDripBatches>> | null>(null);
  const [selected, setSelected] = useState<Map<string, RestartProduct>>(new Map());
  const [preview, setPreview] = useState<RestartDripSchedulePreview | null>(null);
  const [name, setName] = useState("");
  const [startDate, setStartDate] = useState("");
  const [dailyTime, setDailyTime] = useState("");
  const [timezone, setTimezone] = useState("");
  const [totalDays, setTotalDays] = useState(7);
  const [detail, setDetail] = useState<{ batchId: string; items: RestartDripBatchItem[]; page: number; totalPages: number } | null>(null);
  const [runtimeSettings, setRuntimeSettings] = useState<RestartSettings | null>(null);
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [settingsError, setSettingsError] = useState("");
  const [settingsReload, setSettingsReload] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const requestAccount = useRef(account);
  requestAccount.current = account;
  const selectedProducts = useMemo(() => [...selected.values()], [selected]);
  const selectedIds = useMemo(() => new Set(selected.keys()), [selected]);
  const sameCohort = selectedProducts.length > 0
    && selectedProducts.every((product) => product.period === selectedProducts[0].period
      && product.factoryId === selectedProducts[0].factoryId);
  const canPreview = sameCohort && selectedProducts.every((product) => product.operationId && product.status === "READY");
  const currentSettings = runtimeSettings?.account === account ? runtimeSettings : null;

  const load = useCallback(async (signal: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      const [candidateResult, batchResult] = await Promise.all([
        api.listProducts(account, { page: candidatePage, pageSize: PAGE_SIZE, status: "READY", includeMetrics: false, signal }),
        api.listDripBatches(account, { page: batchPage, pageSize: 50, signal }),
      ]);
      if (signal.aborted || requestAccount.current !== account) return;
      setCandidateData(candidateResult);
      setBatches(batchResult.items);
      setBatchPageData(batchResult);
    } catch (cause) {
      if (!signal.aborted && requestAccount.current === account) setError(errorText(cause));
    } finally {
      if (!signal.aborted && requestAccount.current === account) setLoading(false);
    }
  }, [account, candidatePage, batchPage]);

  useEffect(() => {
    setCandidatePage(1);
    setBatchPage(1);
    setCandidateData(null);
    setBatches([]);
    setBatchPageData(null);
    setSelected(new Map());
    setPreview(null);
    setDetail(null);
  }, [account]);

  useEffect(() => {
    if (!accountReady) return;
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [accountReady, account, load, reload]);

  useEffect(() => {
    if (!accountReady) return;
    const controller = new AbortController();
    setRuntimeSettings(null);
    setSettingsLoading(true);
    setSettingsError("");
    api.getSettings(account, controller.signal).then((value) => {
      if (!controller.signal.aborted && requestAccount.current === account) setRuntimeSettings(value);
    }).catch((cause) => {
      if (!controller.signal.aborted && requestAccount.current === account) setSettingsError(errorText(cause));
    }).finally(() => {
      if (!controller.signal.aborted) setSettingsLoading(false);
    });
    return () => controller.abort();
  }, [accountReady, account, settingsReload]);

  function updateSelection(product: RestartProduct, checked: boolean) {
    setSelected((current) => {
      const next = new Map(current);
      if (checked) next.set(product.id, product);
      else next.delete(product.id);
      return next;
    });
    setPreview(null);
    setNotice("");
  }

  function requestBody(): RestartDripScheduleRequest | null {
    if (!name.trim()) { setError("Укажите название расписания."); return null; }
    if (!startDate || !dailyTime || !timezone.trim()) {
      setError("Укажите дату, время и IANA timezone расписания.");
      return null;
    }
    if (!canPreview) {
      setError("Выберите только READY-операции одной фабрики и одного периода 3M/12M.");
      return null;
    }
    return {
      account,
      name: name.trim(),
      operationIds: selectedProducts.map((product) => product.operationId as string),
      startDate,
      dailyTime,
      timezone: timezone.trim(),
      totalDays,
    };
  }

  async function previewSchedule() {
    setBusy(true); setError(""); setNotice(""); setPreview(null);
    try {
      const body = requestBody();
      if (!body) return;
      setPreview(await api.previewDripSchedule(body));
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }

  async function createDraft() {
    if (!preview || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const body = requestBody();
      if (!body) return;
      await api.createDripBatch(body, idempotencyKey("create"));
      setSelected(new Map()); setPreview(null); setName("");
      setNotice("Черновик расписания сохранён в OttoRestart. Вызовов OTTO и публикации не было.");
      setReload((value) => value + 1);
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }

  async function controlBatch(batch: RestartDripBatch, action: "start" | "pause" | "resume" | "cancel" | "retry") {
    if (busy) return;
    if (["start", "resume", "retry"].includes(action) && (!currentSettings || settingsLoading || settingsError)) {
      setSettingsError("Не удалось подтвердить текущие серверные флаги. Обновите настройки перед запуском или повтором очереди.");
      return;
    }
    if (action === "start" || action === "resume" || action === "retry") {
      const productWriteAvailable = Boolean(currentSettings?.mode === "PRODUCTION"
        && currentSettings.otto.readEnabled && currentSettings.publication.productWriteEnabled);
      const availabilityWriteAvailable = Boolean(currentSettings?.mode === "PRODUCTION"
        && currentSettings.otto.readEnabled && currentSettings.publication.availabilityWriteEnabled);
      const productStatus = productWriteAvailable
        ? "Серверные настройки РАЗРЕШАЮТ worker отправить товар в OTTO после наступления расписания и повторной проверки QC."
        : "Отправка товара в OTTO сейчас заблокирована серверными флагами.";
      const availabilityStatus = availabilityWriteAvailable
        ? "Серверные настройки РАЗРЕШАЮТ worker отправить отдельные изменения доступности после подтверждённой обработки OTTO."
        : "Изменения доступности в OTTO сейчас заблокированы серверными флагами.";
      const actionLabel = action === "start" ? "Запустить" : action === "resume" ? "Продолжить" : "Повторить очередь";
      const timing = action === "retry"
        ? "Повтор получат только задания, разрешённые текущим состоянием и серверными проверками; уже просроченные задания могут стать доступными сразу."
        : "Вызовы начнутся после наступления времени заданий и прохождения серверных проверок; при продолжении просроченные задания могут стать доступными сразу.";
      if (!window.confirm(`${actionLabel} расписание OttoRestart?\n\n${productStatus}\n${availabilityStatus}\n\n${timing}`)) return;
    }
    if (action === "cancel" && !window.confirm("Отменить будущие задания этого расписания? Уже начатые внешние операции не затрагиваются.")) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await api.controlDripBatch(batch.id, account, action, batch.version, idempotencyKey(action));
      if (["start", "resume", "retry"].includes(action)) {
        const productWriteAvailable = Boolean(currentSettings?.mode === "PRODUCTION"
          && currentSettings.otto.readEnabled && currentSettings.publication.productWriteEnabled);
        const actionLabel = action === "start" ? "Расписание запущено" : action === "resume" ? "Расписание продолжено" : "Повтор очереди запрошен";
        setNotice(productWriteAvailable
          ? `${actionLabel}. При наступлении времени worker повторно проверит QC и флаги; отправка товара в OTTO возможна.`
          : `${actionLabel}. Отправка товара в OTTO заблокирована текущими серверными флагами.`);
      } else {
        setNotice(`Состояние расписания обновлено: ${batchStatusLabels[batch.status]}. Внешние операции не запускались этим действием.`);
      }
      setDetail(null);
      setReload((value) => value + 1);
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }

  async function showItems(batch: RestartDripBatch, page = 1) {
    setBusy(true); setError("");
    try {
      const result = await api.listDripBatchItems(batch.id, account, { page, pageSize: PAGE_SIZE });
      if (requestAccount.current !== account) return;
      setDetail({ batchId: batch.id, items: result.items, page: result.page.page, totalPages: result.page.totalPages });
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }

  function changeScheduleField(change: () => void) {
    change();
    setPreview(null);
  }

  return <section className="page-section queue-scheduler-page production-drip-page">
    <div className="page-heading">
      <div><div className="eyebrow">OTTORESTART · DRIP FEED</div><h1>Очередь и расписание</h1>
        <p>Расписание и задания хранятся в backend OttoRestart. Внешние действия определяются серверными флагами и проверяются worker перед выполнением.</p></div>
      <span className="phase-chip"><ShieldCheck size={13} /> {currentSettings?.mode || (settingsError ? "Конфигурация недоступна" : "Проверяем настройки")}</span>
    </div>
    <WorkflowStepper active="queue" />

    {error ? <ApiState kind="error" description={error} onRetry={() => setReload((value) => value + 1)} /> : null}
    {notice ? <p className="production-drip-notice" role="status">{notice}</p> : null}

    <section className="surface-card production-drip-card" aria-label="Текущие серверные разрешения">
      <div className="card-heading"><div><h2>Разрешения backend · {account}</h2>
        <p>{settingsLoading ? "Читаем текущую серверную конфигурацию…" : settingsError ? "Старт, продолжение и повтор очереди заблокированы, пока конфигурация не подтверждена." : "Статус отражает настройки backend на момент последнего чтения; worker повторно проверяет флаги при выполнении."}</p></div>
        <button type="button" className="review-secondary" disabled={settingsLoading} onClick={() => setSettingsReload((value) => value + 1)}><RefreshCw size={14} /> Обновить флаги</button></div>
      {settingsError ? <ApiState kind="error" description={settingsError} onRetry={() => setSettingsReload((value) => value + 1)} /> : null}
      {currentSettings ? <div className="production-drip-section-heading"><div>
        <span>OTTO чтение: <strong>{currentSettings.otto.readEnabled ? "включено" : "выключено"}</strong></span>
        <span> · Отправка товара: <strong>{currentSettings.publication.productWriteEnabled ? "разрешена флагом" : "заблокирована"}</strong></span>
        <span> · Доступность: <strong>{currentSettings.publication.availabilityWriteEnabled ? "разрешена флагом" : "заблокирована"}</strong></span>
      </div></div> : null}
    </section>

    <DurableBackgroundJobs account={account} />

    <section className="surface-card production-drip-card">
      <div className="card-heading"><div><h2>Новое расписание</h2><p>Доступны только сохранённые READY-операции с актуальным PASSED_AUTO QC.</p></div>
        <span className="production-drip-lock"><ShieldCheck size={14} /> Старт требует подтверждения флагов</span></div>
      <div className="production-drip-form">
        <label>Название<input value={name} maxLength={160} onChange={(event) => changeScheduleField(() => setName(event.target.value))} placeholder="Например, JV · 3M · ноябрь" /></label>
        <label>Дата начала<input type="date" value={startDate} onChange={(event) => changeScheduleField(() => setStartDate(event.target.value))} /></label>
        <label>Время<input type="time" value={dailyTime} onChange={(event) => changeScheduleField(() => setDailyTime(event.target.value))} /></label>
        <label>IANA timezone<input value={timezone} maxLength={100} onChange={(event) => changeScheduleField(() => setTimezone(event.target.value))} placeholder="Europe/Berlin" /></label>
        <label>Дней<input type="number" min={1} max={30} value={totalDays} onChange={(event) => changeScheduleField(() => setTotalDays(Math.max(1, Math.min(30, Number(event.target.value) || 1))))} /></label>
      </div>

      {loading ? <ApiState kind="loading" label="Загружаем READY-операции и расписания…" /> : candidateData ? <>
        <div className="production-drip-section-heading"><div><h3>READY-операции · {account}</h3><p>Выбрано {selectedProducts.length}. Период и фабрика должны совпадать.</p></div>
          <button type="button" className="review-secondary" disabled={busy} onClick={() => setReload((value) => value + 1)}><RefreshCw size={14} /> Обновить</button></div>
        {!candidateData.items.length ? <ApiState kind="empty" title="READY-операций нет" description="Здесь появятся только операции, которые прошли backend QC." /> : <>
          <div className="restart-table-wrap production-drip-table-wrap"><table className="restart-table"><thead><tr><th>Выбор</th><th>Исходный товар</th><th>Период</th><th>Фабрика</th><th>Target SKU / GTIN</th><th>QC</th></tr></thead>
            <tbody>{candidateData.items.map((product) => <tr key={product.id}>
              <td><input type="checkbox" aria-label={`Выбрать ${product.sku ?? product.ean ?? product.id}`} checked={selectedIds.has(product.id)} disabled={busy || product.status !== "READY" || !product.operationId} onChange={(event) => updateSelection(product, event.target.checked)} /></td>
              <td><strong>{product.title || "Название отсутствует"}</strong><small>{product.sku || "SKU отсутствует"} · EAN {product.ean || "—"}</small></td>
              <td>{product.period || "—"}</td><td>{product.factoryName || product.factoryId || "—"}</td>
              <td>{product.targetSku || "—"}<small>{product.newEan || "GTIN не назначен"}</small></td>
              <td><span className="production-drip-ready">{product.status}</span></td>
            </tr>)}</tbody>
          </table></div>
          <div className="pagination-bar production-drip-pagination"><span>Страница {candidateData.page.page} из {Math.max(1, candidateData.page.totalPages)} · всего {candidateData.page.total.toLocaleString("ru-RU")}</span><div>
            <button type="button" aria-label="Предыдущая страница кандидатов" disabled={candidatePage <= 1 || busy} onClick={() => setCandidatePage((page) => page - 1)}><ChevronLeft size={16} /></button>
            <button type="button" aria-label="Следующая страница кандидатов" disabled={candidatePage >= candidateData.page.totalPages || busy} onClick={() => setCandidatePage((page) => page + 1)}><ChevronRight size={16} /></button>
          </div></div>
        </>}
      </> : null}

      {preview ? <div className="production-drip-preview" role="region" aria-label="Предварительное распределение расписания">
        <div><strong>Предварительное распределение</strong><span>{preview.itemCount} операций · {preview.period} · {preview.timezone}</span></div>
        <ul>{preview.distribution.map((day) => <li key={day.dayNumber}><span>День {day.dayNumber}</span><span>{formattedDate(day.scheduledAt, preview.timezone)}</span><strong>{day.count}</strong></li>)}</ul>
        <p>Preview не сохраняется и не вызывает внешние сервисы.</p>
      </div> : null}
      <div className="production-drip-actions">
        <span>{selectedProducts.length ? `${selectedProducts.length} выбрано${sameCohort ? ` · ${selectedProducts[0].period} · ${selectedProducts[0].factoryName || selectedProducts[0].factoryId || "фабрика не указана"}` : " · смешанные фабрики/периоды"}` : "Выберите READY-операции для preview."}</span>
        <button type="button" className="review-secondary" disabled={busy || loading || !canPreview} onClick={() => void previewSchedule()}><CalendarClock size={14} /> Preview</button>
        <button type="button" className="primary-action" disabled={busy || !preview || !canPreview} onClick={() => void createDraft()}><ListChecks size={14} /> Сохранить черновик</button>
      </div>
    </section>

    <section className="surface-card production-drip-card">
      <div className="card-heading"><div><h2>Сохранённые расписания · {account}</h2><p>Постраничный список backend batches; управление меняет только локальные задания OttoRestart.</p></div></div>
      {loading && !batches.length ? <ApiState kind="loading" label="Загружаем расписания…" /> : !batches.length ? <ApiState kind="empty" title="Сохранённых расписаний пока нет" description="После создания черновики появятся здесь." /> : <div className="restart-table-wrap production-drip-table-wrap"><table className="restart-table"><thead><tr><th>Название</th><th>Аккаунт / когорта</th><th>Начало</th><th>Товары</th><th>Статус</th><th>Действия</th></tr></thead>
        <tbody>{batches.map((batch) => <tr key={batch.id}>
          <td><strong>{batch.name}</strong><small>{batch.id.slice(0, 8)} · создана {formattedDate(batch.createdAt)}</small></td>
          <td>{batch.account} · {batch.period}<small>{batch.factoryId || "Фабрика не указана"}</small></td>
          <td>{batch.scheduledStartAt ? formattedDate(batch.scheduledStartAt, batch.timezone) : "—"}<small>{batch.dailyTime || ""} {batch.timezone || ""}</small></td>
          <td>{batch.itemCount}</td><td><span className={`status-pill production-drip-status status-${batch.status.toLowerCase()}`}>{batchStatusLabels[batch.status]}</span></td>
          <td><div className="production-drip-row-actions">
            <button type="button" className="review-secondary" disabled={busy || batch.status === "DRAFT"} onClick={() => void (detail?.batchId === batch.id ? setDetail(null) : showItems(batch))}>Состав</button>
            {batch.status === "DRAFT" ? <button type="button" className="review-secondary" disabled={busy || settingsLoading || !currentSettings || Boolean(settingsError)} onClick={() => void controlBatch(batch, "start")}><Play size={13} /> Старт</button> : null}
            {batch.status === "SCHEDULED" || batch.status === "ACTIVE" ? <button type="button" className="review-secondary" disabled={busy} onClick={() => void controlBatch(batch, "pause")}><CirclePause size={13} /> Пауза</button> : null}
            {batch.status === "PAUSED" ? <button type="button" className="review-secondary" disabled={busy || settingsLoading || !currentSettings || Boolean(settingsError)} onClick={() => void controlBatch(batch, "resume")}><CirclePlay size={13} /> Продолжить</button> : null}
            {batch.status !== "CANCELLED" && batch.status !== "COMPLETED" ? <button type="button" className="review-secondary" disabled={busy} onClick={() => void controlBatch(batch, "cancel")}><X size={13} /> Отменить</button> : null}
            {batch.status === "SCHEDULED" || batch.status === "ACTIVE" || batch.status === "PAUSED" ? <button type="button" className="review-secondary" disabled={busy || settingsLoading || !currentSettings || Boolean(settingsError)} onClick={() => void controlBatch(batch, "retry")}><RefreshCw size={13} /> Повторить очередь</button> : null}
          </div></td>
        </tr>)}</tbody></table></div>}
      {detail ? <div className="production-drip-item-detail">
        <div className="production-drip-section-heading"><div><h3>Элементы расписания</h3><p>READY_TO_SUBMIT означает готовность к следующему backend-этапу, не отправку в OTTO.</p></div>
          <button type="button" className="review-secondary" onClick={() => setDetail(null)}>Закрыть</button></div>
        {!detail.items.length ? <ApiState kind="empty" title="Элементов пока нет" description="Состав черновика не создаётся до явного старта расписания." /> : <div className="restart-table-wrap production-drip-table-wrap"><table className="restart-table"><thead><tr><th>Время</th><th>Исходный SKU / EAN</th><th>Target SKU</th><th>Ревизия</th><th>Состояние</th><th>Попытки</th></tr></thead>
          <tbody>{detail.items.map((item) => <tr key={item.id}><td>{formattedDate(item.scheduledAt)}</td><td>{item.sourceSku || "—"}<small>EAN {item.sourceEan}</small></td><td>{item.targetSku || "—"}</td><td>v{item.revisionNumber} · {item.payloadHash.slice(0, 12)}</td><td>{itemStatusLabel(item.status)}</td><td>{item.releaseAttempts + item.totalAttempts}</td></tr>)}</tbody>
        </table></div>}
        {detail.totalPages > 1 ? <div className="pagination-bar production-drip-pagination"><span>Страница {detail.page} из {detail.totalPages}</span><div>
          <button type="button" aria-label="Предыдущая страница элементов" disabled={busy || detail.page <= 1} onClick={() => { const batch = batches.find((row) => row.id === detail.batchId); if (batch) void showItems(batch, detail.page - 1); }}><ChevronLeft size={16} /></button>
          <button type="button" aria-label="Следующая страница элементов" disabled={busy || detail.page >= detail.totalPages} onClick={() => { const batch = batches.find((row) => row.id === detail.batchId); if (batch) void showItems(batch, detail.page + 1); }}><ChevronRight size={16} /></button>
        </div></div> : null}
      </div> : null}
      {batchPageData && batchPageData.page.totalPages > 1 ? <div className="pagination-bar production-drip-pagination"><span>Страница {batchPageData.page.page} из {batchPageData.page.totalPages} · всего {batchPageData.page.totalItems.toLocaleString("ru-RU")}</span><div>
        <button type="button" aria-label="Предыдущая страница расписаний" disabled={busy || batchPage <= 1} onClick={() => setBatchPage((page) => page - 1)}><ChevronLeft size={16} /></button>
        <button type="button" aria-label="Следующая страница расписаний" disabled={busy || batchPage >= batchPageData.page.totalPages} onClick={() => setBatchPage((page) => page + 1)}><ChevronRight size={16} /></button>
      </div></div> : null}
    </section>
  </section>;
}
