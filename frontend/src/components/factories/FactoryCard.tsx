"use client";

import React from "react";
import { useRouter } from "next/navigation";
import {
  FolderTree,
  AlertCircle,
  CheckCircle2,
  Database,
  ArrowUpRight,
  ChevronRight,
  Package,
  ArrowRightLeft
} from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";
import { useAccount } from "@/lib/useAccount";
import { useTranslation } from "react-i18next";

export interface FactoryCardProps {
  id: string;
  name: string;
  totalProducts?: number;
  mappedCount?: number;
  invalidCount?: number;
  errorBreakdown?: Record<string, number>;
  isSelected: boolean;
  onToggleSelect: () => void;
  onMouseDown?: (e: React.MouseEvent) => void;
  viewMode: "grid" | "list";
  variant?: "default" | "validation";
  onClick?: () => void;
  isCached?: boolean;
  isTransferred?: boolean;
  transferredFrom?: string;
}

const FactoryCardComponent = ({
  id,
  name,
  totalProducts = 0,
  mappedCount = 0,
  invalidCount = 0,
  errorBreakdown = {},
  isSelected,
  onToggleSelect,
  onMouseDown,
  viewMode,
  variant = "default",
  onClick,
  isCached,
  isTransferred,
  transferredFrom,
}: FactoryCardProps) => {
  const router = useRouter();
  const { account } = useAccount();
  const { t } = useTranslation();
  const isValidation = variant === "validation";
  const hoverTimer = React.useRef<NodeJS.Timeout | null>(null);

  const handleMouseEnter = () => {
    if (isCached || isValidation) return;
    hoverTimer.current = setTimeout(async () => {
      try {
        const { getCachedFactory, setCachedFactory } = await import("@/lib/cache");
        const existing = await getCachedFactory(id, account);
        if (existing?.products?.length) return;
        const { fetchWithAuth } = await import("@/lib/api");
        const res = await fetchWithAuth(`/aftercool/products?factory_id=${id}&account=${account}&dataset=lister&limit=5000&include_row=1&sync_db=0`);
        if (res.ok) {
          const data = await res.json();
          const items = data?.items || data || [];
          if (Array.isArray(items) && items.length > 0) {
            await setCachedFactory({ factoryId: id, products: items, total: items.length, fetchedAt: Date.now() }, false, account);
          }
        }
      } catch (e) {}
    }, 150);
  };

  const handleMouseLeave = () => {
    if (hoverTimer.current) {
      clearTimeout(hoverTimer.current);
      hoverTimer.current = null;
    }
  };

  if (viewMode === "grid") {
    return (
      <div
        data-factory-id={id}
        onMouseDown={onMouseDown}
        onClick={onClick}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        className={cn(
          "group relative flex flex-col justify-between p-4 rounded-xl border bg-white dark:bg-[#111827] transition-all cursor-pointer select-none",
          isSelected
            ? "border-indigo-600 dark:border-indigo-500 bg-indigo-50/40 dark:bg-indigo-950/20 shadow-xs ring-1 ring-indigo-600/30"
            : isValidation
            ? "border-slate-200/80 dark:border-slate-800 hover:border-rose-300 dark:hover:border-rose-800/80 hover:shadow-xs"
            : "border-slate-200/80 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 hover:shadow-xs"
        )}
      >
        {/* Top bar: Checkbox & Badges / Actions */}
        <div className="flex items-center justify-between mb-3">
          <div className="checkbox-area" onClick={(e) => e.stopPropagation()}>
            <Checkbox
              checked={isSelected}
              onCheckedChange={onToggleSelect}
              className={cn(
                "transition-opacity",
                isSelected ? "opacity-100" : "opacity-40 group-hover:opacity-100"
              )}
            />
          </div>

          <div className="flex items-center gap-1.5">
            {isCached && (
              <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-indigo-700 dark:text-indigo-300 bg-indigo-50 dark:bg-indigo-950/60 px-1.5 py-0.5 rounded border border-indigo-100 dark:border-indigo-900/40">
                <Database className="w-2.5 h-2.5" />
                {t("factories.cached")}
              </span>
            )}
            {isTransferred && (
              <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-violet-700 dark:text-violet-300 bg-violet-50 dark:bg-violet-950/60 px-1.5 py-0.5 rounded border border-violet-100 dark:border-violet-900/40" title={`Перенесено из ${transferredFrom || "JV"}`}>
                <ArrowRightLeft className="w-2.5 h-2.5" />
                Из {transferredFrom || "JV"}
              </span>
            )}
            <button
              onClick={(e) => {
                e.stopPropagation();
                router.push(isValidation ? `/dashboard/validation/${id}` : `/dashboard/factories/${id}`);
              }}
              className="w-7 h-7 rounded-lg flex items-center justify-center text-slate-400 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
              title={t("factories.open_workspace")}
            >
              <ArrowUpRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        {/* Catalog Info */}
        <div className="mb-3">
          <div className="flex items-start gap-2.5">
            <div
              className={cn(
                "w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-0.5",
                isValidation
                  ? "bg-rose-50 dark:bg-rose-950/50 text-rose-600 dark:text-rose-400"
                  : "bg-indigo-50 dark:bg-indigo-950/50 text-indigo-600 dark:text-indigo-400"
              )}
            >
              {isValidation ? <AlertCircle className="w-4 h-4" /> : <FolderTree className="w-4 h-4" />}
            </div>
            <div className="min-w-0 flex-1">
              <h3
                className="font-semibold text-slate-900 dark:text-white text-sm leading-snug line-clamp-2"
                title={name}
              >
                {name}
              </h3>
              <p className="text-[11px] font-mono text-slate-400 dark:text-slate-500 mt-0.5 truncate">
                ID: {id}
              </p>
            </div>
          </div>
        </div>

        {/* Footer info: Product Count or Validation errors */}
        <div className="pt-2.5 border-t border-slate-100 dark:border-slate-800/80 flex items-center justify-between text-xs">
          {isValidation ? (
            <div className="flex items-center justify-between w-full">
              <span className="inline-flex items-center gap-1 font-semibold text-[11px] text-rose-600 dark:text-rose-400 bg-rose-50 dark:bg-rose-950/40 px-2 py-0.5 rounded-md">
                <AlertCircle className="w-3 h-3" />
                {invalidCount} {t("validation.errors", "ошибок")}
              </span>
              <span className="text-[11px] text-slate-400">{t("validation.requires_review", "Требует внимания")}</span>
            </div>
          ) : (
            <div className="flex items-center justify-between w-full">
              <div className="flex items-center gap-1.5 text-slate-600 dark:text-slate-300 font-medium text-xs">
                <Package className="w-3.5 h-3.5 text-slate-400" />
                <span>{totalProducts > 0 ? `${totalProducts.toLocaleString()} ${t("factories.items_suffix")}` : t("factories.no_data")}</span>
              </div>
              {mappedCount > 0 && (
                <span className="text-[11px] text-emerald-600 dark:text-emerald-400 font-medium">
                  {mappedCount} {t("dashboard.in_pipeline", "в обработке")}
                </span>
              )}
            </div>
          )}
        </div>
      </div>
    );
  }

  // List View (Dense enterprise row)
  return (
    <div
      data-factory-id={id}
      onMouseDown={onMouseDown}
      onClick={onClick}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      className={cn(
        "group flex items-center justify-between px-4 py-3 rounded-xl border bg-white dark:bg-[#111827] transition-all cursor-pointer select-none",
        isSelected
          ? "border-indigo-600 dark:border-indigo-500 bg-indigo-50/40 dark:bg-indigo-950/20 shadow-xs ring-1 ring-indigo-600/30"
          : isValidation
          ? "border-slate-200/80 dark:border-slate-800 hover:border-rose-300 dark:hover:border-rose-800/80 hover:bg-slate-50/50"
          : "border-slate-200/80 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 hover:bg-slate-50/50"
      )}
    >
      <div className="flex items-center gap-3.5 min-w-0 flex-1 pr-4">
        <div className="checkbox-area shrink-0" onClick={(e) => e.stopPropagation()}>
          <Checkbox
            checked={isSelected}
            onCheckedChange={onToggleSelect}
            className={cn(
              "transition-opacity",
              isSelected ? "opacity-100" : "opacity-40 group-hover:opacity-100"
            )}
          />
        </div>

        <div
          className={cn(
            "w-8 h-8 rounded-lg flex items-center justify-center shrink-0",
            isValidation
              ? "bg-rose-50 dark:bg-rose-950/50 text-rose-600 dark:text-rose-400"
              : "bg-indigo-50 dark:bg-indigo-950/50 text-indigo-600 dark:text-indigo-400"
          )}
        >
          {isValidation ? <AlertCircle className="w-4 h-4" /> : <FolderTree className="w-4 h-4" />}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="font-semibold text-slate-900 dark:text-white text-sm truncate" title={name}>
              {name}
            </h3>
            {isCached && (
              <span className="shrink-0 inline-flex items-center gap-1 text-[10px] font-semibold text-indigo-700 dark:text-indigo-300 bg-indigo-50 dark:bg-indigo-950/60 px-1.5 py-0.2 rounded border border-indigo-100 dark:border-indigo-900/40">
                <Database className="w-2.5 h-2.5" />
                {t("factories.cached")}
              </span>
            )}
            {isTransferred && (
              <span className="shrink-0 inline-flex items-center gap-1 text-[10px] font-semibold text-violet-700 dark:text-violet-300 bg-violet-50 dark:bg-violet-950/60 px-1.5 py-0.2 rounded border border-violet-100 dark:border-violet-900/40" title={`Перенесено из ${transferredFrom || "JV"}`}>
                <ArrowRightLeft className="w-2.5 h-2.5" />
                Из {transferredFrom || "JV"}
              </span>
            )}
          </div>
          <p className="text-[11px] font-mono text-slate-400 dark:text-slate-500 mt-0.5">
            ID: {id}
          </p>
        </div>
      </div>

      <div className="flex items-center gap-6 shrink-0">
        {isValidation ? (
          <div className="flex items-center gap-3">
            <span className="inline-flex items-center gap-1 font-semibold text-xs text-rose-600 dark:text-rose-400 bg-rose-50 dark:bg-rose-950/40 px-2 py-0.5 rounded-md">
              <AlertCircle className="w-3 h-3" />
              {invalidCount.toLocaleString()} {t("validation.errors", "ошибок")}
            </span>
            {Object.entries(errorBreakdown)
              .sort((a, b) => b[1] - a[1])
              .slice(0, 1)
              .map(([label, count]) => (
                <span
                  key={label}
                  className="text-xs text-slate-400 truncate max-w-[140px] hidden md:inline-block"
                  title={label}
                >
                  {label}: {count}
                </span>
              ))}
          </div>
        ) : (
          <div className="flex items-center gap-4 text-xs">
            <div className="flex items-center gap-1.5 text-slate-600 dark:text-slate-300 font-medium">
              <Package className="w-3.5 h-3.5 text-slate-400" />
              <span>{totalProducts.toLocaleString()} {t("factories.items_suffix")}</span>
            </div>
            {mappedCount > 0 && (
              <span className="text-emerald-600 dark:text-emerald-400 font-medium hidden sm:inline-block">
                {mappedCount} {t("dashboard.in_pipeline", "в обработке")}
              </span>
            )}
          </div>
        )}

        <button
          onClick={(e) => {
            e.stopPropagation();
            router.push(isValidation ? `/dashboard/validation/${id}` : `/dashboard/factories/${id}`);
          }}
          className="h-8 px-2.5 rounded-lg border border-slate-200 dark:border-slate-800 bg-white dark:bg-[#111827] text-xs font-semibold text-slate-700 dark:text-slate-300 hover:text-indigo-600 dark:hover:text-indigo-400 hover:border-slate-300 dark:hover:border-slate-700 flex items-center gap-1 transition-colors"
        >
          <span>{t("common.open", "Открыть")}</span>
          <ChevronRight className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
};

export const FactoryCard = React.memo(FactoryCardComponent, (prevProps, nextProps) => {
  return (
    prevProps.id === nextProps.id &&
    prevProps.isSelected === nextProps.isSelected &&
    prevProps.viewMode === nextProps.viewMode &&
    prevProps.invalidCount === nextProps.invalidCount &&
    prevProps.totalProducts === nextProps.totalProducts &&
    prevProps.isCached === nextProps.isCached &&
    prevProps.isTransferred === nextProps.isTransferred &&
    prevProps.transferredFrom === nextProps.transferredFrom
  );
});

