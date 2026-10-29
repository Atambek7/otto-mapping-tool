"use client";

import React, { useState, useMemo } from "react";
import {
  Calendar,
  Check,
  Clock,
  Layers,
  Loader2,
  Play,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { useRestartAccount } from "@/features/state/restart-account";

interface DripScheduleModalProps {
  isOpen: boolean;
  onClose: () => void;
  factoryName?: string;
  totalProductsCount: number;
  onScheduleCreated?: (days: number) => void;
}

const PRESET_DAYS = [3, 7, 14, 30];

export function DripScheduleModal({
  isOpen,
  onClose,
  factoryName = "Каталог OTTO Restart",
  totalProductsCount,
  onScheduleCreated,
}: DripScheduleModalProps) {
  const { account } = useRestartAccount();
  const [days, setDays] = useState<number>(7);
  const [dailyTime, setDailyTime] = useState<string>("08:00");
  const [excludePublished, setExcludePublished] = useState<boolean>(true);
  const [antiCollision, setAntiCollision] = useState<boolean>(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Distribution calculation
  const distribution = useMemo(() => {
    const total = Math.max(1, totalProductsCount);
    const basePerDay = Math.floor(total / days);
    const remainder = total % days;

    return Array.from({ length: days }, (_, idx) => {
      const count = idx < remainder ? basePerDay + 1 : basePerDay;
      const d = new Date();
      d.setDate(d.getDate() + idx + 1);
      const dayLabel = d.toLocaleDateString("ru-RU", {
        weekday: "short",
        day: "numeric",
        month: "short",
      });
      return {
        dayNum: idx + 1,
        dateFormatted: dayLabel,
        count: Math.max(1, count),
      };
    });
  }, [totalProductsCount, days]);

  const maxDayCount = useMemo(() => {
    return Math.max(...distribution.map((d) => d.count), 1);
  }, [distribution]);

  if (!isOpen) return null;

  function handleSubmit() {
    setIsSubmitting(true);
    setTimeout(() => {
      setIsSubmitting(false);
      onScheduleCreated?.(days);
      onClose();
    }, 450);
  }

  return (
    <div className="drip-modal-backdrop" onClick={onClose} role="dialog" aria-modal="true">
      <div
        className="drip-modal-window"
        onClick={(e) => e.stopPropagation()}
        aria-label="Настройка расписания Drip Feed"
      >
        {/* Header */}
        <div className="drip-modal-header">
          <div className="drip-header-left">
            <div className="drip-icon-box">
              <Clock size={20} className="drip-icon-clock" />
            </div>
            <div>
              <div className="drip-title-row">
                <h2 className="drip-title-h2">Настройка Drip Feed</h2>
                <span className="drip-account-chip">{account} Möbel</span>
              </div>
              <p className="drip-subtitle">
                Постепенная выгрузка товаров в OTTO · <strong>{factoryName}</strong>
              </p>
            </div>
          </div>

          <button
            type="button"
            className="drip-close-btn"
            onClick={onClose}
            aria-label="Закрыть модальное окно"
          >
            <X size={16} />
          </button>
        </div>

        {/* Modal Body */}
        <div className="drip-modal-body">
          {/* Section 1: Parameters (Days + Time) */}
          <div className="drip-card-block">
            <div className="drip-card-header">
              <SlidersHorizontal size={15} className="text-orange-400" />
              <span>Параметры расписания</span>
            </div>

            <div className="drip-params-grid">
              {/* Days Selector */}
              <div className="drip-param-col">
                <label className="drip-label">
                  <Calendar size={13} className="text-muted" />
                  <span>Срок распределения</span>
                </label>
                <div className="drip-preset-chips">
                  {PRESET_DAYS.map((d) => {
                    const isActive = days === d;
                    return (
                      <button
                        key={d}
                        type="button"
                        onClick={() => setDays(d)}
                        className={`drip-preset-btn ${isActive ? "active" : ""}`}
                      >
                        <span className="preset-number">{d}</span>
                        <span className="preset-unit">{d === 3 ? "дня" : "дней"}</span>
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Time Selector */}
              <div className="drip-param-col">
                <label className="drip-label">
                  <Clock size={13} className="text-muted" />
                  <span>Ежедневное время старта</span>
                </label>
                <div className="drip-time-input-box">
                  <input
                    type="time"
                    value={dailyTime}
                    onChange={(e) => setDailyTime(e.target.value)}
                    className="drip-time-field"
                  />
                  <span className="drip-tz-tag">Europe/Berlin (CET)</span>
                </div>
              </div>
            </div>
          </div>

          {/* Section 2: Safeguards & Options */}
          <div className="drip-card-block">
            <div className="drip-card-header">
              <ShieldCheck size={15} className="text-emerald-400" />
              <span>Правила безопасности и исключения</span>
            </div>

            <div className="drip-toggles-stack">
              {/* Toggle 1: Exclude Published */}
              <div
                className={`drip-toggle-card ${excludePublished ? "toggle-active" : ""}`}
                onClick={() => setExcludePublished((v) => !v)}
              >
                <div className="drip-toggle-checkbox">
                  <div className={`custom-checkbox ${excludePublished ? "checked" : ""}`}>
                    {excludePublished && <Check size={12} strokeWidth={3} />}
                  </div>
                </div>
                <div className="drip-toggle-content">
                  <span className="drip-toggle-title">
                    Исключить уже опубликованные в OTTO товары
                  </span>
                  <p className="drip-toggle-desc">
                    Товары со статусом «Опубликованы» будут пропущены и не продублируются в магазине
                  </p>
                </div>
              </div>

              {/* Toggle 2: Anti-Collision */}
              <div
                className={`drip-toggle-card ${antiCollision ? "toggle-active" : ""}`}
                onClick={() => setAntiCollision((v) => !v)}
              >
                <div className="drip-toggle-checkbox">
                  <div className={`custom-checkbox ${antiCollision ? "checked" : ""}`}>
                    {antiCollision && <Check size={12} strokeWidth={3} />}
                  </div>
                </div>
                <div className="drip-toggle-content">
                  <div className="drip-toggle-title-row">
                    <span className="drip-toggle-title">
                      Защита от пересечений категорий (Anti-collision)
                    </span>
                    <span className="drip-rec-badge">🛡️ Рекомендуется</span>
                  </div>
                  <p className="drip-toggle-desc">
                    Интеллектуально распределяет схожие позиции по разным дням для максимальной конверсии
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Section 3: Distribution Timeline & Rate */}
          <div className="drip-card-block">
            <div className="drip-card-header">
              <Layers size={15} className="text-amber-400" />
              <span>План распределения товаров</span>
            </div>

            {/* Metrics Strip */}
            <div className="drip-kpi-strip">
              <div className="drip-kpi-item">
                <span className="drip-kpi-label">Всего товаров</span>
                <strong className="drip-kpi-value text-orange">{totalProductsCount}</strong>
              </div>
              <div className="drip-kpi-item">
                <span className="drip-kpi-label">В день в среднем</span>
                <strong className="drip-kpi-value text-emerald">
                  ~{Math.round(totalProductsCount / days)} тов./день
                </strong>
              </div>
              <div className="drip-kpi-item">
                <span className="drip-kpi-label">Период выгрузки</span>
                <strong className="drip-kpi-value text-amber">{days} дней</strong>
              </div>
            </div>

            {/* Visual Bar Chart */}
            <div className="drip-chart-container">
              <div className="drip-chart-bars">
                {distribution.map((item) => {
                  const heightPercent = Math.max(30, Math.round((item.count / maxDayCount) * 100));
                  return (
                    <div key={item.dayNum} className="drip-chart-col">
                      <span className="drip-bar-count">{item.count}</span>
                      <div className="drip-bar-track">
                        <div
                          className="drip-bar-fill"
                          style={{ height: `${heightPercent}%` }}
                        />
                      </div>
                      <span className="drip-bar-day">Д {item.dayNum}</span>
                      <span className="drip-bar-date">{item.dateFormatted.split(",")[0]}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="drip-modal-footer">
          <div className="drip-footer-summary">
            <span>Итого: <strong>{totalProductsCount} товаров</strong> на <strong>{days} дней</strong></span>
          </div>

          <div className="drip-footer-actions">
            <button
              type="button"
              className="drip-btn-secondary"
              onClick={onClose}
              disabled={isSubmitting}
            >
              Отмена
            </button>

            <button
              type="button"
              className="drip-btn-primary"
              onClick={handleSubmit}
              disabled={isSubmitting}
            >
              {isSubmitting ? (
                <>
                  <Loader2 size={15} className="spin" />
                  <span>Запуск расписания...</span>
                </>
              ) : (
                <>
                  <Play size={14} fill="currentColor" />
                  <span>Запустить Drip Feed ({days} дней)</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
