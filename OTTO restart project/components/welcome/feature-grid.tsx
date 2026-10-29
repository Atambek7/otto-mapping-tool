"use client";

import Link from "next/link";
import {
  Factory,
  BookOpen,
  Send,
  Settings2,
  ArrowUpRight,
  type LucideIcon,
} from "lucide-react";

interface StageAction {
  title: string;
  desc: string;
  href: string;
  icon: LucideIcon;
}

interface WorkflowStage {
  step: string;
  title: string;
  summary: string;
  actions: StageAction[];
}

const workflowStages: WorkflowStage[] = [
  {
    step: "01",
    title: "Каталоги Afterbuy",
    summary: "Просмотр фабрик и товаров 3/12M",
    actions: [
      {
        title: "Товары Afterbuy",
        desc: "Каталоги фабрик, мануальные цены и категории",
        href: "/afterbuy",
        icon: Factory,
      },
    ],
  },
  {
    step: "02",
    title: "Каталог OTTO",
    summary: "Прямая работа с товарами 3/12M",
    actions: [
      {
        title: "Напрямую из ОТТО",
        desc: "Реактивация позиций каталога OTTO, ИИ-названия",
        href: "/direct-otto",
        icon: Send,
      },
    ],
  },
  {
    step: "03",
    title: "Данные и параметры",
    summary: "Библиотека EAN и настройки",
    actions: [
      {
        title: "OTTO Library",
        desc: "Связи идентификаторов, пул EAN и история",
        href: "/library",
        icon: BookOpen,
      },
      {
        title: "Настройки",
        desc: "Параметры интеграций и аккаунты JV/XL",
        href: "/settings",
        icon: Settings2,
      },
    ],
  },
];

export function FeatureGrid() {
  return (
    <section className="welcome-stages-card" aria-labelledby="stages-panel-title">
      <div className="welcome-stages-header">
        <h3 id="stages-panel-title" className="welcome-stages-title">
          Что вы можете делать здесь
        </h3>
        <p className="welcome-stages-subtitle">
          Три этапа работы с товарами 3/12M
        </p>
      </div>

      <div className="welcome-stages-list">
        {workflowStages.map((stage) => (
          <div key={stage.step} className="welcome-stage-item">
            <div className="stage-meta-col">
              <span className="stage-step-num">{stage.step}</span>
              <div className="stage-info">
                <h4 className="stage-title">{stage.title}</h4>
                <p className="stage-summary">{stage.summary}</p>
              </div>
            </div>

            <div className="stage-actions-grid">
              {stage.actions.map((act) => {
                const Icon = act.icon;
                return (
                  <Link
                    key={act.href}
                    href={act.href}
                    className="stage-action-chip"
                    title={`${act.title} — ${act.desc}`}
                  >
                    <span className="stage-action-icon">
                      <Icon size={14} aria-hidden="true" />
                    </span>
                    <span className="stage-action-label">{act.title}</span>
                    <ArrowUpRight
                      size={13}
                      className="stage-action-arrow"
                      aria-hidden="true"
                    />
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
