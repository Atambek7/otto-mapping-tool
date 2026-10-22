"use client";

import { useState } from "react";
import { CalendarClock, CircleSlash, Clock3, FlaskConical } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { WorkflowStepper } from "@/components/workflow-stepper";
import { useRestartAccount } from "@/features/state/restart-account";
import { QueueWorkspace } from "@/components/restart-queue/queue-workspace";

type QueueTab = "queue" | "scheduler";

function SchedulerPanel() {
  const { account } = useRestartAccount();
  return <div className="scheduler-panel">
    <div className="scheduler-demo-note"><FlaskConical size={17} /><span><strong>Планировщик недоступен</strong> В текущем OttoRestart API нет настроек расписания, фоновых запусков или журнала планировщика. Ниже показана отключённая структура экрана.</span></div>
    <div className="scheduler-account-grid">{(["JV", "XL"] as const).map((value) => <article className={`scheduler-account-card${account === value ? " selected" : ""}`} key={value}><div className="scheduler-account-heading"><span className="scheduler-account-mark">{value}</span><div><strong>Аккаунт {value}</strong><small>{account === value ? "Выбран в OttoRestart" : "Доступен через переключатель аккаунта"}</small></div><span className="scheduler-disabled-status"><i /> Не настроен</span></div><div className="scheduler-settings-list"><div><CalendarClock size={15} /><span>Ежедневный поиск</span><strong>Недоступно</strong></div><div><Clock3 size={15} /><span>Время запуска</span><strong>—</strong></div><div><CircleSlash size={15} /><span>Автоматическая обработка</span><strong>Отключена</strong></div></div><button className="demo-button" type="button" disabled title="API планировщика не реализован">Настроить расписание</button></article>)}</div>
    <div className="scheduler-history"><div><h2>Предстоящие задачи</h2><p>Запланированные запуски появятся после подключения backend-планировщика.</p></div><ApiState kind="unavailable" title="Нет данных планировщика" description="Реальных или demo-запусков по расписанию сейчас нет." /></div>
  </div>;
}

export function QueueSchedulerWorkspace() {
  const [tab, setTab] = useState<QueueTab>("queue");
  return <section className="page-section queue-scheduler-page">
    <div className="page-heading"><div><div className="eyebrow">ПУБЛИКАЦИЯ И РАСПИСАНИЕ</div><h1>Очередь и планировщик</h1><p>Проверка только в локальном dry-run. Публикация и фоновые запуски отключены.</p></div><span className="phase-chip"><span /> DEMO · локальная симуляция</span></div>
    <WorkflowStepper active="queue" />
    <div className="queue-scheduler-tabs" role="tablist" aria-label="Очередь или планировщик">
      <button type="button" role="tab" aria-selected={tab === "queue"} onClick={() => setTab("queue")}>Очередь Restart</button>
      <button type="button" role="tab" aria-selected={tab === "scheduler"} onClick={() => setTab("scheduler")}>Планировщик</button>
    </div>
    {tab === "queue" ? <QueueWorkspace compact /> : <SchedulerPanel />}
  </section>;
}
