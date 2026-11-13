"use client";

import { useEffect, useMemo, useState } from "react";
import { FileSpreadsheet, RefreshCw, ShieldCheck } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { RestartApiClient } from "@/features/api/client";
import type { RestartAccount, RestartEanEntry, RestartEanPoolPreview, RestartPageInfo } from "@/features/api/types";

export function EanPoolWorkspace() {
  const [account, setAccount] = useState<RestartAccount | "">("");
  const [file, setFile] = useState<File | null>(null);
  const [entries, setEntries] = useState<RestartEanEntry[]>([]);
  const [preview, setPreview] = useState<(RestartEanPoolPreview & { idempotencyKey: string }) | null>(null);
  const [busy, setBusy] = useState(false);
  const [listLoading, setListLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageInfo, setPageInfo] = useState<RestartPageInfo | null>(null);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const client = useMemo(() => {
    try { return new RestartApiClient(); } catch { return null; }
  }, []);

  useEffect(() => {
    if (!account) { setEntries([]); setPageInfo(null); setListLoading(false); return; }
    const controller = new AbortController();
    setEntries([]); setPageInfo(null); setListLoading(true); setError("");
    const api = client ?? new RestartApiClient();
    void api.listEanPool(account, { page, pageSize: 50, signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted) { setEntries(result.items); setPageInfo(result.page); }
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Не удалось загрузить пул EAN.");
    }).finally(() => { if (!controller.signal.aborted) setListLoading(false); });
    return () => controller.abort();
  }, [account, page, reload, client]);

  async function previewFile() {
    if (!file || !account) return setError("Выберите аккаунт и файл с новыми EAN.");
    setBusy(true); setError(""); setNotice(""); setPreview(null);
    try {
      const api = client ?? new RestartApiClient();
      await api.getCsrfToken();
      const result = await api.previewEanPoolFile(account, file);
      const idempotencyKey = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID() : `ean-${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
      setPreview({ ...result, idempotencyKey });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Проверка файла не выполнена.");
    } finally { setBusy(false); }
  }

  async function importFile() {
    if (!file || !account || !preview || preview.account !== account || preview.invalidEans.length || preview.duplicates.length || preview.uniqueCount === 0) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const api = client ?? new RestartApiClient();
      const result = await api.importEans(account, preview.uniqueEans, preview.idempotencyKey);
      setNotice(`Импортировано: ${result.importedCount}. На карантине: ${result.ineligibleCount}; без подтверждённого статуса: ${result.unverifiedCount}.`);
      setPreview(null); setFile(null);
      setPage(1); setReload((value) => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось импортировать EAN.");
    } finally { setBusy(false); }
  }

  return (
    <div className="workspace-stack">
      <section className="surface-card import-card">
        <div className="card-heading"><div><h2>Загрузка новых EAN</h2><p>Пул будет храниться отдельно от партий. Dry-Run не резервирует и не расходует EAN.</p></div><FileSpreadsheet size={18} className="muted-icon" /></div>
        <div className="import-form-grid ean-import-grid">
          <label className="field-label">Аккаунт OTTO
            <select disabled={busy} value={account} onChange={(event) => { setAccount(event.target.value as RestartAccount | ""); setPage(1); setEntries([]); setPageInfo(null); setPreview(null); setNotice(""); }}>
              <option value="">Выберите JV или XL</option><option value="JV">JV Möbel (JV)</option><option value="XL">XL Möbel (XL)</option>
            </select>
          </label>
          <label className="field-label import-file">Файл с новыми EAN
            <input disabled={busy} accept=".xlsx,.csv,.txt" type="file" onChange={(event) => { setFile(event.target.files?.[0] ?? null); setPreview(null); }} />
            <small>{file ? `${file.name} · ${(file.size / 1024).toFixed(1)} KB` : "XLSX, CSV или TXT · до 20 MB"}</small>
          </label>
        </div>
        <div className="import-actions">
          <button className="primary-action button-action" disabled={busy || !account || !file} onClick={() => void previewFile()} type="button">
            {busy ? <RefreshCw className="is-spinning" size={15} /> : <ShieldCheck size={15} />} Проверить пул
          </button>
          {preview ? <span className="inline-note">Проверено уникальных: {preview.uniqueCount} · без вызовов OTTO/Aftercool</span> : null}
        </div>
        {error ? <ApiState kind="error" description={error} /> : null}
        {notice ? <div className="success-banner" role="status">{notice}</div> : null}
        {preview ? <div className="preview-result">
          {preview.invalidEans.length || preview.duplicates.length ? <div className="issue-list"><strong>Файл содержит ошибки.</strong>
            {preview.invalidEans.length ? <p>Некорректные EAN: {preview.invalidEans.slice(0, 20).join(", ")}</p> : null}
            {preview.duplicates.length ? <p>Дубли: {preview.duplicates.slice(0, 20).join(", ")}</p> : null}</div> : <p className="preview-ok">Проверены формат из 13 цифр и дубли внутри файла.</p>}
          <div className="preview-footer"><span>Проверяется формат 13 цифр и дубли в файле. Конфликты при импорте могут попасть на карантин; резервирования нет.</span>
            <button className="primary-action button-action" disabled={busy || Boolean(preview.invalidEans.length || preview.duplicates.length) || preview.uniqueCount === 0} onClick={() => void importFile()} type="button">
              Импортировать EAN
            </button>
          </div>
        </div> : null}
      </section>

      <section className="surface-card batches-list-card">
        <div className="card-heading"><div><h2>EAN-пул</h2><p>{account ? `Аккаунт ${account} · страница ${page}` : "Выберите аккаунт, чтобы загрузить пул"}</p></div>
          {account ? <button className="icon-button" aria-label="Обновить пул EAN" onClick={() => setReload((value) => value + 1)} type="button"><RefreshCw size={15} /></button> : null}
        </div>
        {listLoading ? <div className="table-empty">Загружаем EAN…</div> : null}
        {account && entries.length ? <div className="restart-table-wrap"><table className="restart-table"><thead><tr><th>EAN</th><th>Статус</th><th>Импортирован</th></tr></thead>
          <tbody>{entries.map((entry) => <tr key={entry.ean}><td className="mono-value">{entry.ean}</td><td><span className="status-pill">{entry.status}</span></td><td>{entry.importedAt ? new Date(entry.importedAt).toLocaleString("ru-RU") : "—"}</td></tr>)}</tbody>
        </table></div> : account && !listLoading && !error ? <div className="table-empty">В этом аккаунте пока нет EAN.</div> : null}
        {pageInfo && pageInfo.totalPages > 1 && <div className="catalog-pagination"><button type="button" className="catalog-action-btn" disabled={listLoading || page <= 1} onClick={() => setPage(page - 1)}>Назад</button><span>Страница {page} из {pageInfo.totalPages} · всего {pageInfo.totalItems}</span><button type="button" className="catalog-action-btn" disabled={listLoading || page >= pageInfo.totalPages} onClick={() => setPage(page + 1)}>Далее</button></div>}
      </section>
    </div>
  );
}
