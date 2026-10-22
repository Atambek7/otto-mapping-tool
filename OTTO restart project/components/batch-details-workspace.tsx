"use client";

import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import Link from "next/link";
import { ArrowLeft, ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { RestartApiClient } from "@/features/api/client";
import type { RestartAccount, RestartBatch, RestartBatchItem, RestartPageInfo } from "@/features/api/types";

export function BatchDetailsWorkspace({ batchId }: { batchId: string }) {
  const [account, setAccount] = useState<RestartAccount | "">("");
  const [batch, setBatch] = useState<RestartBatch | null>(null);
  const [items, setItems] = useState<RestartBatchItem[]>([]);
  const [pageInfo, setPageInfo] = useState<RestartPageInfo | null>(null);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const client = useMemo(() => { try { return new RestartApiClient(); } catch { return null; } }, []);

  useEffect(() => {
    if (!account) return;
    let active = true;
    setLoading(true); setError("");
    const api = client;
    if (!api) {
      setError("API OttoRestart не настроен. Задайте NEXT_PUBLIC_RESTART_API_URL и перезапустите frontend.");
      setLoading(false);
      return;
    }
    void Promise.all([
      api.getBatch(batchId, account),
      api.listBatchItems(batchId, account, { page, pageSize: 50, query: query || undefined }),
    ]).then(([batchResult, itemResult]) => {
      if (!active) return;
      setBatch(batchResult); setItems(itemResult.items); setPageInfo(itemResult.page);
    }).catch((cause: unknown) => {
      if (active) setError(cause instanceof Error ? cause.message : "Не удалось загрузить партию.");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [account, batchId, client, page, query, reload]);

  function applySearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setPage(1); setQuery(search.trim());
  }

  return <div className="workspace-stack">
    <div className="detail-toolbar"><Link className="text-link" href="/batches"><ArrowLeft size={14} /> Все партии</Link>
      <label className="field-label detail-account">Аккаунт партии
        <select value={account} onChange={(event) => { setAccount(event.target.value as RestartAccount | ""); setBatch(null); setItems([]); setPage(1); }}>
          <option value="">Выберите аккаунт</option><option value="JV">JV</option><option value="XL">XL</option>
        </select>
      </label>
    </div>
    {loading ? <ApiState kind="loading" label="Загружаем партию и список EAN…" /> : null}
    {error ? <ApiState kind="error" description={error} onRetry={() => setReload((value) => value + 1)} /> : null}
    {batch ? <section className="surface-card batches-list-card">
      <div className="card-heading"><div><div className="eyebrow">{batch.account} · {batch.period}</div><h2>{batch.name}</h2><p>{batch.itemCount.toLocaleString("ru-RU")} кандидатных EAN · создана {new Date(batch.createdAt).toLocaleString("ru-RU")}</p></div><span className="status-pill">{batch.status}</span></div>
      <form className="item-search" onSubmit={applySearch}><input aria-label="Поиск EAN" placeholder="Найти исходный EAN" value={search} onChange={(event) => setSearch(event.target.value)} /><button className="primary-action button-action" type="submit">Поиск</button></form>
      {items.length ? <div className="restart-table-wrap"><table className="restart-table"><thead><tr><th>Строка</th><th>Исходный EAN</th><th>Аккаунт</th><th>Подготовка</th></tr></thead>
        <tbody>{items.map((item) => <tr key={item.id}><td>{item.rowNumber}</td><td className="mono-value">{item.sourceEan}</td><td>{item.account}</td><td><span className="status-pill">Импортирован</span></td></tr>)}</tbody>
      </table></div> : <div className="table-empty">Кандидатов по этому фильтру нет.</div>}
      {pageInfo ? <div className="pagination-bar"><span>Страница {pageInfo.page} из {Math.max(1, pageInfo.totalPages)} · всего {pageInfo.totalItems.toLocaleString("ru-RU")}</span><div>
        <button type="button" aria-label="Предыдущая страница" disabled={page <= 1 || loading} onClick={() => setPage((value) => value - 1)}><ChevronLeft size={16} /></button>
        <button type="button" aria-label="Следующая страница" disabled={page >= pageInfo.totalPages || loading} onClick={() => setPage((value) => value + 1)}><ChevronRight size={16} /></button>
      </div></div> : null}
      <div className="safe-note"><RefreshCw size={14} /> Здесь пока только импортированные кандидаты. Проверки OTTO и публикация отключены.</div>
    </section> : null}
  </div>;
}
