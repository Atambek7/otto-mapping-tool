"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, CircleAlert, Clock3, PackageCheck, PackageSearch, RefreshCw, ShieldCheck, Workflow } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { useRestartAccount } from "@/features/state/restart-account";
import { DemoWorkspaceClient } from "@/features/demo/client";
import type { DemoOverview } from "@/features/demo/types";

const client = new DemoWorkspaceClient();
const metricCards = [
  { label: "Кандидаты", detail: "в сессионных данных", key: "candidates", icon: PackageSearch, href: "/review" },
  { label: "Готовы к проверке", detail: "статус демо-карточек", key: "awaitingReview", icon: PackageCheck, href: "/quality-control" },
  { label: "Требуют внимания", detail: "демо-сценарии с замечаниями", key: "needsAttention", icon: CircleAlert, href: "/quality-control" },
  { label: "В очереди", detail: "локальный dry-run", key: "queued", icon: Workflow, href: "/queue" },
] as const;

function eventTime(value: string) {
  return new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short", timeZone: "UTC" }).format(new Date(value)) + " UTC";
}

export function OverviewWorkspace() {
  const { account, ready } = useRestartAccount();
  const [data, setData] = useState<DemoOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const load = useCallback(async (active: () => boolean) => {
    setLoading(true); setError("");
    try { const result = await client.overview(account); if (active()) setData(result); }
    catch (cause) { if (active()) setError(cause instanceof Error ? cause.message : "Не удалось загрузить демо-обзор."); }
    finally { if (active()) setLoading(false); }
  }, [account]);
  useEffect(() => { if (!ready) return; let active = true; void load(() => active); return () => { active = false; }; }, [ready, load, reload]);

  return <section className="page-section dashboard-page">
    <div className="page-heading"><div><div className="eyebrow">ОБЗОР RESTART · {account}</div><h1>Панель управления</h1><p>Статус процесса Restart 3/12M для выбранного аккаунта.</p></div>
      <div className="dashboard-heading-actions"><span className="phase-chip"><span /> DEMO · локальная симуляция</span><button type="button" className="demo-button" disabled={loading} onClick={() => setReload((value) => value + 1)}><RefreshCw size={14} /> Обновить</button></div>
    </div>
    {loading ? <ApiState kind="loading" label="Загружаем сводку OttoRestart…" /> : null}
    {error ? <ApiState kind="error" description={error} onRetry={() => setReload((value) => value + 1)} /> : null}
    {data ? <>
      <div className="metric-grid dashboard-metrics">{metricCards.map(({ label, detail, key, icon: Icon, href }) => <Link className="metric-card dashboard-metric" href={href} key={key}><div className="metric-topline"><span>{label}</span><Icon size={17} /></div><div className="metric-value">{data.counts[key].toLocaleString("ru-RU")}</div><div className="metric-detail">{detail}</div></Link>)}</div>
      <div className="dashboard-panels">
        <article className="surface-card dashboard-chart-card"><div className="card-heading"><div><h2>Ход обработки</h2><p>Фактические статусы synthetic-кандидатов текущей сессии.</p></div><span className="dashboard-account-tag">{account}</span></div>
          <div className="dashboard-chart" role="img" aria-label="Количество карточек по статусам демо-процесса">
            {data.statusBreakdown.map((row) => { const maximum = Math.max(1, ...data.statusBreakdown.map((entry) => entry.count)); const width = row.count ? Math.max(5, Math.round(row.count / maximum * 100)) : 0; return <div className="dashboard-chart-row" key={row.key}><span>{row.label}</span><div className="dashboard-chart-track"><i className={`dashboard-chart-fill dashboard-chart-${row.key}`} style={{ width: `${width}%` }} /></div><strong>{row.count}</strong></div>; })}
          </div>
          <div className="dashboard-chart-foot"><span><i className="chart-legend-dot" /> Демо-карточки {data.counts.candidates}</span><span><ShieldCheck size={14} /> Публикация отключена</span></div>
        </article>
        <article className="surface-card dashboard-activity-card"><div className="card-heading"><div><h2>Последние события</h2><p>Изменения в локальной сессии OttoRestart.</p></div><Link className="text-link" href="/history">История <ArrowRight size={14} /></Link></div>
          {data.activity.length ? <div className="dashboard-activity-list">{data.activity.slice(0, 6).map((event) => <article className="dashboard-activity-row" key={event.id}><span className="dashboard-activity-icon"><Clock3 size={15} /></span><div><strong>{event.summary}</strong><small>{eventTime(event.createdAt)} · {event.entityType}</small></div></article>)}</div> : <ApiState kind="empty" title="Событий пока нет" description="Они появятся после действий в демо-процессе." />}
        </article>
      </div>
      <div className="dashboard-footer-note"><span>Снимок демо-сессии · {eventTime(data.generatedAt)}</span><Link href="/review">Перейти к кандидатам <ArrowRight size={14} /></Link></div>
    </> : null}
  </section>;
}
