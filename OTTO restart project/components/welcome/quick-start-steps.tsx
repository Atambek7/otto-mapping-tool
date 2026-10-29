"use client";

import Link from "next/link";
import { ArrowRight, BookOpenText, Factory, Send, type LucideIcon } from "lucide-react";

interface StepItem {
  number: number;
  title: string;
  desc: string;
  icon: LucideIcon;
  href: string;
  actionText: string;
}

const steps: StepItem[] = [
  {
    number: 1,
    title: "Товары Afterbuy",
    desc: "Откройте каталог фабрики Afterbuy, фильтруйте товары 3/12M, редактируйте цены, категории и атрибуты прямо в таблице.",
    icon: Factory,
    href: "/afterbuy",
    actionText: "Открыть Afterbuy",
  },
  {
    number: 2,
    title: "Напрямую из ОТТО",
    desc: "Работайте со снятыми позициями каталога OTTO, оптимизируйте названия до 70 символов и проверяйте статусы.",
    icon: Send,
    href: "/direct-otto",
    actionText: "Открыть каталог OTTO",
  },
  {
    number: 3,
    title: "OTTO Library",
    desc: "Контролируйте связи исходных и новых идентификаторов, пул свободных EAN и историю рестарт-событий.",
    icon: BookOpenText,
    href: "/library",
    actionText: "Перейти в Library",
  },
];

export function QuickStartSteps() {
  return (
    <section id="quick-route" className="welcome-quick-route-section" aria-labelledby="quick-route-title">
      <div className="section-title-wrap">
        <h3 id="quick-route-title" className="section-heading-title">
          Быстрый маршрут
        </h3>
        <p className="section-heading-subtitle">
          Три основных шага для обработки товаров и подготовки их к публикации.
        </p>
      </div>

      <div className="quick-steps-row">
        {steps.map((step) => {
          const Icon = step.icon;
          return (
            <Link
              key={step.number}
              href={step.href}
              className="step-card"
              title={`Шаг ${step.number}: ${step.title}`}
            >
              <div className="step-topline">
                <div className="step-number-circle">{step.number}</div>
                <div className="step-icon-wrap">
                  <Icon size={18} strokeWidth={2} />
                </div>
              </div>

              <div className="step-body">
                <h4 className="step-title">{step.title}</h4>
                <p className="step-desc">{step.desc}</p>
              </div>

              <span className="step-action-hint">
                <span>{step.actionText}</span>
                <ArrowRight size={13} />
              </span>
            </Link>
          );
        })}
      </div>
    </section>
  );
}
