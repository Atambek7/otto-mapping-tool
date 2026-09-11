"use client";

import { useState } from "react";
import {
  Loader2,
  DownloadCloud,
  Trash2,
  ChevronDown,
  Braces,
  FileSpreadsheet,
  Database,
  Sparkles,
  X,
  Check
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";

export interface BatchActionBarProps {
  selectedIds: Set<string>;
  cachedIds: Record<string, boolean>;
  downloading: boolean;
  isAutoMapping: boolean;
  downloadProgress: { current: number; total: number } | null;
  autoMapProgress: { current: number; total: number } | null;
  handleBatchCache: () => void;
  handleBatchDeleteCache: () => void;
  handleBatchExport: (format: "json" | "csv" | "db") => void;
  handleBatchAutoMap: (extractAttributes: boolean) => void;
  setSelectedIds: (ids: Set<string>) => void;
}

export function BatchActionBar({
  selectedIds,
  cachedIds,
  downloading,
  isAutoMapping,
  downloadProgress,
  autoMapProgress,
  handleBatchCache,
  handleBatchDeleteCache,
  handleBatchExport,
  handleBatchAutoMap,
  setSelectedIds,
}: BatchActionBarProps) {
  const [showExportMenu, setShowExportMenu] = useState(false);
  const { t } = useTranslation();

  if (selectedIds.size === 0) return null;

  const isProcessing = downloading || isAutoMapping;
  const progress = downloadProgress || autoMapProgress;
  const percent =
    progress && progress.total > 0
      ? Math.round((progress.current / progress.total) * 100)
      : isProcessing
      ? null
      : 0;

  let actionLabel = t("batch.processing");
  if (isAutoMapping) {
    actionLabel = t("batch.ai_processing");
  } else if (downloading) {
    actionLabel = progress ? t("batch.syncing_cache") : t("batch.preparing_export");
  }

  // Processing Progress Bar State
  if (isProcessing) {
    return (
      <div className="fixed bottom-6 left-1/2 -translate-x-1/2 w-[440px] max-w-[90vw] bg-white dark:bg-[#111827] text-slate-900 dark:text-white rounded-2xl shadow-xl shadow-slate-900/10 dark:shadow-black/50 border border-slate-200/80 dark:border-slate-800 p-5 z-50 animate-in slide-in-from-bottom-4 duration-200">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-indigo-50 dark:bg-indigo-950/60 flex items-center justify-center text-indigo-600 dark:text-indigo-400">
              <Loader2 className="w-5 h-5 animate-spin" />
            </div>
            <div>
              <div className="font-semibold text-sm leading-tight text-slate-900 dark:text-white">
                {actionLabel}
              </div>
              <div className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                {progress ? t("batch.processed_progress", { current: progress.current, total: progress.total }) : t("batch.please_wait")}
              </div>
            </div>
          </div>
          <div className="text-lg font-bold font-mono text-indigo-600 dark:text-indigo-400">
            {percent !== null ? `${percent}%` : "..."}
          </div>
        </div>

        <div className="h-2 w-full bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
          <div
            className="h-full bg-indigo-600 rounded-full transition-all duration-300 ease-out"
            style={{ width: `${percent ?? 100}%` }}
          />
        </div>
      </div>
    );
  }

  const hasUncached = Array.from(selectedIds).some((id) => !cachedIds[id]);
  const hasCached = Array.from(selectedIds).some((id) => cachedIds[id]);

  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-white dark:bg-[#111827] text-slate-900 dark:text-white px-4 py-2.5 rounded-2xl shadow-xl shadow-slate-900/10 dark:shadow-black/40 border border-slate-200/80 dark:border-slate-800 flex items-center gap-2.5 z-50 animate-in slide-in-from-bottom-4 duration-150 select-none">
      {/* Selection Count Pill */}
      <div className="flex items-center gap-2 pl-1 pr-2">
        <span className="h-6 min-w-[24px] px-1.5 rounded-full bg-indigo-600 text-white text-xs font-bold flex items-center justify-center">
          {selectedIds.size}
        </span>
        <span className="text-xs font-semibold text-slate-700 dark:text-slate-300">
          {t("batch.selected", { count: selectedIds.size })}
        </span>
      </div>

      <div className="w-px h-5 bg-slate-200 dark:bg-slate-800" />

      {/* Cache Actions */}
      {hasUncached && (
        <Button
          onClick={handleBatchCache}
          disabled={isProcessing}
          variant="outline"
          size="sm"
          className="h-9 px-3 text-xs font-semibold border-slate-200 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800 text-slate-700 dark:text-slate-200 gap-1.5 cursor-pointer"
        >
          <DownloadCloud className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />
          <span>{t("batch.download_cache")}</span>
        </Button>
      )}

      {hasCached && (
        <Button
          onClick={handleBatchDeleteCache}
          disabled={isProcessing}
          variant="outline"
          size="sm"
          className="h-9 px-3 text-xs font-semibold border-slate-200 dark:border-slate-800 hover:border-rose-300 hover:bg-rose-50 dark:hover:bg-rose-950/30 text-rose-600 dark:text-rose-400 gap-1.5 cursor-pointer"
        >
          <Trash2 className="w-3.5 h-3.5 text-rose-500" />
          <span>{t("common.delete")} {t("factories.cached")}</span>
        </Button>
      )}

      {/* Export Dropdown */}
      <div className="relative">
        <Button
          onClick={() => setShowExportMenu(!showExportMenu)}
          disabled={isProcessing}
          variant="outline"
          size="sm"
          className="h-9 px-3 text-xs font-semibold border-slate-200 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800 text-slate-700 dark:text-slate-200 gap-1.5 cursor-pointer"
        >
          <FileSpreadsheet className="w-3.5 h-3.5 text-slate-500" />
          <span>{t("batch.export")}</span>
          <ChevronDown className={cn("w-3.5 h-3.5 text-slate-400 transition-transform", showExportMenu && "rotate-180")} />
        </Button>

        {showExportMenu && (
          <div className="absolute bottom-full left-0 mb-2 w-44 bg-white dark:bg-[#111827] border border-slate-200 dark:border-slate-800 rounded-xl shadow-lg p-1 z-50 animate-in fade-in-50 zoom-in-95 duration-100">
            <button
              onClick={() => {
                handleBatchExport("json");
                setShowExportMenu(false);
              }}
              className="w-full flex items-center gap-2 px-2.5 py-1.5 text-xs font-medium rounded-lg text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 cursor-pointer"
            >
              <Braces className="w-3.5 h-3.5 text-indigo-500" />
              <span>{t("batch.export_otto_json")}</span>
            </button>
            <button
              onClick={() => {
                handleBatchExport("csv");
                setShowExportMenu(false);
              }}
              className="w-full flex items-center gap-2 px-2.5 py-1.5 text-xs font-medium rounded-lg text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 cursor-pointer"
            >
              <FileSpreadsheet className="w-3.5 h-3.5 text-emerald-500" />
              <span>{t("batch.export_categories_csv")}</span>
            </button>
            <button
              onClick={() => {
                handleBatchExport("db");
                setShowExportMenu(false);
              }}
              className="w-full flex items-center gap-2 px-2.5 py-1.5 text-xs font-medium rounded-lg text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 cursor-pointer"
            >
              <Database className="w-3.5 h-3.5 text-amber-500" />
              <span>{t("batch.export_db_dump")}</span>
            </button>
          </div>
        )}
      </div>

      {/* AI Automation Actions */}
      <Button
        onClick={() => handleBatchAutoMap(false)}
        disabled={isProcessing}
        variant="outline"
        size="sm"
        className="h-9 px-3 text-xs font-semibold border-indigo-200 dark:border-indigo-900/60 bg-indigo-50/50 dark:bg-indigo-950/30 text-indigo-700 dark:text-indigo-300 hover:bg-indigo-50 dark:hover:bg-indigo-900/40 gap-1.5 cursor-pointer"
      >
        <Sparkles className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />
        <span>{t("batch.ai_categories")}</span>
      </Button>

      <Button
        onClick={() => handleBatchAutoMap(true)}
        disabled={isProcessing}
        size="sm"
        className="h-9 px-3 text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 text-white gap-1.5 shadow-xs cursor-pointer"
      >
        <Sparkles className="w-3.5 h-3.5" />
        <span>{t("batch.ai_attributes")}</span>
      </Button>

      <div className="w-px h-5 bg-slate-200 dark:bg-slate-800" />

      {/* Clear Selection */}
      <button
        onClick={() => setSelectedIds(new Set())}
        className="h-8 w-8 rounded-lg flex items-center justify-center text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors cursor-pointer"
        title={t("batch.clear")}
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}
