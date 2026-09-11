"use client";

import { useState, useEffect, useMemo } from "react";
import { fetchWithAuth } from "@/lib/api";
import { useAccount } from "@/lib/useAccount";
import {
  DownloadCloud, CheckCircle2, AlertTriangle, Loader2,
  ExternalLink, RefreshCw, History, Package,
  FileJson, ChevronRight, X
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import Link from "next/link";
import { useTranslation } from "react-i18next";

interface FactoryPreview {
  id: string;
  name: string;
  ready_count: number;
  error_count: number;
  lastExported?: string;
}

const HISTORY_KEY = "ottoflow_export_history";

function getHistory(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || "{}"); } catch { return {}; }
}
function saveHistory(factoryId: string) {
  const h = getHistory();
  h[factoryId] = new Date().toISOString();
  localStorage.setItem(HISTORY_KEY, JSON.stringify(h));
}

export default function ExportPage() {
  const { account } = useAccount();
  const { t } = useTranslation();
  const [factories, setFactories] = useState<FactoryPreview[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [isDownloading, setIsDownloading] = useState(false);
  const [history, setHistory] = useState<Record<string, string>>({});

  useEffect(() => {
    setHistory(getHistory());
    loadFactories();
  }, [account]);

  const loadFactories = async () => {
    setLoading(true);
    let nameMap: Record<string, string> = {};
    try {
      const saved = localStorage.getItem("otto_all_factory_names") || localStorage.getItem("otto_factories_list");
      if (saved) {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed)) {
          parsed.forEach((f: any) => { if (f.id && f.name) nameMap[String(f.id)] = f.name; });
        } else {
          nameMap = parsed;
        }
      }
    } catch (e) {}

    try {
      const res = await fetchWithAuth(`/export/factories?account=${account}`);
      if (!res.ok) return;
      const ids: string[] = await res.json();
      
      // If we don't have all names, fetch them from aftercool
      if (ids.some(id => !nameMap[id])) {
        try {
          const factRes = await fetchWithAuth(`/aftercool/factories?account=${account}`);
          if (factRes.ok) {
            const raw = await factRes.json();
            const list = Array.isArray(raw) ? raw : (raw?.items ?? []);
            list.forEach((f: any) => { if (f.id && f.name) nameMap[String(f.id)] = f.name; });
            localStorage.setItem("otto_all_factory_names", JSON.stringify(nameMap));
          }
        } catch (e) {}
      }

      // Load previews for all factories in parallel
      const previews = await Promise.all(
        ids.map(async (id) => {
          try {
            const r = await fetchWithAuth(`/export/preview?factory_id=${id}&account=${account}`);
            if (r.ok) {
              const d = await r.json();
              return { id, name: nameMap[id] || id, ready_count: d.ready_count || 0, error_count: d.error_count || 0 };
            }
          } catch (e) {}
          return { id, name: nameMap[id] || id, ready_count: 0, error_count: 0 };
        })
      );
      setFactories(previews.sort((a, b) => b.ready_count - a.ready_count));
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const totalReady = useMemo(() =>
    Array.from(selectedIds).reduce((s, id) => s + (factories.find(f => f.id === id)?.ready_count || 0), 0)
  , [selectedIds, factories]);

  const handleDownload = async (factoryId: string) => {
    setIsDownloading(true);
    try {
      const f = factories.find(x => x.id === factoryId);
      const factoryTitle = f?.name || factoryId;
      const cleanName = factoryTitle.replace(/[\\/:*?"<>|]/g, "_");
      const res = await fetchWithAuth(`/export/otto_json/${factoryId}?account=${account}`);
      if (!res.ok) throw new Error("Export failed");
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${cleanName}.json`;
      document.body.appendChild(a); a.click(); a.remove();
      saveHistory(factoryId);
      setHistory(getHistory());
    } catch (e) {
      console.error(e);
      alert(t("export_center.export_failed"));
    } finally {
      setIsDownloading(false);
    }
  };

  const handleDownloadSelected = async () => {
    if (selectedIds.size === 0) return;
    setIsDownloading(true);
    try {
      for (const id of Array.from(selectedIds)) {
        const f = factories.find(x => x.id === id);
        if (!f || f.ready_count === 0) continue;
        await handleDownload(id);
      }
    } finally {
      setIsDownloading(false);
    }
  };

  return (
    <div className="flex flex-col space-y-6 pb-20">
      {/* ── HEADER ── */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight flex items-center gap-2">
            <Package className="w-6 h-6 text-indigo-600 dark:text-indigo-400" />
            {t("export_center.title")}
            <span className="text-xs font-semibold text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-slate-800 px-2 py-0.5 rounded-md ml-1">
              {account}
            </span>
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
            {t("export_center.subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={loadFactories}
            disabled={loading}
            className="gap-1.5 h-9 border-slate-200 dark:border-slate-800 bg-white dark:bg-card text-xs font-medium hover:bg-slate-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            <span>{t("common.refresh")}</span>
          </Button>
        </div>
      </div>

      {/* ── SUMMARY STRIP ── */}
      {!loading && factories.length > 0 && (
        <div className="bg-white dark:bg-card rounded-xl border border-slate-200/80 dark:border-slate-800 shadow-xs divide-y sm:divide-y-0 sm:divide-x divide-slate-100 dark:divide-slate-800 grid grid-cols-1 sm:grid-cols-3">
          <div className="p-4">
            <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block">
              {t("export_center.total_ready_items")}
            </span>
            <span className="text-2xl font-bold text-emerald-600 dark:text-emerald-400 tabular-nums tracking-tight mt-1 block">
              {factories.reduce((s, f) => s + f.ready_count, 0).toLocaleString()}
            </span>
            <span className="text-xs text-slate-400 mt-0.5 block">
              {factories.length} {t("export_center.catalogs_loaded")}
            </span>
          </div>
          <div className="p-4">
            <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block">
              {t("export_center.needs_validation")}
            </span>
            <span className="text-2xl font-bold text-amber-600 dark:text-amber-400 tabular-nums tracking-tight mt-1 block">
              {factories.reduce((s, f) => s + f.error_count, 0).toLocaleString()}
            </span>
            <span className="text-xs text-slate-400 mt-0.5 block">
              {t("export_center.require_resolution")}
            </span>
          </div>
          <div className="p-4">
            <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider block">
              {t("export_center.selected_for_export")}
            </span>
            <span className="text-2xl font-bold text-indigo-600 dark:text-indigo-400 tabular-nums tracking-tight mt-1 block">
              {totalReady.toLocaleString()}
            </span>
            <span className="text-xs text-slate-400 mt-0.5 block">
              {selectedIds.size} {t("export_center.catalogs_selected")}
            </span>
          </div>
        </div>
      )}

      {/* ── FACTORY CATALOGS ── */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
            {t("export_center.publication_catalogs")}
          </h2>
          {factories.length > 0 && (
            <button
              className="text-xs text-indigo-600 dark:text-indigo-400 hover:text-indigo-700 font-medium transition-colors cursor-pointer"
              onClick={() => {
                const readyFactories = factories.filter(f => f.ready_count > 0);
                if (selectedIds.size === readyFactories.length) {
                  setSelectedIds(new Set());
                } else {
                  setSelectedIds(new Set(readyFactories.map(f => f.id)));
                }
              }}
            >
              {selectedIds.size === factories.filter(f => f.ready_count > 0).length && factories.length > 0
                ? t("export_center.deselect_all")
                : t("export_center.select_all_ready")}
            </button>
          )}
        </div>

        {loading ? (
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="h-16 bg-white dark:bg-card border border-slate-200/60 dark:border-slate-800 rounded-xl animate-pulse" />
            ))}
          </div>
        ) : factories.length === 0 ? (
          <div className="py-20 text-center flex flex-col items-center justify-center space-y-3 bg-white dark:bg-card border border-slate-200/80 dark:border-slate-800 rounded-xl p-8 shadow-xs">
            <div className="w-14 h-14 bg-slate-100 dark:bg-slate-800 text-slate-400 rounded-full flex items-center justify-center mb-1">
              <Package className="w-7 h-7" />
            </div>
            <h3 className="text-base font-semibold text-slate-900 dark:text-white">
              {t("export_center.no_catalogs_ready")}
            </h3>
            <p className="text-xs text-slate-500 dark:text-slate-400 max-w-sm">
              {t("export_center.no_catalogs_ready_desc")}
            </p>
            <div className="pt-2">
              <Link href="/dashboard/validation">
                <Button size="sm" variant="outline" className="gap-2 border-slate-200 dark:border-slate-800 text-xs font-semibold">
                  <span>{t("export_center.go_to_validation")}</span>
                  <ChevronRight className="w-3.5 h-3.5" />
                </Button>
              </Link>
            </div>
          </div>
        ) : (
          <div className="bg-white dark:bg-card border border-slate-200/80 dark:border-slate-800 rounded-xl shadow-xs overflow-hidden">
            <table className="w-full text-left text-xs border-collapse">
              <thead className="bg-slate-50/90 dark:bg-slate-900/80 backdrop-blur-xs border-b border-slate-200/80 dark:border-slate-800 text-slate-500 font-semibold sticky top-0">
                <tr>
                  <th className="py-3 px-4 w-12">
                    <input
                      type="checkbox"
                      className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500 cursor-pointer"
                      checked={selectedIds.size === factories.filter(f => f.ready_count > 0).length && factories.length > 0}
                      onChange={() => {
                        const ready = factories.filter(f => f.ready_count > 0);
                        if (selectedIds.size === ready.length) setSelectedIds(new Set());
                        else setSelectedIds(new Set(ready.map(f => f.id)));
                      }}
                    />
                  </th>
                  <th className="py-3 px-4">{t("export_center.th_catalog_name")}</th>
                  <th className="py-3 px-4 w-36">{t("export_center.th_ready_otto")}</th>
                  <th className="py-3 px-4 w-36">{t("export_center.th_validation_issues")}</th>
                  <th className="py-3 px-4 w-40">{t("export_center.th_last_exported")}</th>
                  <th className="py-3 px-4 w-28 text-right">{t("export_center.th_actions")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800/60">
                {factories.map((f) => {
                  const isSelected = selectedIds.has(f.id);
                  const lastExp = history[f.id] ? new Date(history[f.id]).toLocaleDateString("de-DE") : null;
                  const isClickable = f.ready_count > 0;

                  return (
                    <tr
                      key={f.id}
                      onClick={() => isClickable && setSelectedIds(prev => {
                        const next = new Set(prev);
                        if (next.has(f.id)) next.delete(f.id); else next.add(f.id);
                        return next;
                      })}
                      className={cn(
                        "transition-colors",
                        isClickable ? "hover:bg-slate-50/70 dark:hover:bg-slate-800/30 cursor-pointer" : "opacity-60 bg-slate-50/30 dark:bg-slate-900/10 cursor-not-allowed",
                        isSelected && "bg-indigo-50/50 dark:bg-indigo-950/20"
                      )}
                    >
                      <td className="py-3 px-4" onClick={e => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          disabled={!isClickable}
                          checked={isSelected}
                          onChange={() => {
                            setSelectedIds(prev => {
                              const next = new Set(prev);
                              if (next.has(f.id)) next.delete(f.id); else next.add(f.id);
                              return next;
                            });
                          }}
                          className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500 cursor-pointer disabled:opacity-30"
                        />
                      </td>
                      <td className="py-3 px-4">
                        <div className="font-semibold text-slate-900 dark:text-white text-xs truncate max-w-sm" title={f.name}>
                          {f.name}
                        </div>
                        <div className="text-[11px] font-mono text-slate-400 mt-0.5">ID: {f.id}</div>
                      </td>
                      <td className="py-3 px-4">
                        <span className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200/80 dark:border-emerald-800/60 px-2 py-0.5 rounded-md">
                          <CheckCircle2 className="w-3 h-3 text-emerald-500" />
                          <span>{t("export_center.items_count", { count: f.ready_count.toLocaleString() })}</span>
                        </span>
                      </td>
                      <td className="py-3 px-4">
                        {f.error_count > 0 ? (
                          <span className="inline-flex items-center gap-1 text-xs font-semibold text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/40 border border-amber-200/80 dark:border-amber-800/60 px-2 py-0.5 rounded-md">
                            <AlertTriangle className="w-3 h-3 text-amber-500" />
                            <span>{t("export_center.errors_count", { count: f.error_count.toLocaleString() })}</span>
                          </span>
                        ) : (
                          <span className="text-slate-400 text-xs">{t("export_center.none")}</span>
                        )}
                      </td>
                      <td className="py-3 px-4 text-slate-500 dark:text-slate-400 font-mono text-xs">
                        {lastExp ? (
                          <div className="flex items-center gap-1 text-[11px]">
                            <History className="w-3 h-3 text-slate-400" />
                            <span>{lastExp}</span>
                          </div>
                        ) : (
                          <span className="text-slate-300 dark:text-slate-600">{t("export_center.never")}</span>
                        )}
                      </td>
                      <td className="py-3 px-4 text-right" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center justify-end gap-1.5">
                          <Link
                            href={`/dashboard/factories/${f.id}`}
                            className="p-1.5 rounded-lg text-slate-400 hover:text-indigo-600 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
                            title={t("export_center.open_catalog")}
                          >
                            <ExternalLink className="w-3.5 h-3.5" />
                          </Link>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => handleDownload(f.id)}
                            disabled={f.ready_count === 0 || isDownloading}
                            className="h-7 px-2 text-xs border-slate-200 dark:border-slate-800 bg-white dark:bg-card text-slate-700 dark:text-slate-300 hover:bg-slate-50 gap-1"
                            title={t("export_center.download_json")}
                          >
                            <DownloadCloud className="w-3 h-3 text-slate-500" />
                            <span>JSON</span>
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── FLOATING BULK EXPORT BAR ── */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-8 left-1/2 -translate-x-1/2 bg-white dark:bg-card text-slate-900 dark:text-white px-4 py-3 rounded-xl shadow-2xl border border-slate-200 dark:border-slate-800 flex items-center gap-3 z-50 animate-in slide-in-from-bottom-4">
          <div className="flex items-center gap-2 shrink-0">
            <div className="h-6 px-2 rounded-md bg-indigo-50 dark:bg-indigo-950/60 border border-indigo-200/80 dark:border-indigo-900/60 flex items-center justify-center text-xs font-bold text-indigo-700 dark:text-indigo-300">
              {selectedIds.size}
            </div>
            <div className="text-xs leading-tight">
              <div className="text-slate-900 dark:text-white font-medium">
                {t("export_center.floating_selected", { count: selectedIds.size })}
              </div>
              <div className="text-slate-400 text-[10px]">
                {t("export_center.floating_ready", { count: totalReady.toLocaleString() })}
              </div>
            </div>
          </div>

          <div className="w-px h-6 bg-slate-200 dark:bg-slate-700" />

          <Button
            size="sm"
            onClick={handleDownloadSelected}
            disabled={isDownloading || totalReady === 0}
            className="h-8 text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 text-white gap-1.5 shadow-xs"
          >
            {isDownloading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <DownloadCloud className="w-3.5 h-3.5" />}
            <span>{t("export_center.download_all_json")}</span>
          </Button>

          <button
            onClick={() => setSelectedIds(new Set())}
            className="p-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 transition-colors rounded-md hover:bg-slate-100 dark:hover:bg-slate-800"
            title={t("export_center.deselect_all")}
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}
