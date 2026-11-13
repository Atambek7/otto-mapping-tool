"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { RefreshCw } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { RestartApiClient } from "@/features/api/client";
import type { RestartAiStatus, RestartProduct, RestartProductListResponse } from "@/features/api/types";
import { useRestartAccount } from "@/features/state/restart-account";

type ViewKind = "review" | "quality";
type OperationFeedback = { message: string; codes: string[] };

export function ProductionOperationsWorkspace({ view = "review" }: { view?: ViewKind }) {
  const { account, ready, choose } = useRestartAccount();
  const [data, setData] = useState<RestartProductListResponse | null>(null);
  const [page, setPage] = useState(1);
  const [queryDraft, setQueryDraft] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [operationFeedback, setOperationFeedback] = useState<Record<string, OperationFeedback>>({});
  const [aiStatus, setAiStatus] = useState<RestartAiStatus | null>(null);
  const [aiStatusLoading, setAiStatusLoading] = useState(false);
  const [aiBusyId, setAiBusyId] = useState("");
  const [titleSuggestions, setTitleSuggestions] = useState<Record<string, string>>({});
  const [aiError, setAiError] = useState("");
  const [reload, setReload] = useState(0);
  const client = useMemo(() => new RestartApiClient(), []);

  const load = useCallback(async (signal: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      const result = await client.listProducts(account, { page, pageSize: 50, query, includeMetrics: false, signal });
      if (!signal.aborted) setData(result);
    } catch (cause) {
      if (!signal.aborted) setError(cause instanceof Error ? cause.message : "Не удалось загрузить кандидатов.");
    } finally {
      if (!signal.aborted) setLoading(false);
    }
  }, [account, client, page, query]);

  useEffect(() => {
    if (!ready) return;
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [ready, load, reload]);

  useEffect(() => {
    if (!ready) return;
    const controller = new AbortController();
    setAiStatus(null);
    setAiStatusLoading(true);
    setAiError("");
    void client.getAiStatus(account, controller.signal).then((status) => {
      if (!controller.signal.aborted) setAiStatus(status);
    }).catch((cause) => {
      if (!controller.signal.aborted) {
        setAiError(cause instanceof Error ? cause.message : "Не удалось прочитать статус AI.");
      }
    }).finally(() => {
      if (!controller.signal.aborted) setAiStatusLoading(false);
    });
    return () => controller.abort();
  }, [account, client, ready]);

  async function createOperation(product: RestartProduct) {
    if (busyId) return;
    if (product.dataScope !== "PRODUCTION" || product.classification !== "EXACT_CODE" || product.episodeState !== "OPEN") {
      setError("Создать операцию можно только для открытого production-кандидата с точным кодом.");
      return;
    }
    const confirmed = window.confirm(
      `Создать внутреннюю Restart-операцию для ${product.ean ?? product.sku ?? product.candidateEpisodeId}? Это сохранит операцию в базе. GPT, OTTO и Aftercool не вызываются; публикация не запускается.`,
    );
    if (!confirmed) return;
    setBusyId(product.candidateEpisodeId);
    setError("");
    setNotice("");
    try {
      const operation = await client.createOperation(
        account,
        product.candidateEpisodeId,
        window.crypto.randomUUID(),
      );
      setData((current) => current ? {
        ...current,
        items: current.items.map((row) => row.candidateEpisodeId === product.candidateEpisodeId
          ? { ...row, operationId: operation.id, operationStatus: operation.status, operationVersion: operation.version,
            currentRevisionNumber: operation.revisionNumber }
          : row),
      } : current);
      setNotice(`Операция ${operation.id.slice(0, 8)} создана как DRAFT. Подготовка, проверка QC и отправка — отдельные этапы.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось создать операцию.");
    } finally {
      setBusyId("");
    }
  }

  async function prepareOperation(product: RestartProduct) {
    if (busyId || !product.operationId || product.operationVersion == null) return;
    setBusyId(product.candidateEpisodeId);
    setError("");
    setNotice("");
    try {
      const result = await client.prepareOperation(
        account,
        product.operationId,
        product.operationVersion,
        window.crypto.randomUUID(),
      );
      const codes = result.readinessGaps ?? [];
      setOperationFeedback((current) => ({
        ...current,
        [product.candidateEpisodeId]: {
          message: codes.length
            ? `Ревизия ${result.revisionNumber} подготовлена с пробелами.`
            : `Ревизия ${result.revisionNumber} подготовлена; запустите QC.`,
          codes,
        },
      }));
      setReload((value) => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось подготовить ревизию.");
    } finally {
      setBusyId("");
    }
  }

  async function runQc(product: RestartProduct) {
    if (busyId || !product.operationId || product.operationVersion == null) return;
    setBusyId(product.candidateEpisodeId);
    setError("");
    setNotice("");
    try {
      const result = await client.runOperationQc(
        account,
        product.operationId,
        product.operationVersion,
        window.crypto.randomUUID(),
      );
      const codes = (result.issues ?? []).map((issue) => issue.code);
      setOperationFeedback((current) => ({
        ...current,
        [product.candidateEpisodeId]: {
          message: result.outcome === "PASSED_AUTO"
            ? "QC пройден. Операция готова для ручного добавления в Drip Feed."
            : `QC не разрешил READY: ${result.outcome}.`,
          codes,
        },
      }));
      setReload((value) => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось выполнить QC.");
    } finally {
      setBusyId("");
    }
  }

  async function suggestTitle(product: RestartProduct) {
    if (aiBusyId || !aiStatus?.available || product.dataScope !== "PRODUCTION"
      || product.classification !== "EXACT_CODE" || product.episodeState !== "OPEN"
      || !product.title?.trim()) return;
    setAiBusyId(product.candidateEpisodeId);
    setAiError("");
    try {
      const result = await client.suggestTitle(account, product.title.trim());
      setTitleSuggestions((current) => ({ ...current, [product.candidateEpisodeId]: result.title }));
    } catch (cause) {
      setAiError(cause instanceof Error ? cause.message : "Не удалось получить черновик названия.");
    } finally {
      setAiBusyId("");
    }
  }

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPage(1);
    setQuery(queryDraft.trim());
  }

  const title = view === "quality" ? "Проверка операций" : "Кандидаты Restart";

  return <section className="page-section">
    <div className="page-heading">
      <div><div className="eyebrow">{view === "quality" ? "КОНТРОЛЬ КАЧЕСТВА" : "ПОДГОТОВКА RESTART"}</div><h1>{title}</h1>
        <p>Подготовка читает подтверждённые данные OTTO и Aftercool и создаёт версию карточки. QC проверяет эту версию локально; ни один из этих шагов не публикует товар.</p></div>
      <span className="phase-chip"><span /> API · {account}</span>
    </div>
    <div className="workspace-stack">
      <section className="surface-card batches-list-card">
        <div className="card-heading"><div><h2>{view === "quality" ? "Открытые source-кандидаты" : "Кандидаты по аккаунту"}</h2>
          <p>Сначала создайте DRAFT, затем отдельно подготовьте ревизию и запустите QC. Только операция со статусом READY доступна для ручного планирования в Drip Feed.</p></div>
          <button className="icon-button" type="button" aria-label="Обновить кандидатов" disabled={loading || Boolean(busyId)} onClick={() => setReload((value) => value + 1)}><RefreshCw size={15} /></button>
        </div>
        {aiStatusLoading ? <p className="inline-note">Читаем статус AI-конфигурации; этот запрос не вызывает OpenAI.</p> : null}
        {aiStatus && !aiStatus.available ? <p className="inline-note">AI-подсказки выключены или не настроены; ключ не проверяется внешним запросом.</p> : null}
        {aiError ? <p className="inline-note" role="alert">{aiError}</p> : null}
        <div className="import-form-grid">
          <label className="field-label">Аккаунт OTTO
            <select value={account} onChange={(event) => { choose(event.target.value as "JV" | "XL"); setPage(1); setData(null); }}>
              <option value="JV">JV Möbel (JV)</option><option value="XL">XL Möbel (XL)</option>
            </select>
          </label>
          <form className="item-search" onSubmit={search}><input aria-label="Поиск кандидатов" placeholder="Старый EAN, SKU или название" value={queryDraft} onChange={(event) => setQueryDraft(event.target.value)} /><button className="primary-action button-action" type="submit">Поиск</button></form>
        </div>
        {notice ? <div className="success-banner" role="status">{notice}</div> : null}
        {loading ? <ApiState kind="loading" label="Загружаем production-кандидатов…" /> : null}
        {error ? <ApiState kind="error" description={error} onRetry={() => setReload((value) => value + 1)} /> : null}
        {!loading && !error && data?.items.length === 0 ? <ApiState kind="empty" title="Кандидаты не найдены" description="Для аккаунта и фильтра нет открытых кандидатов." /> : null}
        {data?.items.length ? <div className="restart-table-wrap"><table className="restart-table">
          <thead><tr><th>Старый товар</th><th>Период</th><th>Источник</th><th>Состояние</th><th>Операция</th><th>Действие</th></tr></thead>
          <tbody>{data.items.map((product) => <tr key={product.candidateEpisodeId}>
            <td><strong>{product.title || "Без названия в source snapshot"}</strong><br /><span className="mono-value">EAN {product.ean || "—"} · SKU {product.sku || "—"}</span>
              {titleSuggestions[product.candidateEpisodeId] ? <p className="inline-note">Черновик AI: {titleSuggestions[product.candidateEpisodeId]} · проверьте вручную, не сохранён</p> : null}</td>
            <td>{product.period || "—"}</td><td>{product.source} · {product.factoryName || product.factoryId || "—"}</td>
            <td><span className="status-pill">{product.dataScope} / {product.episodeState} / {product.classification}</span></td>
            <td>{product.operationId ? <><span className="status-pill">{product.operationStatus || "DRAFT"}</span><br /><span className="mono-value">{product.operationId.slice(0, 8)} · v{product.operationVersion ?? "—"} · rev {product.currentRevisionNumber ?? 0}</span>
              {operationFeedback[product.candidateEpisodeId] ? <p className="inline-note" role="status">{operationFeedback[product.candidateEpisodeId].message}{operationFeedback[product.candidateEpisodeId].codes.length ? <><br />{operationFeedback[product.candidateEpisodeId].codes.join(" · ")}</> : null}</p> : null}</> : "Не создана"}</td>
            <td>{product.operationId ? <div className="queue-row-actions">
              {product.operationStatus !== "READY" ? <button type="button" className="queue-control-button" disabled={Boolean(busyId) || product.operationVersion == null} onClick={() => void prepareOperation(product)}>{busyId === product.candidateEpisodeId ? "Обрабатываем…" : "Подготовить ревизию"}</button> : <span className="inline-note">Готово к ручному планированию</span>}
              {(product.currentRevisionNumber ?? 0) > 0 && product.operationStatus !== "READY" ? <button type="button" className="queue-control-button" disabled={Boolean(busyId) || product.operationVersion == null} onClick={() => void runQc(product)}>Запустить QC</button> : null}
            </div> : <button type="button" className="primary-action button-action" disabled={Boolean(busyId) || product.dataScope !== "PRODUCTION" || product.episodeState !== "OPEN" || product.classification !== "EXACT_CODE"} onClick={() => void createOperation(product)}>{busyId === product.candidateEpisodeId ? "Сохраняем…" : "Создать DRAFT"}</button>}
              {aiStatus?.available && product.title?.trim() && product.dataScope === "PRODUCTION" && product.episodeState === "OPEN" && product.classification === "EXACT_CODE" ? <button type="button" className="primary-action button-action" disabled={Boolean(aiBusyId)} onClick={() => void suggestTitle(product)}>{aiBusyId === product.candidateEpisodeId ? "Запрашиваем AI…" : "Черновик названия AI"}</button> : null}</td>
          </tr>)}</tbody>
        </table></div> : null}
        {data?.page ? <div className="pagination-bar"><span>Страница {data.page.page} из {Math.max(1, data.page.totalPages)} · всего {data.page.total.toLocaleString("ru-RU")}</span><div>
          <button type="button" disabled={page <= 1 || loading} onClick={() => setPage((value) => value - 1)}>Назад</button>
          <button type="button" disabled={page >= data.page.totalPages || loading} onClick={() => setPage((value) => value + 1)}>Дальше</button>
        </div></div> : null}
      </section>
      <p className="safe-note">Подготовка использует только account-scoped чтение OTTO/Aftercool и локально резервирует GTIN после успешных проверок. QC не отправляет данные наружу. Публикация не начинается автоматически: её разрешают только ручной запуск Drip Feed и отдельные серверные write-флаги.</p>
    </div>
  </section>;
}
