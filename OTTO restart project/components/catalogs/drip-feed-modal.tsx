"use client";

import React, { useState } from "react";
import {
  Clock,
  SlidersHorizontal,
  TableProperties,
  AlertCircle,
  X,
  Send,
} from "lucide-react";
import { useRestartAccount } from "@/features/state/restart-account";

interface DripFeedModalProps {
  factoryId: string;
  factoryName?: string;
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: () => void;
  selectedEans?: string[];
}

export function DripFeedModal({
  factoryId,
  factoryName,
  isOpen,
  onClose,
  onSuccess,
  selectedEans,
}: DripFeedModalProps) {
  const { account } = useRestartAccount();
  const [days, setDays] = useState<number>(7);
  const [dailyTime, setDailyTime] = useState<string>("08:00");
  const [executorType, setExecutorType] = useState<string>("otto_api");
  const [excludePublished, setExcludePublished] = useState<boolean>(false);
  const [antiCollisionEnabled, setAntiCollisionEnabled] = useState<boolean>(true);
  const [staggerDays, setStaggerDays] = useState<number>(2);

  if (!isOpen) return null;

  return (
    <div
      className="drip-modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="drip-modal-title"
    >
      <div className="drip-modal-container">
        {/* Header */}
        <div className="drip-modal-header">
          <div className="drip-modal-title-wrap">
            <div className="drip-modal-icon-badge">
              <Clock size={18} />
            </div>
            <div>
              <div className="drip-modal-header-heading">
                <h3 id="drip-modal-title">Публикация Drip Feed</h3>
                <span className="drip-queue-account-badge">{account} Möbel</span>
              </div>
              <p className="drip-queue-subtitle">
                Каталог: <strong>{factoryName || factoryId}</strong>
                {selectedEans && selectedEans.length > 0 && (
                  <span className="drip-selected-badge">
                    {" "}· выбрано: {selectedEans.length} шт.
                  </span>
                )}
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="drip-queue-close-btn"
            aria-label="Закрыть"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="drip-modal-body">
          {/* Strategy & Schedule */}
          <div>
            <div className="drip-section-header">
              <SlidersHorizontal size={14} />
              <span>Параметры расписания</span>
            </div>

            <div className="drip-param-grid">
              {/* Duration Days */}
              <div className="drip-param-card">
                <label className="drip-param-label">Продолжительность (дней)</label>
                <div className="drip-days-pill-row">
                  {[3, 5, 7, 14].map((d) => (
                    <button
                      key={d}
                      type="button"
                      onClick={() => setDays(d)}
                      className={`drip-day-pill ${days === d ? "active" : ""}`}
                    >
                      {d}d
                    </button>
                  ))}
                </div>
                <input
                  type="number"
                  min="1"
                  max="30"
                  value={days}
                  onChange={(e) =>
                    setDays(Math.max(1, Math.min(30, parseInt(e.target.value) || 1)))
                  }
                  className="drip-input-number"
                  placeholder="Своё число дней"
                />
              </div>

              {/* Time */}
              <div className="drip-param-card">
                <label className="drip-param-label">Ежедневное время старта</label>
                <input
                  type="time"
                  value={dailyTime}
                  onChange={(e) => setDailyTime(e.target.value)}
                  className="drip-input-time"
                />
                <span className="drip-param-hint">Часовой пояс: Europe/Berlin</span>
              </div>

              {/* Delivery Channel */}
              <div className="drip-param-card">
                <label className="drip-param-label">Канал выгрузки</label>
                <select
                  value={executorType}
                  onChange={(e) => setExecutorType(e.target.value)}
                  className="drip-select-channel"
                >
                  <option value="otto_api">Прямой OTTO API</option>
                  <option value="batch_file">Экспортный CSV/ZIP пакет</option>
                </select>
                <span className="drip-param-hint">Отправка карточек с валидацией</span>
              </div>
            </div>

            {/* Anti-collision protection */}
            <div className="drip-anti-collision-box">
              <label className="drip-checkbox-label">
                <input
                  type="checkbox"
                  checked={antiCollisionEnabled}
                  onChange={(e) => setAntiCollisionEnabled(e.target.checked)}
                />
                <span>🛡️ Защита от коллизий между аккаунтами (JV ↔ XL)</span>
              </label>

              {antiCollisionEnabled && (
                <div className="drip-stagger-row">
                  <span>Смещение публикации для одинаковых карточек:</span>
                  <div className="drip-stagger-buttons">
                    {[1, 2, 3, 5].map((d) => (
                      <button
                        key={d}
                        type="button"
                        onClick={() => setStaggerDays(d)}
                        className={`stagger-btn ${staggerDays === d ? "active" : ""}`}
                      >
                        {d}д
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Distribution Preview */}
          <div>
            <div className="drip-section-header">
              <TableProperties size={14} />
              <span>Распределение по дням</span>
            </div>

            <div className="drip-dist-grid">
              <div className="drip-dist-card">
                <span className="label">Всего товаров</span>
                <span className="val">24</span>
              </div>
              <div className="drip-dist-card">
                <span className="label">Готовы к выгрузке</span>
                <span className="val text-success">24</span>
              </div>
              <div className="drip-dist-card">
                <span className="label">В среднем в день</span>
                <span className="val text-accent">{Math.round(24 / days)} шт./день</span>
              </div>
              <div className="drip-dist-card">
                <span className="label">Первый запуск</span>
                <span className="val">Завтра в {dailyTime}</span>
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="drip-modal-footer">
          <button type="button" onClick={onClose} className="drip-queue-btn-close">
            Отмена
          </button>
          <button
            type="button"
            onClick={() => {
              onSuccess?.();
              onClose();
            }}
            className="drip-modal-submit-btn"
          >
            <Send size={13} />
            <span>Создать расписание Drip Feed</span>
          </button>
        </div>
      </div>
    </div>
  );
}
