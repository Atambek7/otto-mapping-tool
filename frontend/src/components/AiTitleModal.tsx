"use client";

import React, { useState } from "react";
import { fetchWithAuth } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Sparkles, Loader2, X, AlertCircle, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

interface AiTitleModalProps {
  isOpen: boolean;
  onClose: () => void;
  account: string;
  factoryId: string;
  factoryName?: string;
  totalProductsCount: number;
  over70Count: number;
  selectedIdsCount: number;
  selectedEans: string[];
  selectedOver70Count: number;
  xlAlreadyGeneratedCount?: number;
  xlNeedCount?: number;
  productTitles?: Record<string, string>;
  onTitlesUpdated: (updated: Record<string, string>) => void;
}

export const AiTitleModal: React.FC<AiTitleModalProps> = ({
  isOpen,
  onClose,
  account,
  factoryId,
  factoryName,
  totalProductsCount,
  over70Count,
  selectedIdsCount,
  selectedEans,
  selectedOver70Count,
  xlAlreadyGeneratedCount = 0,
  xlNeedCount,
  productTitles,
  onTitlesUpdated,
}) => {
  const [isGenerating, setIsGenerating] = useState(false);
  const [forceRegenerate, setForceRegenerate] = useState(false);
  const [filterOver70Only, setFilterOver70Only] = useState(false);

  if (!isOpen) return null;

  const hasSelection = selectedIdsCount > 0;
  const xlProtected = xlAlreadyGeneratedCount;

  // Determine target count to process
  let targetCount = 0;
  if (hasSelection) {
    // When specific products are selected, process all of them regardless of title length
    targetCount = selectedIdsCount;
  } else {
    // Whole factory mode
    if (filterOver70Only) {
      targetCount = over70Count;
    } else if (forceRegenerate) {
      targetCount = totalProductsCount;
    } else {
      targetCount = xlNeedCount !== undefined ? xlNeedCount : Math.max(0, totalProductsCount - xlProtected);
    }
  }

  const handleStartGeneration = async () => {
    if (targetCount === 0 || isGenerating) return;

    setIsGenerating(true);
    try {
      const payload: any = {
        factory_id: factoryId,
        account: account.toUpperCase(),
        mode: hasSelection ? "all" : (filterOver70Only ? "over_70_only" : "all"),
        force: hasSelection ? true : forceRegenerate,
      };

      if (hasSelection && selectedEans.length > 0) {
        payload.eans = selectedEans;
        if (productTitles) {
          const selectedTitles: Record<string, string> = {};
          for (const ean of selectedEans) {
            if (productTitles[ean]) {
              selectedTitles[ean] = productTitles[ean];
            }
          }
          if (Object.keys(selectedTitles).length > 0) {
            payload.titles = selectedTitles;
          }
        }
      } else if (productTitles && Object.keys(productTitles).length > 0) {
        payload.titles = productTitles;
      }

      const res = await fetchWithAuth("/mapping/generate-ai-titles", {
        method: "POST",
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        if (errData.detail === "OPENAI_API_KEY_MISSING") {
          throw new Error("Не настроен ключ OpenAI API. Укажите его в Настройках профиля.");
        }
        throw new Error(errData.detail || "Ошибка при генерации названий");
      }

      const data = await res.json();
      const updatedMap: Record<string, string> = data.updated || {};
      const updatedCount = Object.keys(updatedMap).length;
      const skipped = data.skipped_count || 0;

      if (updatedCount > 0) {
        onTitlesUpdated(updatedMap);
        const skippedMsg = skipped > 0 ? ` (${skipped} пропущено для экономии токенов)` : "";
        toast.success(`Успешно обновлено ${updatedCount} названий товаров!${skippedMsg}`);
        onClose();
      } else {
        toast.info(data.message || "Нет товаров для обновления");
        onClose();
      }
    } catch (err: any) {
      toast.error(err.message || "Не удалось сгенерировать названия");
    } finally {
      setIsGenerating(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-xs p-4 animate-in fade-in duration-150">
      <div className="bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 rounded-2xl shadow-2xl max-w-lg w-full overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 dark:border-slate-800">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-xl bg-amber-500/10 text-amber-600 dark:text-amber-400">
              <Sparkles className="w-5 h-5" />
            </div>
            <div>
              <h3 className="font-semibold text-base text-slate-800 dark:text-slate-100">
                ИИ-Генерация названий
              </h3>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {factoryName ? `${factoryName} (${factoryId})` : `Фабрика ${factoryId}`} • Аккаунт {account.toUpperCase()}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={isGenerating}
            className="p-1 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors disabled:opacity-50"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-4 text-sm text-slate-600 dark:text-slate-300">
          {/* Status Box */}
          <div
            className={cn(
              "p-4 rounded-xl border flex items-start gap-3",
              targetCount > 0
                ? "bg-amber-50/50 dark:bg-amber-950/20 border-amber-200/80 dark:border-amber-900/50 text-amber-950 dark:text-amber-200"
                : "bg-emerald-50/50 dark:bg-emerald-950/20 border-emerald-200/80 dark:border-emerald-900/50 text-emerald-950 dark:text-emerald-200"
            )}
          >
            {hasSelection ? (
              <Sparkles className="w-5 h-5 shrink-0 text-amber-600 dark:text-amber-400 mt-0.5" />
            ) : targetCount > 0 ? (
              <AlertCircle className="w-5 h-5 shrink-0 text-amber-600 dark:text-amber-400 mt-0.5" />
            ) : (
              <CheckCircle2 className="w-5 h-5 shrink-0 text-emerald-600 dark:text-emerald-400 mt-0.5" />
            )}
            <div className="space-y-1">
              <div className="font-medium text-xs uppercase tracking-wider">
                {hasSelection
                  ? `Выбрано товаров: ${selectedIdsCount} (${account.toUpperCase()})`
                  : `Фабрика ${account.toUpperCase()}: Всего ${totalProductsCount} товаров`}
              </div>
              <div className="text-xs leading-relaxed">
                {hasSelection ? (
                  <>
                    Для всех <b>{selectedIdsCount}</b> выбранных товаров будут сгенерированы естественные названия до 70 символов с помощью ИИ.
                    {selectedOver70Count > 0 && selectedOver70Count < selectedIdsCount && (
                      <span className="block mt-1 text-slate-500 dark:text-slate-400">
                        (Из них {selectedOver70Count} длиннее 70 символов, {selectedIdsCount - selectedOver70Count} — до 70 символов).
                      </span>
                    )}
                  </>
                ) : filterOver70Only ? (
                  <>
                    Режим фильтра: к генерации <b>{over70Count}</b> товаров с названием длиннее 70 символов.
                  </>
                ) : (
                  <>
                    К генерации: <b>{targetCount}</b> товаров.
                    {xlProtected > 0 && !forceRegenerate && (
                      <span className="block mt-0.5 text-slate-500 dark:text-slate-400">
                        (У {xlProtected} уже создано ИИ-название, они защищены от повторной траты токенов).
                      </span>
                    )}
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Whole Factory Options (when no specific items selected) */}
          {!hasSelection && (
            <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/30 p-3 space-y-2.5">
              <label className="flex items-center gap-2 text-xs text-slate-700 dark:text-slate-300 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={filterOver70Only}
                  onChange={(e) => {
                    setFilterOver70Only(e.target.checked);
                    if (e.target.checked) setForceRegenerate(false);
                  }}
                  className="rounded border-slate-300 dark:border-slate-700 text-amber-600 focus:ring-amber-500 w-3.5 h-3.5"
                />
                <span>Обрабатывать только названия длиннее 70 символов ({over70Count} шт.)</span>
              </label>

              {!filterOver70Only && (
                <label className="flex items-center gap-2 pt-1 border-t border-slate-200/60 dark:border-slate-800 text-xs text-slate-600 dark:text-slate-400 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={forceRegenerate}
                    onChange={(e) => setForceRegenerate(e.target.checked)}
                    className="rounded border-slate-300 dark:border-slate-700 text-amber-600 focus:ring-amber-500 w-3.5 h-3.5"
                  />
                  <span>Принудительно перегенерировать даже готовые названия ({totalProductsCount} шт.)</span>
                </label>
              )}
            </div>
          )}

          {/* AI Rules Info */}
          <div className="bg-slate-50 dark:bg-slate-900/60 border border-slate-200/70 dark:border-slate-800 rounded-xl p-3.5 space-y-2 text-xs">
            <div className="font-semibold text-slate-700 dark:text-slate-200 flex items-center gap-1.5">
              <span>Правила генерации (OpenAI gpt-4o-mini):</span>
            </div>
            <ul className="list-disc pl-4 space-y-1 text-slate-500 dark:text-slate-400">
              <li>Естественный заголовок на немецком языке строго до 70 символов.</li>
              <li><b>Не начинается с категории</b> товара.</li>
              <li><b>Без Keyword Stuffing</b>: исключаются повторы слов и бессмысленные перечисления.</li>
              <li>Только реальные характеристики товара без мусорных слов (Premium, Top).</li>
            </ul>
          </div>

          {/* Progress Indicator */}
          {isGenerating && (
            <div className="p-4 rounded-xl bg-indigo-50 dark:bg-indigo-950/30 border border-indigo-200 dark:border-indigo-900/60 space-y-2.5">
              <div className="flex items-center justify-between text-xs font-medium text-indigo-700 dark:text-indigo-300">
                <span className="flex items-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Генерация названий ({targetCount} товаров)...
                </span>
                <span className="animate-pulse">Обработка...</span>
              </div>
              <div className="w-full h-1.5 bg-indigo-100 dark:bg-indigo-900/50 rounded-full overflow-hidden">
                <div className="h-full bg-indigo-600 rounded-full w-2/3 animate-[pulse_1s_ease-in-out_infinite]" />
              </div>
              <p className="text-[11px] text-indigo-600/80 dark:text-indigo-400/80">
                Параллельные вызовы (семафор 15). Пожалуйста, не закрывайте страницу.
              </p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2.5 px-6 py-4 bg-slate-50/70 dark:bg-slate-900/40 border-t border-slate-100 dark:border-slate-800">
          <Button
            variant="outline"
            size="sm"
            onClick={onClose}
            disabled={isGenerating}
            className="text-xs h-9 px-4"
          >
            Отмена
          </Button>
          <Button
            size="sm"
            onClick={handleStartGeneration}
            disabled={targetCount === 0 || isGenerating}
            className={cn(
              "text-xs h-9 px-4 gap-1.5 font-medium shadow-xs",
              "bg-amber-600 hover:bg-amber-700 text-white dark:bg-amber-500 dark:hover:bg-amber-600",
              (targetCount === 0 || isGenerating) && "opacity-50 cursor-not-allowed"
            )}
          >
            {isGenerating ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Sparkles className="w-3.5 h-3.5" />
            )}
            <span>
              {targetCount === 0
                ? "Все названия уже созданы (0 токенов)"
                : `Сгенерировать названия (${targetCount})`}
            </span>
          </Button>
        </div>
      </div>
    </div>
  );
};

export default AiTitleModal;
