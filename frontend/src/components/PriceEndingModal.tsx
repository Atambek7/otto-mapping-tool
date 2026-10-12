"use client";

import React, { useState } from "react";
import { fetchWithAuth } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Loader2, X, Tag, Sparkles, Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

interface PriceEndingModalProps {
  isOpen: boolean;
  onClose: () => void;
  account: string;
  factoryId: string;
  factoryName?: string;
  totalProductsCount: number;
  selectedCount: number;
  selectedEans: string[];
  items?: Array<{ ean: string; price?: any }>;
  onPricesUpdated: (updatedPrices: Record<string, string>) => void;
}

export const PriceEndingModal: React.FC<PriceEndingModalProps> = ({
  isOpen,
  onClose,
  account,
  factoryId,
  factoryName,
  totalProductsCount,
  selectedCount,
  selectedEans,
  items,
  onPricesUpdated,
}) => {
  const [selectedEnding, setSelectedEnding] = useState<string>("99");
  const [customEnding, setCustomEnding] = useState<string>("");
  const [isCustom, setIsCustom] = useState<boolean>(false);
  const [isApplying, setIsApplying] = useState<boolean>(false);

  if (!isOpen) return null;

  const presets = ["99", "95", "00"];
  const activeEnding = isCustom ? (customEnding.replace(/\D/g, "").slice(0, 2) || "99") : selectedEnding;

  const targetCount = selectedCount > 0 ? selectedCount : totalProductsCount;
  const isSelectedMode = selectedCount > 0;

  const handleApply = async () => {
    if (isApplying) return;
    setIsApplying(true);

    try {
      const payload: Record<string, any> = {
        account: account || "JV",
        ending: activeEnding,
      };

      if (isSelectedMode) {
        if (!selectedEans || selectedEans.length === 0) {
          throw new Error("Не удалось определить EAN выбранных товаров");
        }
        payload.product_eans = selectedEans;
      }

      if (items && items.length > 0) {
        payload.items = isSelectedMode && selectedEans && selectedEans.length > 0
          ? items.filter((it) => selectedEans.includes(it.ean))
          : items;
      }

      const res = await fetchWithAuth(`/mapping/factory/${factoryId}/apply-price-ending`, {
        method: "POST",
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Не удалось обновить цены");
      }

      const data = await res.json();
      const count = data.updated_count || 0;
      const updatedMap = data.updated_prices || {};

      onPricesUpdated(updatedMap);
      toast.success(
        `Цены успешно обновлены на ,${activeEnding} для ${count} ${
          count === 1 ? "товара" : count < 5 ? "товаров" : "товаров"
        }!`
      );
      onClose();
    } catch (err: any) {
      console.error("Apply price ending error:", err);
      toast.error(err.message || "Ошибка при обновлении окончания цен");
    } finally {
      setIsApplying(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs animate-in fade-in duration-200"
      onClick={(e) => {
        if (e.target === e.currentTarget && !isApplying) onClose();
      }}
    >
      <div className="relative w-full max-w-md bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-2xl overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 dark:border-slate-800/80 bg-slate-50/50 dark:bg-slate-900/50">
          <div className="flex items-center gap-2.5">
            <div className="p-2 bg-emerald-100 dark:bg-emerald-950/60 text-emerald-600 dark:text-emerald-400 rounded-xl border border-emerald-200 dark:border-emerald-800/40">
              <Tag className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-slate-900 dark:text-white">
                Окончание цены (,{activeEnding})
              </h3>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {isSelectedMode ? `Для выбранных товаров (${selectedCount} шт.)` : `Для всех товаров фабрики (${totalProductsCount} шт.)`}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={isApplying}
            className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 rounded-lg transition-colors cursor-pointer disabled:opacity-50"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-5 text-sm">
          {/* Target Scope Badge */}
          <div className="p-3 bg-slate-50 dark:bg-slate-800/50 border border-slate-200/80 dark:border-slate-700/60 rounded-xl flex items-center justify-between">
            <span className="text-xs text-slate-600 dark:text-slate-300 font-medium">
              Область применения:
            </span>
            <span className="text-xs font-semibold px-2 py-0.5 rounded-md bg-emerald-100 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-200 border border-emerald-300 dark:border-emerald-800">
              {isSelectedMode ? `${selectedCount} выбранных` : `Все ${totalProductsCount} товаров`}
            </span>
          </div>

          {/* Ending Selection */}
          <div className="space-y-2">
            <label className="text-xs font-semibold text-slate-700 dark:text-slate-300 uppercase tracking-wider block">
              Выберите или введите окончание:
            </label>
            <div className="grid grid-cols-4 gap-2">
              {presets.map((preset) => {
                const isSelected = !isCustom && selectedEnding === preset;
                return (
                  <button
                    key={preset}
                    type="button"
                    onClick={() => {
                      setIsCustom(false);
                      setSelectedEnding(preset);
                    }}
                    className={cn(
                      "py-2.5 px-3 rounded-xl font-mono text-sm font-bold border transition-all cursor-pointer flex flex-col items-center justify-center gap-0.5",
                      isSelected
                        ? "bg-emerald-600 text-white border-emerald-600 shadow-sm shadow-emerald-500/20"
                        : "bg-slate-50 hover:bg-slate-100 dark:bg-slate-800/60 dark:hover:bg-slate-800 text-slate-700 dark:text-slate-300 border-slate-200 dark:border-slate-700"
                    )}
                  >
                    <span>,{preset}</span>
                    {isSelected && <Check className="w-3 h-3 text-white" />}
                  </button>
                );
              })}

              {/* Custom Input Tab */}
              <div
                className={cn(
                  "relative rounded-xl border transition-all flex items-center px-2",
                  isCustom
                    ? "border-emerald-500 ring-2 ring-emerald-500/20 bg-emerald-50/30 dark:bg-emerald-950/20"
                    : "border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/60 hover:bg-slate-100 dark:hover:bg-slate-800"
                )}
              >
                <span className="text-xs font-bold text-slate-400 select-none mr-0.5">,</span>
                <input
                  type="text"
                  maxLength={2}
                  placeholder="Своё"
                  value={customEnding}
                  onFocus={() => setIsCustom(true)}
                  onChange={(e) => {
                    setIsCustom(true);
                    const clean = e.target.value.replace(/\D/g, "").slice(0, 2);
                    setCustomEnding(clean);
                  }}
                  className="w-full bg-transparent font-mono text-sm font-bold text-slate-900 dark:text-white outline-none placeholder:text-slate-400 placeholder:text-xs"
                />
              </div>
            </div>
          </div>

          {/* Live Preview Box */}
          <div className="p-3.5 bg-emerald-50/60 dark:bg-emerald-950/20 border border-emerald-200/60 dark:border-emerald-900/40 rounded-xl space-y-1.5">
            <div className="flex items-center gap-1.5 text-xs font-bold text-emerald-800 dark:text-emerald-300">
              <Sparkles className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
              <span>Пример преобразования цен:</span>
            </div>
            <div className="font-mono text-xs text-slate-700 dark:text-slate-300 space-y-0.5">
              <div className="flex items-center gap-2">
                <span className="line-through text-slate-400">1.900,00 €</span>
                <span>➔</span>
                <span className="font-bold text-emerald-700 dark:text-emerald-300">1.900,{activeEnding} €</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="line-through text-slate-400">850,50 €</span>
                <span>➔</span>
                <span className="font-bold text-emerald-700 dark:text-emerald-300">850,{activeEnding} €</span>
              </div>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400 pt-1 border-t border-emerald-100 dark:border-emerald-900/40">
              ✓ Новые цены защищаются флагом ручной цены и не затираются фоновым синком Aftercool.
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2.5 px-6 py-4 border-t border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/50">
          <Button
            variant="outline"
            size="sm"
            onClick={onClose}
            disabled={isApplying}
            className="h-9 px-4 text-xs font-semibold cursor-pointer"
          >
            Отмена
          </Button>
          <Button
            size="sm"
            onClick={handleApply}
            disabled={isApplying || targetCount === 0}
            className="h-9 px-4 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white cursor-pointer shadow-xs gap-1.5"
          >
            {isApplying ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                <span>Применяем...</span>
              </>
            ) : (
              <>
                <span>Применить ,{activeEnding} ({targetCount} шт.)</span>
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  );
};
