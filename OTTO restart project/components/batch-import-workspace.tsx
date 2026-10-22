"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ArrowRight, FileSpreadsheet, RefreshCw, ShieldCheck } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { RestartApiClient } from "@/features/api/client";
import type { RestartAccount, RestartBatch, RestartBatchPreview, RestartPeriod } from "@/features/api/types";

function makeClient() {
  return new RestartApiClient();
}

export function BatchImportWorkspace() {
  const [account, setAccount] = useState<RestartAccount | "">("");
  const [period, setPeriod] = useState<RestartPeriod | "">("");
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<RestartBatchPreview | null>(null);
  const [batches, setBatches] = useState<RestartBatch[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const idempotencyKey = useRef<string | null>(null);

  const canCreate = Boolean(preview && preview.sourceEans.length && !preview.invalidEans.length && !preview.duplicateCount);
  const client = useMemo(() => {
    try { return makeClient(); } catch { return null; }
  }, []);

  async function loadBatches(selected: RestartAccount) {
    setError("");
    try {
      const api = client ?? makeClient();
      const result = await api.listBatches(selected, { page: 1, pageSize: 20 });
      setBatches(result.items);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось загрузить партии.");
    }
  }

  useEffect(() => {
    if (account) void loadBatches(account);
    else setBatches([]);
    // loadBatches is tied to the selected account/client in this workspace.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account]);

  function chooseFile(nextFile: File | null) {
    idempotencyKey.current = null;
    setFile(nextFile);
    setPreview(null);
    setNotice("");
    setError("");
    if (nextFile && !name) setName(nextFile.name.replace(/\.[^.]+$/, ""));
  }

  async function runPreview() {
    if (!file || !account) {
      setError("Выберите аккаунт и файл со старыми EAN.");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const api = client ?? makeClient();
      await api.getCsrfToken();
      const result = await api.previewImportFile(file, account, period || undefined);
      setPreview(result);
      idempotencyKey.current = null;
      if (!name.trim()) setName(file.name.replace(/\.[^.]+$/, ""));
    } catch (cause) {
      setPreview(null);
      setError(cause instanceof Error ? cause.message : "Предварительная проверка не выполнена.");
    } finally {
      setBusy(false);
    }
  }

  async function createBatch() {
    if (!canCreate || !preview || !account || !period || !name.trim()) {
      setError("Для создания укажите аккаунт, период и имя партии; Dry-Run должен пройти без ошибок.");
      return;
    }
    const confirmed = window.confirm(
      `Создать партию «${name.trim()}» для ${account} (${period}) из ${preview.sourceEans.length} EAN? Никакие EAN из нового пула не будут зарезервированы, данные в OTTO не отправляются.`,
    );
    if (!confirmed) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const api = client ?? makeClient();
      if (!client) await api.getCsrfToken();
      const batch = await api.createBatch(
        { account, period, name: name.trim(), sourceEans: preview.sourceEans },
        idempotencyKey.current ?? (idempotencyKey.current = window.crypto.randomUUID()),
      );
      setNotice(`Партия ${batch.name} создана. Импортировано строк: ${batch.itemCount}.`);
      idempotencyKey.current = null;
      setPreview(null);
      setFile(null);
      await loadBatches(account);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось создать партию.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="workspace-stack">
      <section className="surface-card import-card">
        <div className="card-heading">
          <div><h2>Импорт старых EAN</h2><p>Сначала проверьте файл. Dry-Run не сохраняет партию и не расходует новые EAN.</p></div>
          <FileSpreadsheet className="muted-icon" size={18} />
        </div>
        <div className="import-form-grid">
          <label className="field-label">Аккаунт OTTO
            <select value={account} onChange={(event) => { idempotencyKey.current = null; setAccount(event.target.value as RestartAccount | ""); setPreview(null); }}>
              <option value="">Выберите JV или XL</option><option value="JV">JV Möbel (JV)</option><option value="XL">XL Möbel (XL)</option>
            </select>
          </label>
          <label className="field-label">Период Restart
            <select value={period} onChange={(event) => { idempotencyKey.current = null; setPeriod(event.target.value as RestartPeriod | ""); setPreview(null); }}>
              <option value="">Выберите период</option><option value="3M">3 месяца</option><option value="12M">12 месяцев</option>
            </select>
          </label>
          <label className="field-label import-name">Название партии
            <input maxLength={160} value={name} onChange={(event) => { idempotencyKey.current = null; setName(event.target.value); }} placeholder="Например, Restart октябрь JV" />
          </label>
          <label className="field-label import-file">Файл со старыми EAN
            <input accept=".xlsx,.csv,.txt" type="file" onChange={(event) => chooseFile(event.target.files?.[0] ?? null)} />
            <small>{file ? `${file.name} · ${(file.size / 1024).toFixed(1)} KB` : "XLSX, CSV или TXT · до 20 MB · максимум 10 000 строк"}</small>
          </label>
        </div>
        <div className="import-actions">
          <button className="primary-action button-action" disabled={busy || !file || !account} onClick={() => void runPreview()} type="button">
            {busy ? <RefreshCw className="is-spinning" size={15} /> : <ShieldCheck size={15} />} Проверить файл
          </button>
          {preview ? <span className="inline-note">Проверена только структура файла и контрольная цифра EAN.</span> : null}
        </div>

        {error ? <ApiState kind="error" description={error} /> : null}
        {notice ? <div className="success-banner" role="status">{notice}</div> : null}

        {preview ? <div className="preview-result">
          <div className="preview-summary">
            <div><strong>{preview.inputCount}</strong><span>строк прочитано</span></div>
            <div><strong>{preview.uniqueCount}</strong><span>уникальных EAN</span></div>
            <div className={preview.duplicateCount ? "has-issue" : ""}><strong>{preview.duplicateCount}</strong><span>дублей</span></div>
            <div className={preview.invalidEans.length ? "has-issue" : ""}><strong>{preview.invalidEans.length}</strong><span>не прошли проверку</span></div>
          </div>
          {preview.duplicates?.length ? <p className="issue-list"><strong>Дубли:</strong> {preview.duplicates.slice(0, 20).join(", ")}</p> : null}
          {preview.invalidEans.length ? <p className="issue-list"><strong>Некорректные значения:</strong> {preview.invalidEans.slice(0, 20).join(", ")}</p> : null}
          <div className="preview-footer"><span>{preview.warnings[0]}</span>
            <button className="primary-action button-action" disabled={busy || !canCreate || !period || !name.trim()} onClick={() => void createBatch()} type="button">
              Создать партию <ArrowRight size={15} />
            </button>
          </div>
        </div> : null}
      </section>

      <section className="surface-card batches-list-card">
        <div className="card-heading"><div><h2>Последние партии</h2><p>{account ? `Аккаунт ${account} · последние 20` : "Выберите аккаунт, чтобы загрузить список"}</p></div>
          {account ? <button className="icon-button" aria-label="Обновить список партий" onClick={() => void loadBatches(account)} type="button"><RefreshCw size={15} /></button> : null}
        </div>
        {account && batches.length ? <div className="restart-table-wrap"><table className="restart-table"><thead><tr><th>Название</th><th>Период</th><th>Строк</th><th>Статус</th><th>Создана</th></tr></thead>
          <tbody>{batches.map((batch) => <tr key={batch.id}><td><Link className="text-link" href={`/batches/${batch.id}`}>{batch.name}</Link></td><td>{batch.period}</td><td>{batch.itemCount.toLocaleString("ru-RU")}</td><td><span className="status-pill">{batch.status}</span></td><td>{new Date(batch.createdAt).toLocaleString("ru-RU")}</td></tr>)}</tbody>
        </table></div> : account ? <div className="table-empty">Пока нет партий в этом аккаунте.</div> : null}
      </section>
    </div>
  );
}
