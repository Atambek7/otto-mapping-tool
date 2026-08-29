import { Loader2, DownloadCloud, Trash2, ChevronDown, Braces, FileSpreadsheet, Database, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";

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
  setSelectedIds
}: BatchActionBarProps) {
  if (selectedIds.size === 0) return null;

  const isProcessing = downloading || isAutoMapping;
  const progress = downloadProgress || autoMapProgress;
  const percent = progress && progress.total > 0 ? Math.round((progress.current / progress.total) * 100) : (isProcessing ? null : 0);
  
  let actionLabel = "Обработка...";
  let IconComponent = DownloadCloud;
  let iconColor = "text-indigo-400";
  
  // Try to infer the action based on props and progress
  if (isAutoMapping) {
    actionLabel = "Магия AI в процессе...";
    IconComponent = Sparkles;
    iconColor = "text-purple-400";
  } else if (downloading) {
    if (progress) {
      actionLabel = "Синхронизация кэша...";
      IconComponent = DownloadCloud;
      iconColor = "text-blue-400";
    } else {
      actionLabel = "Подготовка экспорта / Удаление...";
      IconComponent = FileSpreadsheet;
      iconColor = "text-amber-400";
    }
  }

  if (isProcessing) {
    return (
      <div className="fixed bottom-6 left-1/2 -translate-x-1/2 w-[460px] bg-slate-900 text-white rounded-2xl shadow-2xl shadow-indigo-500/40 flex flex-col z-50 animate-in slide-in-from-bottom-5 overflow-hidden border border-slate-700/50">
        {/* Subtle background fill - either static or filling */}
        <div 
          className="absolute left-0 top-0 bottom-0 bg-gradient-to-r from-indigo-500/10 to-purple-500/10 transition-all duration-500 ease-out" 
          style={{ width: percent !== null ? `${percent}%` : '100%', opacity: percent !== null ? 1 : 0.5 }} 
        />
        
        <div className="relative p-5 z-10">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-full bg-slate-800 flex items-center justify-center border border-slate-700 shadow-inner">
                {isAutoMapping ? (
                  <IconComponent className={`w-5 h-5 ${iconColor} animate-pulse`} />
                ) : (
                  <IconComponent className={`w-5 h-5 ${iconColor} animate-bounce`} />
                )}
              </div>
              <div>
                <div className="font-bold text-slate-100 tracking-tight text-sm">{actionLabel}</div>
                <div className="text-xs text-slate-400 mt-0.5 font-medium">
                  {progress ? `Обработано ${progress.current} из ${progress.total}` : "Пожалуйста, подождите..."}
                </div>
              </div>
            </div>
            <div className="text-2xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 to-purple-400 tabular-nums tracking-tighter drop-shadow-sm">
              {percent !== null ? `${percent}%` : <Loader2 className="w-6 h-6 animate-spin text-indigo-400" />}
            </div>
          </div>
          
          <div className="relative h-2 w-full bg-slate-800 rounded-full overflow-hidden shadow-inner">
            {percent !== null ? (
              <div 
                className="absolute left-0 top-0 bottom-0 bg-gradient-to-r from-indigo-500 via-purple-500 to-fuchsia-500 transition-all duration-300 ease-out" 
                style={{ width: `${percent}%` }}
              />
            ) : (
              <div className="absolute left-0 top-0 bottom-0 w-1/2 bg-gradient-to-r from-indigo-500 via-purple-500 to-fuchsia-500 animate-[pulse_2s_ease-in-out_infinite] translate-x-1/4 rounded-full" />
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-slate-900/95 backdrop-blur-md text-white px-6 py-4 rounded-2xl shadow-2xl shadow-indigo-500/20 flex items-center gap-4 z-50 animate-in slide-in-from-bottom-5 border border-slate-700/50">
      <div className="flex items-center gap-3">
        <div className="w-6 h-6 rounded-full bg-indigo-500 flex items-center justify-center text-xs font-bold shadow-inner">
          {selectedIds.size}
        </div>
        <span className="font-medium tracking-tight text-slate-200">выбрано</span>
      </div>
      <div className="w-px h-6 bg-slate-700"></div>
      
      {Array.from(selectedIds).some(id => !cachedIds[id]) && (
        <Button 
          onClick={handleBatchCache} 
          disabled={isProcessing}
          className="bg-indigo-500 hover:bg-indigo-600 text-white rounded-xl h-10 px-5 font-medium gap-2 border-none shadow-sm transition-all hover:scale-105"
        >
          <DownloadCloud className="w-4 h-4" /> Скачать кэш
        </Button>
      )}

      {Array.from(selectedIds).some(id => cachedIds[id]) && (
        <Button 
          onClick={handleBatchDeleteCache} 
          disabled={isProcessing}
          className="bg-slate-800 hover:bg-rose-500 text-slate-300 hover:text-white rounded-xl h-10 px-5 font-medium gap-2 border-none shadow-sm transition-all hover:scale-105"
        >
          <Trash2 className="w-4 h-4" /> Удалить кэш
        </Button>
      )}

      <div className="relative group/dropdown">
        <div className="flex bg-amber-400 hover:bg-amber-500 text-slate-900 rounded-xl shadow-lg shadow-amber-500/20 transition-all overflow-hidden h-10 border-none hover:scale-105 cursor-pointer">
          <button 
            onClick={() => handleBatchExport('csv')}
            disabled={isProcessing}
            className="flex items-center gap-2 px-5 font-bold transition-colors outline-none focus:outline-none focus:ring-0 border-none"
          >
            <FileSpreadsheet className="w-4 h-4" /> Экспорт
          </button>
          <div className="w-[1px] bg-amber-500/30 my-1.5"></div>
          <button className="px-3 flex items-center justify-center hover:bg-amber-600 transition-colors outline-none focus:outline-none focus:ring-0 border-none">
            <ChevronDown className="w-4 h-4 opacity-70" />
          </button>
        </div>
        
        <div className="absolute bottom-full right-0 mb-3 bg-slate-900 border border-slate-700/50 rounded-2xl shadow-2xl p-1.5 flex gap-1 opacity-0 invisible group-hover/dropdown:opacity-100 group-hover/dropdown:visible transition-all translate-y-2 group-hover/dropdown:translate-y-0 z-50">
          <button 
            onClick={() => handleBatchExport('json')}
            disabled={isProcessing}
            className="flex flex-col items-center justify-center w-[72px] py-2.5 rounded-xl hover:bg-slate-800 text-slate-300 hover:text-white transition-colors"
          >
            <Braces className="w-5 h-5 mb-1.5 text-slate-400" />
            <span className="text-[11px] font-bold tracking-wider">JSON</span>
          </button>
          <div className="w-[1px] bg-slate-700/50 my-2"></div>
          <button 
            onClick={() => handleBatchExport('csv')}
            disabled={isProcessing}
            className="flex flex-col items-center justify-center w-[72px] py-2.5 rounded-xl hover:bg-slate-800 text-slate-300 hover:text-white transition-colors"
          >
            <FileSpreadsheet className="w-5 h-5 mb-1.5 text-slate-400" />
            <span className="text-[11px] font-bold tracking-wider">CSV</span>
          </button>
          <div className="w-[1px] bg-slate-700/50 my-2"></div>
          <button 
            onClick={() => handleBatchExport('db')}
            disabled={isProcessing}
            className="flex flex-col items-center justify-center w-[72px] py-2.5 rounded-xl hover:bg-slate-800 text-slate-300 hover:text-white transition-colors"
          >
            <Database className="w-5 h-5 mb-1.5 text-slate-400" />
            <span className="text-[11px] font-bold tracking-wider">DB</span>
          </button>
        </div>
      </div>

      <Button
        onClick={() => handleBatchAutoMap(false)}
        disabled={isProcessing}
        className="bg-purple-600 hover:bg-purple-700 text-white rounded-xl h-10 px-5 font-medium gap-2 border-none shadow-sm transition-all hover:scale-105"
      >
        <Sparkles className="w-4 h-4" /> AI Categories
      </Button>

      <Button
        onClick={() => handleBatchAutoMap(true)}
        disabled={isProcessing}
        className="bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-600 hover:to-purple-700 text-white rounded-xl h-10 px-5 font-bold gap-2 border-none shadow-lg shadow-indigo-500/30 transition-all hover:scale-105"
      >
        <Sparkles className="w-4 h-4" /> AI Attributes
      </Button>

      {!isProcessing && (
        <button 
          onClick={() => setSelectedIds(new Set())}
          className="p-2 -mr-2 text-slate-400 hover:text-white transition-colors rounded-xl hover:bg-slate-800/80 font-medium ml-1"
        >
          Отмена
        </button>
      )}
    </div>
  );
}
