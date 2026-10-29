"use client";

import { useState } from "react";
import {
  Check,
  CheckSquare,
  Database,
  Loader2,
  RefreshCw,
  SlidersHorizontal,
  Sparkles,
  Tag,
  X,
  Zap,
} from "lucide-react";

interface ProductSelectionBarProps {
  selectedCount: number;
  totalCount: number;
  onClearSelection: () => void;
  onSelectAll?: () => void;
  onApplyDefaults?: () => Promise<void> | void;
  onApplyAiTitles?: () => Promise<void> | void;
  onSyncFromOtto: () => Promise<void> | void;
  onApplyAiCategories: () => Promise<void> | void;
  onApplyAiAttributes: () => Promise<void> | void;
}

export function ProductSelectionBar({
  selectedCount,
  totalCount,
  onClearSelection,
  onSelectAll,
  onApplyDefaults,
  onApplyAiTitles,
  onSyncFromOtto,
  onApplyAiCategories,
  onApplyAiAttributes,
}: ProductSelectionBarProps) {
  const [activeTask, setActiveTask] = useState<{
    label: string;
    current: number;
    total: number;
  } | null>(null);

  if (selectedCount === 0 && !activeTask) return null;

  async function runWithProgress(
    label: string,
    actionFn: () => Promise<void> | void
  ) {
    const total = selectedCount;
    setActiveTask({ label, current: 0, total });

    const stepInterval = Math.max(70, Math.floor(800 / (total + 1)));
    let currentStep = 0;

    const intervalId = setInterval(() => {
      currentStep += 1;
      if (currentStep <= total) {
        setActiveTask((prev) => (prev ? { ...prev, current: currentStep } : null));
      }
    }, stepInterval);

    try {
      await Promise.resolve(actionFn());
    } finally {
      clearInterval(intervalId);
      setActiveTask((prev) => (prev ? { ...prev, current: total } : null));
      setTimeout(() => {
        setActiveTask(null);
      }, 400);
    }
  }

  // PROGRESS STATE
  if (activeTask) {
    const percent =
      activeTask.total > 0
        ? Math.min(100, Math.round((activeTask.current / activeTask.total) * 100))
        : 100;

    return (
      <aside
        className="catalog-selection-bar floating-bar-progress"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="selection-progress-box">
          <div className="selection-progress-header">
            <div className="progress-title-stack">
              <div className="progress-spinner-wrap">
                <Loader2 size={16} className="spin" />
              </div>
              <div className="progress-labels">
                <span className="progress-action-name">{activeTask.label}</span>
                <span className="progress-action-sub">
                  Обработано {activeTask.current} из {activeTask.total} товаров
                </span>
              </div>
            </div>

            <div className="progress-percentage-pill">{percent}%</div>
          </div>

          <div className="selection-progress-track">
            <div
              className="selection-progress-bar"
              style={{ width: `${percent}%` }}
            />
          </div>
        </div>
      </aside>
    );
  }

  const itemsWord =
    selectedCount % 10 === 1 && selectedCount % 100 !== 11
      ? "товар"
      : selectedCount % 10 >= 2 &&
        selectedCount % 10 <= 4 &&
        (selectedCount % 100 < 10 || selectedCount % 100 >= 20)
      ? "товара"
      : "товаров";

  const totalWord =
    totalCount % 10 === 1 && totalCount % 100 !== 11
      ? "товара"
      : "товаров";

  return (
    <aside
      className="catalog-selection-bar floating-bar-actions"
      role="region"
      aria-label="Панель массовых действий над выбранными товарами"
    >
      <div className="selection-bar-inner">
        {/* Left Side: Circular Badge & Count Label matching Photo 4 */}
        <div className="selection-bar-info">
          <div className="selection-count-circle" title={`Выбрано ${selectedCount} ${itemsWord}`}>
            {selectedCount}
          </div>

          <div className="selection-count-text-block">
            <div className="selection-count-headline">
              Выбрано: <strong>{selectedCount}</strong>
            </div>
            <div className="selection-count-subline">
              из {totalCount} {totalWord}
            </div>
          </div>
        </div>

        <div className="selection-bar-divider" aria-hidden="true" />

        {/* Right Side: Action Buttons matching Photo 4 (No ,99, No в XL) */}
        <div className="selection-bar-actions">
          {/* 1. Defaults */}
          <button
            type="button"
            className="selection-btn btn-action-defaults"
            onClick={() =>
              runWithProgress(
                "Применение стандартных значений...",
                onApplyDefaults || (() => {})
              )
            }
            title="Применить стандартные параметры фабрики и логистики к выбранным товарам"
          >
            <Zap size={14} className="icon-zap" />
            <span>Defaults</span>
          </button>

          {/* 2. ИИ-название (count) */}
          {onApplyAiTitles && (
            <button
              type="button"
              className="selection-btn btn-action-ai-title"
              onClick={() =>
                runWithProgress(
                  `Генерация ИИ-названий (≤70 симв.) для ${selectedCount} товаров...`,
                  onApplyAiTitles
                )
              }
              title="Оптимизировать названия выбранных товаров до ≤ 70 символов для стандартов OTTO"
            >
              <Sparkles size={14} className="icon-sparkles" />
              <span>ИИ-название ({selectedCount})</span>
            </button>
          )}

          {/* 3. Синхронизация */}
          <button
            type="button"
            className="selection-btn btn-action-sync"
            onClick={() =>
              runWithProgress(
                "Синхронизация карточек с каталогом OTTO...",
                onSyncFromOtto
              )
            }
            title="Синхронизация: подтянуть актуальные данные и категории из каталога OTTO"
          >
            <Database size={14} className="icon-db" />
            <span>Синхронизация</span>
          </button>

          {/* 4. AI Категории */}
          <button
            type="button"
            className="selection-btn btn-action-ai-cat"
            onClick={() =>
              runWithProgress(
                "Определение категорий каталога OTTO через ИИ...",
                onApplyAiCategories
              )
            }
            title="Определить и назначить точную категорию каталога OTTO для выбранных товаров"
          >
            <Sparkles size={14} className="icon-sparkles-orange" />
            <span>AI Категории</span>
          </button>

          {/* 5. AI Атрибуты */}
          <button
            type="button"
            className="selection-btn btn-action-ai-attr"
            onClick={() =>
              runWithProgress(
                "Генерация атрибутов (материал, габариты, цвет) через ИИ...",
                onApplyAiAttributes
              )
            }
            title="Сгенерировать обязательные и рекомендуемые характеристики товаров через ИИ"
          >
            <Sparkles size={14} className="icon-sparkles-white" />
            <span>AI Атрибуты</span>
          </button>

          {/* Dismiss / Clear Selection Button */}
          <button
            type="button"
            className="selection-close-btn"
            onClick={onClearSelection}
            title="Снять выбор"
            aria-label="Снять выбор"
          >
            <X size={15} />
          </button>
        </div>
      </div>
    </aside>
  );
}
