"use client";

import Link from "next/link";
import {
  Armchair,
  ArrowRight,
  BookOpen,
  CalendarDays,
  Check,
  Factory,
  Layers,
  Send,
  Settings2,
  ShieldCheck,
  Sofa,
  Zap,
  type LucideIcon,
} from "lucide-react";

interface ScreenItem {
  title: string;
  desc: string;
  href: string;
  icon: LucideIcon;
  mockupKind: "afterbuy" | "candidates" | "qc" | "queue";
}

const screens: ScreenItem[] = [
  {
    title: "Товары Afterbuy",
    desc: "Каталоги фабрик и единая таблица товаров 3/12M.",
    href: "/afterbuy",
    icon: Factory,
    mockupKind: "afterbuy",
  },
  {
    title: "Напрямую из ОТТО",
    desc: "Снятые позиции OTTO и оптимизация карточек.",
    href: "/direct-otto",
    icon: Send,
    mockupKind: "candidates",
  },
  {
    title: "OTTO Library",
    desc: "Библиотека связей и пул свободных EAN.",
    href: "/library",
    icon: BookOpen,
    mockupKind: "qc",
  },
  {
    title: "Настройки",
    desc: "Конфигурация аккаунтов JV/XL и интеграций.",
    href: "/settings",
    icon: Settings2,
    mockupKind: "queue",
  },
];

/* 1. Afterbuy Illustration: Stylized catalog miniatures and factory cards */
function AfterbuyIllustration() {
  return (
    <div className="screen-illustration ill-afterbuy" aria-hidden="true">
      <div className="ill-header-strip">
        <span className="ill-strip-badge">
          <Factory size={12} />
          <span>Каталоги фабрик</span>
        </span>
        <span className="ill-strip-pill">Aftercool JV / XL</span>
      </div>

      <div className="ill-afterbuy-cards">
        <div className="ill-mini-factory">
          <div className="ill-factory-icon">
            <Armchair size={13} />
          </div>
          <div className="ill-factory-info">
            <span className="ill-f-name">Elena_S 504014</span>
            <div className="ill-f-meta">
              <span className="pill-3m-mini">3M</span>
              <span>14 тов.</span>
            </div>
          </div>
        </div>

        <div className="ill-mini-factory">
          <div className="ill-factory-icon accent">
            <Sofa size={13} />
          </div>
          <div className="ill-factory-info">
            <span className="ill-f-name">Timur_18 503916</span>
            <div className="ill-f-meta">
              <span className="pill-12m-mini">12M</span>
              <span>38 тов.</span>
            </div>
          </div>
        </div>

        <div className="ill-mini-factory">
          <div className="ill-factory-icon">
            <Layers size={13} />
          </div>
          <div className="ill-factory-info">
            <span className="ill-f-name">Bemente 504123</span>
            <div className="ill-f-meta">
              <span className="pill-3m-mini">3M</span>
              <span>21 тов.</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* 2. Candidates 3/12M Illustration: Compact product table with 3M/12M badges and status pills */
function CandidatesIllustration() {
  return (
    <div className="screen-illustration ill-candidates" aria-hidden="true">
      <div className="ill-mini-table">
        <div className="ill-table-head">
          <span>ТОВАР</span>
          <span>ПЕРИОД</span>
          <span>RESTART</span>
          <span>СТАТУС</span>
        </div>

        <div className="ill-table-row">
          <div className="ill-prod-cell">
            <span className="ill-thumb-mini" />
            <span className="ill-p-title">Ecksofa Modern L-Form</span>
          </div>
          <div>
            <span className="pill-3m-mini">3M</span>
          </div>
          <span className="ill-price-val">
            839 €<small>-10€</small>
          </span>
          <div>
            <span className="ill-status-pill green">Готов</span>
          </div>
        </div>

        <div className="ill-table-row">
          <div className="ill-prod-cell">
            <span className="ill-thumb-mini" />
            <span className="ill-p-title">Esstisch Massivholz Dunkel</span>
          </div>
          <div>
            <span className="pill-12m-mini">12M</span>
          </div>
          <span className="ill-price-val">
            1 184 €<small>-15€</small>
          </span>
          <div>
            <span className="ill-status-pill amber">ИИ-назв.</span>
          </div>
        </div>
      </div>
    </div>
  );
}

/* 3. Quality Control Illustration: Checklist with checkmarks, glowing shield, and validation pills */
function QualityControlIllustration() {
  return (
    <div className="screen-illustration ill-qc" aria-hidden="true">
      <div className="ill-qc-layout">
        <div className="ill-qc-badge-box">
          <div className="ill-qc-shield">
            <ShieldCheck size={22} strokeWidth={2.4} />
          </div>
          <span className="ill-qc-score">100% QC</span>
          <span className="ill-qc-sub">Проверено</span>
        </div>

        <div className="ill-qc-checklist">
          <div className="ill-qc-item">
            <div className="ill-check-dot">
              <Check size={10} strokeWidth={3} />
            </div>
            <span className="ill-qc-label">Заголовок OTTO</span>
            <span className="ill-qc-tag green">≤ 70 симв.</span>
          </div>

          <div className="ill-qc-item">
            <div className="ill-check-dot">
              <Check size={10} strokeWidth={3} />
            </div>
            <span className="ill-qc-label">Категория</span>
            <span className="ill-qc-tag blue">OTTO Verified</span>
          </div>

          <div className="ill-qc-item">
            <div className="ill-check-dot">
              <Check size={10} strokeWidth={3} />
            </div>
            <span className="ill-qc-label">Характеристики</span>
            <span className="ill-qc-tag amber">8/8 заполнены</span>
          </div>
        </div>
      </div>
    </div>
  );
}

/* 4. Queue / Scheduler Illustration: Timeline with scheduled batches, rate and status blocks */
function QueueIllustration() {
  return (
    <div className="screen-illustration ill-queue" aria-hidden="true">
      <div className="ill-timeline-header">
        <div className="ill-timeline-title">
          <CalendarDays size={12} />
          <span>График Drip Feed</span>
        </div>
        <span className="ill-timeline-rate">
          <Zap size={10} /> 10 тов. / час
        </span>
      </div>

      <div className="ill-timeline-track">
        <div className="ill-time-markers">
          <span>10:00</span>
          <span>12:00</span>
          <span>14:00</span>
          <span>16:00</span>
        </div>

        <div className="ill-timeline-blocks">
          <div className="ill-slot done" title="Партия #14 — Отправлено">
            <span className="slot-dot green" />
            <span className="slot-label">Партия #14 (25)</span>
          </div>

          <div className="ill-slot active" title="Drip Feed #15 — В процессе">
            <span className="slot-dot pulse" />
            <span className="slot-label">Drip Feed #15</span>
          </div>

          <div className="ill-slot pending" title="Партия #16 — Запланировано">
            <span className="slot-label">Партия #16</span>
          </div>
        </div>
      </div>
    </div>
  );
}

export function ScreenPreviewGrid() {
  return (
    <section className="welcome-screens-section" aria-labelledby="screens-title">
      <div className="section-title-wrap">
        <h3 id="screens-title" className="section-heading-title">
          Основные экраны системы
        </h3>
        <p className="section-heading-subtitle">
          Ключевые разделы OTTO Restart, с которых стоит начать работу.
        </p>
      </div>

      <div className="screens-preview-grid">
        {screens.map((screen) => {
          const Icon = screen.icon;
          return (
            <Link
              key={screen.title}
              href={screen.href}
              className="screen-nav-card"
              title={`Перейти в раздел: ${screen.title}`}
            >
              {/* Dynamic Screen UI Illustration */}
              {screen.mockupKind === "afterbuy" && <AfterbuyIllustration />}
              {screen.mockupKind === "candidates" && <CandidatesIllustration />}
              {screen.mockupKind === "qc" && <QualityControlIllustration />}
              {screen.mockupKind === "queue" && <QueueIllustration />}

              {/* Card Meta Content */}
              <div className="screen-card-content">
                <div className="screen-card-topline">
                  <h4 className="screen-card-title">
                    <Icon size={16} strokeWidth={2} style={{ color: "var(--accent)" }} />
                    <span>{screen.title}</span>
                  </h4>
                  <ArrowRight size={15} className="screen-card-arrow" />
                </div>
                <p className="screen-card-desc">{screen.desc}</p>
              </div>
            </Link>
          );
        })}
      </div>
    </section>
  );
}
