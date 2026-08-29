"use client";

import { useState, useEffect, useMemo } from "react";
import { fetchWithAuth } from "@/lib/api";
import {
  DownloadCloud, CheckCircle2, AlertTriangle, Loader2,
  ExternalLink, RefreshCw, History, Package,
  FileJson, ChevronRight, X
} from "lucide-react";
import { Button } from "@/components/ui/button";
import Link from "next/link";

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
  const [factories, setFactories] = useState<FactoryPreview[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [isDownloading, setIsDownloading] = useState(false);
  const [history, setHistory] = useState<Record<string, string>>({});

  useEffect(() => {
    setHistory(getHistory());
    loadFactories();
  }, []);

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
      const res = await fetchWithAuth("/export/factories");
      if (!res.ok) return;
      const ids: string[] = await res.json();
      
      // If we don't have all names, fetch them from aftercool
      if (ids.some(id => !nameMap[id])) {
        try {
          const factRes = await fetchWithAuth("/aftercool/factories");
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
            const r = await fetchWithAuth(`/export/preview?factory_id=${id}`);
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
      const res = await fetchWithAuth(`/export/download?factory_id=${factoryId}`);
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `export_${factoryId}_${new Date().toISOString().split('T')[0]}.json`;
      document.body.appendChild(a); a.click(); a.remove();
      saveHistory(factoryId);
      setHistory(getHistory());
    } catch (e) {
      console.error(e);
      alert("Не удалось скачать экспорт");
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
    <div className="flex flex-col space-y-6 pb-16">
      {/* ── HEADER ── */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-zinc-100 flex items-center gap-2">
            <Package className="w-6 h-6 text-indigo-600 dark:text-indigo-400" />
            Центр Публикации и Экспорта
          </h1>
          <p className="text-sm text-slate-500 dark:text-zinc-400 mt-0.5">
            Подготовка готовых данных в формате JSON для отправки в Otto Market
          </p>
        </div>
        <Button variant="outline" onClick={loadFactories} disabled={loading} className="gap-2 shrink-0">
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} /> Обновить
        </Button>
      </div>

      {/* ── SUMMARY STRIP ── */}
      {!loading && factories.length > 0 && (
        <div className="grid grid-cols-3 gap-3">
          <div className="bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800/50 rounded-xl p-4">
            <p className="text-xs text-emerald-600 dark:text-emerald-400 font-semibold uppercase tracking-wider mb-1">Всего готово</p>
            <p className="text-3xl font-extrabold text-emerald-700 dark:text-emerald-300 tabular-nums">
              {factories.reduce((s, f) => s + f.ready_count, 0).toLocaleString()}
            </p>
            <p className="text-xs text-emerald-600/60 mt-0.5">{factories.length} фабрик</p>
          </div>
          <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800/50 rounded-xl p-4">
            <p className="text-xs text-amber-600 dark:text-amber-400 font-semibold uppercase tracking-wider mb-1">С ошибками</p>
            <p className="text-3xl font-extrabold text-amber-700 dark:text-amber-300 tabular-nums">
              {factories.reduce((s, f) => s + f.error_count, 0).toLocaleString()}
            </p>
            <p className="text-xs text-amber-600/60 mt-0.5">требуют валидации</p>
          </div>
          <div className="bg-indigo-50 dark:bg-indigo-900/20 border border-indigo-200 dark:border-indigo-800/50 rounded-xl p-4">
            <p className="text-xs text-indigo-600 dark:text-indigo-400 font-semibold uppercase tracking-wider mb-1">Выбрано</p>
            <p className="text-3xl font-extrabold text-indigo-700 dark:text-indigo-300 tabular-nums">{totalReady.toLocaleString()}</p>
            <p className="text-xs text-indigo-600/60 mt-0.5">{selectedIds.size} фабрик готово к экспорту</p>
          </div>
        </div>
      )}

      {/* ── FACTORY GRID ── */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-300">Фабрики</h2>
          <button
            className="text-xs text-slate-500 hover:text-indigo-600 transition-colors cursor-pointer"
            onClick={() => {
              if (selectedIds.size === factories.filter(f => f.ready_count > 0).length) {
                setSelectedIds(new Set());
              } else {
                setSelectedIds(new Set(factories.filter(f => f.ready_count > 0).map(f => f.id)));
              }
            }}
          >
            {selectedIds.size === factories.filter(f => f.ready_count > 0).length && factories.length > 0 ? "Снять выбор" : "Выбрать все готовые"}
          </button>
        </div>

        {loading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {Array.from({length:6}).map((_,i) => <div key={i} className="h-28 bg-slate-100 dark:bg-slate-800/50 rounded-xl animate-pulse" />)}
          </div>
        ) : factories.length === 0 ? (
          <div className="p-12 text-center bg-white dark:bg-[#0B0F19] border border-dashed border-slate-200 dark:border-slate-800 rounded-xl">
            <Package className="w-10 h-10 text-slate-300 mx-auto mb-3" />
            <p className="text-slate-500 text-sm">Нет данных о фабриках.<br />Разметьте товары в фабриках.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {factories.map((f) => {
              const isSelected = selectedIds.has(f.id);
              const lastExp = history[f.id] ? new Date(history[f.id]).toLocaleDateString("ru-RU") : null;
              return (
                <div
                  key={f.id}
                  onClick={() => f.ready_count > 0 && setSelectedIds(prev => { const next = new Set(prev); if (next.has(f.id)) next.delete(f.id); else next.add(f.id); return next; })}
                  className={`relative p-4 rounded-xl border transition-all ${
                    f.ready_count === 0 ? "opacity-50 cursor-not-allowed bg-slate-50 dark:bg-slate-900/50 border-slate-200 dark:border-slate-800" :
                    isSelected ? "bg-indigo-50 border-indigo-300 dark:bg-indigo-950/30 dark:border-indigo-700 ring-2 ring-indigo-500/30 cursor-pointer" :
                    "bg-white dark:bg-[#0B0F19] border-slate-200 dark:border-slate-800 hover:border-indigo-200 dark:hover:border-indigo-800 cursor-pointer hover:shadow-sm"
                  }`}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1 min-w-0">
                      <p className="font-semibold text-slate-800 dark:text-white truncate text-sm">{f.name}</p>
                      <p className="text-xs text-slate-400 font-mono mt-0.5 truncate">{f.id}</p>
                    </div>
                    <div className="flex items-center gap-1.5 ml-2">
                      <Link
                        href={`/dashboard/factories/${f.id}`}
                        onClick={e => e.stopPropagation()}
                        className="p-1 rounded-md text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/30 transition-colors"
                        title="Перейти к фабрике"
                      >
                        <ExternalLink className="w-3.5 h-3.5" />
                      </Link>
                      <button
                        onClick={e => { e.stopPropagation(); handleDownload(f.id); }}
                        disabled={f.ready_count === 0 || isDownloading}
                        className="p-1 rounded-md text-slate-400 hover:text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-900/30 transition-colors disabled:opacity-30 cursor-pointer"
                        title="Скачать JSON"
                      >
                        <DownloadCloud className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                  <div className="mt-3 flex items-center gap-3">
                    <div className="flex items-center gap-1 text-xs">
                      <CheckCircle2 className="w-3 h-3 text-emerald-500" />
                      <span className="font-semibold text-emerald-600 dark:text-emerald-400 tabular-nums">{f.ready_count.toLocaleString()}</span>
                      <span className="text-slate-400">готово</span>
                    </div>
                    {f.error_count > 0 && (
                      <div className="flex items-center gap-1 text-xs">
                        <AlertTriangle className="w-3 h-3 text-amber-500" />
                        <span className="font-semibold text-amber-600 dark:text-amber-400 tabular-nums">{f.error_count.toLocaleString()}</span>
                        <span className="text-slate-400">ошибки</span>
                      </div>
                    )}
                    {lastExp && (
                      <div className="ml-auto flex items-center gap-1 text-[10px] text-slate-400">
                        <History className="w-3 h-3" /> {lastExp}
                      </div>
                    )}
                  </div>
                  {/* Readiness progress bar */}
                  {(f.ready_count + f.error_count) > 0 && (
                    <div className="mt-2 h-1 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-gradient-to-r from-emerald-400 to-emerald-500 rounded-full transition-all duration-700"
                        style={{ width: `${Math.round((f.ready_count / (f.ready_count + f.error_count)) * 100)}%` }}
                      />
                    </div>
                  )}
                  {isSelected && (
                    <div className="absolute top-3 right-3 w-4 h-4 rounded-full bg-indigo-500 flex items-center justify-center">
                      <CheckCircle2 className="w-3 h-3 text-white" />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── SELECTION EXPORT BAR ── */}
      {selectedIds.size > 0 && (
        <div className="selection-popup fixed bottom-6 left-1/2 -translate-x-1/2 bg-slate-900/95 backdrop-blur-md text-white px-5 py-3 rounded-2xl shadow-2xl shadow-indigo-500/20 flex items-center gap-4 z-50 animate-in slide-in-from-bottom-4 border border-slate-700/50">
          <div className="flex items-center gap-2 shrink-0">
            <div className="w-7 h-7 rounded-full bg-indigo-500/20 border border-indigo-500/40 flex items-center justify-center text-xs font-bold text-indigo-300">{selectedIds.size}</div>
            <div className="text-xs leading-tight">
              <div className="text-slate-200 font-medium">{selectedIds.size} фабрик выбрано</div>
              <div className="text-slate-500">{totalReady.toLocaleString()} готово к JSON</div>
            </div>
          </div>
          <div className="w-px h-8 bg-slate-700/70" />
          <button
            onClick={handleDownloadSelected}
            disabled={isDownloading || totalReady === 0}
            className="flex items-center gap-2 h-9 px-4 text-sm font-semibold rounded-xl bg-gradient-to-r from-emerald-500 to-teal-600 hover:from-emerald-600 hover:to-teal-700 text-white transition-all shadow-md shadow-emerald-500/30 disabled:opacity-60 cursor-pointer"
          >
            {isDownloading ? <Loader2 className="w-4 h-4 animate-spin" /> : <DownloadCloud className="w-4 h-4" />}
            Скачать JSON
          </button>
          <button onClick={() => setSelectedIds(new Set())} className="p-1.5 text-slate-500 hover:text-white transition-colors rounded-lg hover:bg-slate-800 cursor-pointer">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}
    </div>
  );
}
