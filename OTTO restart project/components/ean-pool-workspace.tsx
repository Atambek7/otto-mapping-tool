"use client";

import { useEffect, useMemo, useState } from "react";
import { FileSpreadsheet, RefreshCw, ShieldCheck } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { RestartApiClient } from "@/features/api/client";
import type { RestartAccount, RestartEanEntry } from "@/features/api/types";

export function EanPoolWorkspace() {
  const [account, setAccount] = useState<RestartAccount | "">("");
  const [file, setFile] = useState<File | null>(null);
  const [entries, setEntries] = useState<RestartEanEntry[]>([]);
  const [preview, setPreview] = useState<{ count: number; invalid: string[]; duplicates: string[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const client = useMemo(() => {
    try { return new RestartApiClient(); } catch { return null; }
  }, []);

  async function loadEntries(selected: RestartAccount) {
    setError("");
    try {
      const api = client ?? new RestartApiClient();
      const result = await api.listEanPool(selected, { page: 1, pageSize: 50 });
      setEntries(result.items);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось загрузить пул EAN.");
    }
  }

  useEffect(() => {
    if (account) void loadEntries(account);
    else setEntries([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account]);

  async function previewFile() {
    if (!file || !account) return setError("Выберите аккаунт и файл с новыми EAN.");
    setBusy(true); setError(""); setNotice(""); setPreview(null);
    try {
      const api = client ?? new RestartApiClient();
      await api.getCsrfToken();
      const result = await api.previewImportFile(file, account);
      setPreview({ count: result.uniqueCount, invalid: result.invalidEans, duplicates: result.duplicates ?? [] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Проверка файла не выполнена.");
    } finally { setBusy(false); }
  }

  async function importFile() {
    if (!file || !account || !preview || preview.invalid.length || preview.duplicates.length || preview.count === 0) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const api = client ?? new RestartApiClient();
      // Parse again at the API boundary so the imported values are exactly those validated by the backend.
      const parsed = await api.previewImportFile(file, account);
      const result = await api.importEans(account, parsed.sourceEans);
      setNotice(`Импортировано EAN: ${result.importedCount}. Статус: AVAILABLE.`);
      setPreview(null); setFile(null);
      await loadEntries(account);
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
            <select value={account} onChange={(event) => { setAccount(event.target.value as RestartAccount | ""); setPreview(null); }}>
              <option value="">Выберите JV или XL</option><option value="JV">JV Möbel (JV)</option><option value="XL">XL Möbel (XL)</option>
            </select>
          </label>
          <label className="field-label import-file">Файл с новыми EAN
            <input accept=".xlsx,.csv,.txt" type="file" onChange={(event) => { setFile(event.target.files?.[0] ?? null); setPreview(null); }} />
            <small>{file ? `${file.name} · ${(file.size / 1024).toFixed(1)} KB` : "XLSX, CSV или TXT · до 20 MB"}</small>
          </label>
        </div>
        <div className="import-actions">
          <button className="primary-action button-action" disabled={busy || !account || !file} onClick={() => void previewFile()} type="button">
            {busy ? <RefreshCw className="is-spinning" size={15} /> : <ShieldCheck size={15} />} Проверить пул
          </button>
          {preview ? <span className="inline-note">Проверено уникальных: {preview.count} · без вызовов OTTO/Aftercool</span> : null}
        </div>
        {error ? <ApiState kind="error" description={error} /> : null}
        {notice ? <div className="success-banner" role="status">{notice}</div> : null}
        {preview ? <div className="preview-result">
          {preview.invalid.length || preview.duplicates.length ? <div className="issue-list"><strong>Файл содержит ошибки.</strong>
            {preview.invalid.length ? <p>Некорректные EAN: {preview.invalid.slice(0, 20).join(", ")}</p> : null}
            {preview.duplicates.length ? <p>Дубли: {preview.duplicates.slice(0, 20).join(", ")}</p> : null}</div> : <p className="preview-ok">EAN прошли проверку формата, контрольной цифры и дублей внутри файла.</p>}
          <div className="preview-footer"><span>Импортированные номера получат статус AVAILABLE. Резервирования нет.</span>
            <button className="primary-action button-action" disabled={busy || Boolean(preview.invalid.length || preview.duplicates.length) || preview.count === 0} onClick={() => void importFile()} type="button">
              Импортировать EAN
            </button>
          </div>
        </div> : null}
      </section>

      <section className="surface-card batches-list-card">
        <div className="card-heading"><div><h2>EAN-пул</h2><p>{account ? `Аккаунт ${account} · первые 50 номеров` : "Выберите аккаунт, чтобы загрузить пул"}</p></div>
          {account ? <button className="icon-button" aria-label="Обновить пул EAN" onClick={() => void loadEntries(account)} type="button"><RefreshCw size={15} /></button> : null}
        </div>
        {account && entries.length ? <div className="restart-table-wrap"><table className="restart-table"><thead><tr><th>EAN</th><th>Статус</th><th>Импортирован</th></tr></thead>
          <tbody>{entries.map((entry) => <tr key={entry.ean}><td className="mono-value">{entry.ean}</td><td><span className="status-pill">{entry.status}</span></td><td>{new Date(entry.importedAt).toLocaleString("ru-RU")}</td></tr>)}</tbody>
        </table></div> : account ? <div className="table-empty">В этом аккаунте пока нет EAN.</div> : null}
      </section>
    </div>
  );
}
