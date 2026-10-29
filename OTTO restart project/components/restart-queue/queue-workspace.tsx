"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AlertTriangle, Check, CirclePause, CirclePlay, FlaskConical, ListChecks, Pause, Play, RefreshCw, ShieldCheck, Square, X } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { useRestartAccount } from "@/features/state/restart-account";
import { DemoQueueClient, DemoQueueClientError } from "@/features/queue/client";
import type { DemoQueueSnapshot, DemoQueueTask, DemoQueueTaskStatus } from "@/features/queue/types";
import "./queue-workspace.css";

const client = new DemoQueueClient();
const statusLabels: Record<DemoQueueTaskStatus, string> = {
  QUEUED: "Ожидает dry-run", DRY_RUN_COMPLETE: "Dry-run пройден", BLOCKED: "Заблокирована", CANCELLED: "Отменена",
};

function money(cents: number) {
  return new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" }).format(cents / 100);
}

function timestamp(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "medium", timeZone: "UTC" }).format(new Date(value)) + " UTC";
}

export function QueueWorkspace({ compact = false }: { compact?: boolean }) {
  const { account, ready } = useRestartAccount();
  const [queue, setQueue] = useState<DemoQueueSnapshot | null>(null);
  const [approved, setApproved] = useState<{ id: string; version: number; old: { title: string } }[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [busyTask, setBusyTask] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const requestAccount = useRef(account);
  requestAccount.current = account;

  const load = useCallback(async (active: () => boolean) => {
    setLoading(true); setError("");
    try {
      // Sequential on first visit: the first response sets the browser-scoped demo cookie.
      const queueResult = await client.snapshot(account);
      if (!active()) return;
      const itemResult = await client.approvedItems(account);
      if (!active() || requestAccount.current !== account) return;
      setQueue(queueResult); setApproved(itemResult.items); setSelected(new Set());
    } catch (cause) {
      if (active() && requestAccount.current === account) setError(cause instanceof DemoQueueClientError ? cause.message : cause instanceof Error ? cause.message : "Не удалось загрузить демо-очередь.");
    } finally {
      if (active() && requestAccount.current === account) setLoading(false);
    }
  }, [account]);

  useEffect(() => {
    if (!ready) return;
    let active = true;
    void load(() => active);
    return () => { active = false; };
  }, [ready, load, reload]);

  async function control(action: "pause" | "resume" | "stop-accepting" | "start-accepting") {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const state = await client.control(account, action);
      if (requestAccount.current !== account) return;
      setQueue(state);
      setNotice(({ pause: "Обработка dry-run приостановлена.", resume: "Обработка dry-run возобновлена.", "stop-accepting": "Приём новых демо-задач остановлен.", "start-accepting": "Приём демо-задач возобновлён." } as const)[action]);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось изменить состояние очереди."); }
    finally { setBusy(false); }
  }

  async function enqueue() {
    const entries = approved.filter((item) => selected.has(item.id)).map((item) => ({ id: item.id, expectedVersion: item.version }));
    if (!entries.length || busy || !window.confirm(`Добавить ${entries.length} утверждённых карточек в демонстрационную очередь? Будет доступен только локальный dry-run; запросов к OTTO не будет.`)) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await client.enqueueDryRun(account, entries);
      if (requestAccount.current !== account) return;
      setQueue(result.queue); setSelected(new Set());
      const added = result.results.filter((row) => row.task).length;
      const rejected = result.results.filter((row) => row.error).length;
      setNotice(`В демо-очередь добавлено: ${added}; отклонено серверной проверкой: ${rejected}. Публикация отключена.`);
      setReload((value) => value + 1);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось добавить карточки."); }
    finally { setBusy(false); }
  }

  async function taskAction(task: DemoQueueTask, action: "dry-run" | "cancel") {
    if (busyTask || busy) return;
    setBusyTask(task.id); setError(""); setNotice("");
    try {
      const changed = action === "dry-run" ? await client.runDryRun(account, task.id) : await client.cancel(account, task.id);
      if (requestAccount.current !== account) return;
      setQueue(await client.snapshot(account));
      const items = await client.approvedItems(account);
      if (requestAccount.current === account) setApproved(items.items);
      setNotice(action === "dry-run" ? (changed.status === "DRY_RUN_COMPLETE" ? "Локальная сухая проверка пройдена; OTTO не вызывался." : changed.result ?? "Задача заблокирована.") : "Демо-задача отменена; внешние действия не выполнялись.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Не удалось обработать демо-задачу."); }
    finally { setBusyTask(""); }
  }

  const pageItems = queue?.tasks ?? [];
  const selectedReady = approved.filter((item) => selected.has(item.id));

  return <section className={`page-section demo-queue${compact ? " queue-compact" : ""}`}>
    {!compact ? <div className="page-heading"><div><div className="eyebrow">OTTORESTART · СОБСТВЕННЫЕ ЗАДАЧИ</div><h1>Очередь Restart</h1><p>Демо-мониторинг задач с локальным dry-run и независимым управлением для {account}.</p></div><span className="queue-demo-label"><FlaskConical size={14} /> DEMO · этап 5</span></div> : null}
    {!compact ? <div className="queue-demo-banner"><FlaskConical size={22} /><div><strong>Изолированная демонстрационная очередь</strong><p>Показываются только утверждённые DEMO-карточки. Dry-run проверяет снимок локально; публикация и обращения к OTTO/Aftercool отключены.</p></div><button type="button" disabled={busy || loading} onClick={() => setReload((value) => value + 1)}><RefreshCw size={14} /> Обновить</button></div> : null}

    {error ? <ApiState kind="error" description={error} onRetry={() => setReload((value) => value + 1)} /> : null}
    {notice ? <p className="queue-notice" role="status"><Check size={15} />{notice}</p> : null}

    {loading ? <ApiState kind="loading" label="Загружаем демо-очередь…" /> : queue ? <>
      <div className="queue-control-grid">
        <article className="queue-control-card"><div className="queue-control-heading"><span className="queue-control-icon"><CirclePause size={18} /></span><div><strong>Обработка dry-run</strong><small>{queue.paused ? "Приостановлена" : "Активна"}</small></div></div><p>Пауза запрещает запуск локальных проверок. Она не отправляет и не отменяет товары.</p><button type="button" className="queue-control-button" disabled={busy} onClick={() => void control(queue.paused ? "resume" : "pause")}>{queue.paused ? <><CirclePlay size={15} /> Возобновить</> : <><Pause size={15} /> Приостановить</>}</button></article>
        <article className="queue-control-card"><div className="queue-control-heading"><span className="queue-control-icon"><ListChecks size={18} /></span><div><strong>Приём задач</strong><small>{queue.acceptingTasks ? "Открыт" : "Остановлен"}</small></div></div><p>Останавливает или возобновляет добавление новых карточек в демонстрационную очередь.</p><button type="button" className="queue-control-button" disabled={busy} onClick={() => void control(queue.acceptingTasks ? "stop-accepting" : "start-accepting")}>{queue.acceptingTasks ? <><Square size={14} /> Остановить приём</> : <><Play size={15} /> Разрешить приём</>}</button></article>
      </div>

      <div className="queue-metrics" aria-label="Состояние очереди">
        <article><span>Ожидают dry-run</span><strong>{queue.counts.QUEUED}</strong></article>
        <article><span>Dry-run пройден</span><strong>{queue.counts.DRY_RUN_COMPLETE}</strong></article>
        <article><span>Заблокированы</span><strong>{queue.counts.BLOCKED}</strong></article>
        <article><span>Отменены</span><strong>{queue.counts.CANCELLED}</strong></article>
      </div>

      <section className="queue-panel" aria-labelledby="queue-approved-title">
        <div className="queue-panel-heading"><div><h2 id="queue-approved-title">Утверждённые демо-карточки</h2><p>Выберите карточки, чтобы создать для них локальные задачи проверки.</p></div><Link href="/review?tab=approval">Открыть проверку товаров →</Link></div>
        {!approved.length ? <ApiState kind="empty" title="Пока нет утверждённых демо-карточек" description="Откройте проверку товаров, подготовьте одну из тестовых карточек, выполните проверку и утвердите её в демо." /> : <>
          <div className="queue-candidate-list">{approved.map((item) => <label key={item.id} className="queue-candidate"><input type="checkbox" checked={selected.has(item.id)} disabled={busy || !queue.acceptingTasks} onChange={(event) => setSelected((current) => { const next = new Set(current); if (event.target.checked) next.add(item.id); else next.delete(item.id); return next; })} /><span><strong>{item.old.title}</strong><small>{item.id} · версия {item.version}</small></span><ShieldCheck size={16} /></label>)}</div>
          <div className="queue-enqueue-row"><span>Выбрано {selectedReady.length} · доступно карточек {approved.length}</span><button type="button" disabled={busy || !selectedReady.length || !queue.acceptingTasks} onClick={() => void enqueue()}><ListChecks size={15} /> Добавить в демо-очередь</button></div>
        </>}
      </section>

      <section className="queue-panel" aria-labelledby="queue-tasks-title">
        <div className="queue-panel-heading"><div><h2 id="queue-tasks-title">Задачи · {account}</h2><p>Аккаунты JV и XL имеют отдельные состояния очереди.</p></div><span className="queue-publish-lock"><AlertTriangle size={14} /> Публикация отключена</span></div>
        {!pageItems.length ? <ApiState kind="empty" title="Демо-задач пока нет" description="Утвердите тестовую карточку и добавьте её в очередь сухой проверки." /> : <div className="queue-table-wrap"><table className="queue-table"><thead><tr><th>Карточка</th><th>Цена · снимок</th><th>Состояние</th><th>Попытки</th><th>Создана · UTC</th><th>Действия</th></tr></thead><tbody>{pageItems.map((task) => <tr key={task.id}><td><strong>{task.title}</strong><small>{task.oldSku} → {task.newEan}</small><small>v{task.payloadVersion} · {task.payloadHash.slice(0, 12)}</small></td><td><strong>{money(task.baseCents)}</strong><small>−{money(task.discountCents)} = {money(task.newCents)}</small><small>snapshot {task.priceSnapshotVersion}</small></td><td><span className={`queue-task-status queue-task-${task.status.toLowerCase()}`}>{statusLabels[task.status]}</span>{task.result ? <small className="queue-task-result">{task.result}</small> : null}</td><td>{task.attempts}</td><td>{timestamp(task.createdAt)}</td><td><div className="queue-row-actions">{task.status === "QUEUED" ? <><button type="button" disabled={busy || !!busyTask || queue.paused} title={queue.paused ? "Возобновите обработку" : "Запустить только локальную dry-run проверку"} onClick={() => void taskAction(task, "dry-run")}><Play size={14} /> Dry-run</button><button type="button" disabled={busy || !!busyTask} onClick={() => void taskAction(task, "cancel")}><X size={14} /> Отменить</button></> : <span>—</span>}</div></td></tr>)}</tbody></table></div>}
      </section>
    </> : null}
  </section>;
}
