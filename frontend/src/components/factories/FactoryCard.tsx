import React from "react";
import { useRouter } from "next/navigation";
import { Folder, FolderOpen, AlertCircle, LayoutGrid, CheckCircle2, Database, ArrowUpRight } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";

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
  isCached
}: FactoryCardProps) => {
  const router = useRouter();
  const hasInvalid = invalidCount > 0;
  const isValidation = variant === "validation";

  const renderStatusBadge = () => {
    let text = "Нет данных";
    let cls = "bg-slate-100 dark:bg-slate-800/50 text-slate-500 dark:text-slate-400 border-slate-200 dark:border-slate-800/50";
    
    if (totalProducts > 0) {
      text = `${totalProducts} товаров`;
      cls = "bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400 border-emerald-200 dark:border-emerald-800/50";
    } else if (isCached) {
      text = "Доступно в кэше";
      cls = "bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-400 border-indigo-200 dark:border-indigo-800/50";
    }

    return (
      <div className="flex items-center gap-2">
        {isCached && (
          <div className="flex items-center gap-1.5 text-[10px] uppercase font-bold tracking-wider text-indigo-500 dark:text-indigo-400 bg-indigo-50 dark:bg-indigo-900/30 px-2 py-1 rounded-md">
            <Database className="w-3 h-3" /> Cached
          </div>
        )}
        <Badge variant="outline" className={`shrink-0 border bg-transparent font-medium shadow-none px-2 py-0.5 rounded-full text-xs ${cls}`}>
          <div className="flex items-center gap-1.5">
            {totalProducts > 0 ? <CheckCircle2 className="w-3 h-3 text-emerald-500" /> : <AlertCircle className="w-3 h-3 text-slate-300" />} 
            {text}
          </div>
        </Badge>
      </div>
    );
  };

  if (viewMode === "grid") {
    return (
      <div 
        data-factory-id={id}
        onMouseDown={onMouseDown}
        onClick={onClick}
        className={`group relative flex flex-col items-center p-6 rounded-2xl border transition-all cursor-pointer ${
          isSelected 
            ? "bg-indigo-50/50 border-indigo-200 dark:bg-indigo-950/20 dark:border-indigo-800" 
            : isValidation 
              ? "bg-white border-red-100 hover:border-red-200 dark:bg-[#0B0F19] dark:border-red-900/50 dark:hover:border-red-800" 
              : "bg-white border-slate-100 hover:border-indigo-100 dark:bg-[#0B0F19] dark:border-slate-800 dark:hover:border-slate-700 hover:shadow-md"
        }`}
      >
        <div className="absolute top-4 left-4 checkbox-area z-10" onClick={(e) => e.stopPropagation()}>
          <Checkbox 
            checked={isSelected}
            onCheckedChange={onToggleSelect}
            className={`transition-opacity ${isSelected ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`}
          />
        </div>

        {/* Hover Action: Jump to all products in Factory Workspace */}
        <button
          onClick={(e) => {
            e.stopPropagation();
            router.push(`/dashboard/factories/${id}`);
          }}
          className="absolute top-3.5 right-3.5 flex items-center gap-1 px-2.5 py-1 rounded-xl bg-slate-100/90 dark:bg-slate-800/90 hover:bg-indigo-600 hover:text-white dark:hover:bg-indigo-600 text-slate-600 dark:text-slate-300 text-[11px] font-semibold shadow-sm transition-all opacity-0 group-hover:opacity-100 hover:scale-105 z-20 backdrop-blur-sm border border-slate-200/60 dark:border-slate-700/60 cursor-pointer"
          title="Открыть все товары фабрики"
        >
          <span>К фабрике</span>
          <ArrowUpRight className="w-3.5 h-3.5" />
        </button>
        
        {isValidation ? (
          <div className={`w-20 h-20 mb-4 rounded-full flex items-center justify-center transition-colors ${
            isSelected ? 'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-500' : 'bg-red-50 dark:bg-red-900/20 text-red-500 group-hover:bg-red-100 dark:group-hover:bg-red-900/40'
          }`}>
            <AlertCircle className="w-10 h-10" strokeWidth={1.5} />
          </div>
        ) : (
          <div className={`w-20 h-20 mb-4 rounded-full flex items-center justify-center transition-colors ${
            isSelected ? 'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-500' : 'bg-slate-50 dark:bg-slate-800 text-slate-400 group-hover:bg-indigo-50 dark:group-hover:bg-indigo-900/20 group-hover:text-indigo-400'
          }`}>
            {isSelected ? <FolderOpen className="w-10 h-10" strokeWidth={1.5} /> : <Folder className="w-10 h-10" strokeWidth={1.5} />}
          </div>
        )}

        <h3 className="font-medium text-slate-900 dark:text-white text-center line-clamp-1 max-w-full">{name}</h3>
        


        {isValidation ? (
          <div className="mt-3 w-full space-y-1.5">
            <div className="flex items-center justify-center gap-1.5">
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400">
                <AlertCircle className="w-3 h-3" />
                {invalidCount} с ошибками
              </span>
            </div>
            {/* Top 2 error types */}
            {Object.entries(errorBreakdown).sort((a,b) => b[1]-a[1]).slice(0,2).map(([label, count]) => (
              <div key={label} className="flex items-center gap-1.5 px-1">
                <span className="text-[10px] text-slate-400 truncate flex-1" title={label}>{label}</span>
                <span className="text-[10px] font-mono font-semibold text-slate-500">{count}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="mt-3 flex items-center justify-center">
            {renderStatusBadge()}
          </div>
        )}
      </div>
    );
  }

  // List view
  return (
    <div 
      data-factory-id={id}
      onMouseDown={onMouseDown}
      onClick={onClick}
      className={`group flex items-center p-4 rounded-xl border transition-all cursor-pointer ${
        isSelected 
          ? "bg-indigo-50/50 border-indigo-200 dark:bg-indigo-950/20 dark:border-indigo-800" 
          : isValidation 
            ? "bg-white border-red-100 hover:border-red-200 dark:bg-[#0B0F19] dark:border-red-900/50 dark:hover:border-red-800"
            : "bg-white border-slate-100 hover:border-indigo-100 dark:bg-[#0B0F19] dark:border-slate-800 dark:hover:border-slate-700"
      }`}
    >
      <div className="flex-shrink-0 mr-4 checkbox-area" onClick={(e) => e.stopPropagation()}>
        <Checkbox 
          checked={isSelected}
          onCheckedChange={onToggleSelect}
        />
      </div>
      
      {isValidation ? (
        <div className={`w-10 h-10 mr-4 rounded-full flex flex-shrink-0 items-center justify-center transition-colors ${
          isSelected ? 'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-500' : 'bg-red-50 dark:bg-red-900/20 text-red-500 group-hover:bg-red-100 dark:group-hover:bg-red-900/40'
        }`}>
          <AlertCircle className="w-5 h-5" strokeWidth={1.5} />
        </div>
      ) : (
        <div className={`w-10 h-10 mr-4 rounded-full flex flex-shrink-0 items-center justify-center transition-colors ${
          isSelected ? 'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-500' : 'bg-slate-50 dark:bg-slate-800 text-slate-400 group-hover:bg-indigo-50 dark:group-hover:bg-indigo-900/20 group-hover:text-indigo-400'
        }`}>
          {isSelected ? <FolderOpen className="w-5 h-5" strokeWidth={1.5} /> : <Folder className="w-5 h-5" strokeWidth={1.5} />}
        </div>
      )}

      <div className="flex-1 min-w-0 pr-4">
        <h3 className="font-medium text-slate-900 dark:text-white truncate">{name}</h3>
        {!isValidation && (
          <div className="text-xs text-slate-500 mt-0.5 truncate flex items-center gap-2">
            <span>{mappedCount} mapped</span>
          </div>
        )}
      </div>

      <div className="flex-shrink-0 flex items-center gap-2">
        {/* Hover Action: Jump to all products in Factory Workspace */}
        <button
          onClick={(e) => {
            e.stopPropagation();
            router.push(`/dashboard/factories/${id}`);
          }}
          className="flex items-center gap-1 px-2.5 py-1 rounded-xl bg-slate-100/90 dark:bg-slate-800/90 hover:bg-indigo-600 hover:text-white dark:hover:bg-indigo-600 text-slate-600 dark:text-slate-300 text-[11px] font-semibold shadow-sm transition-all opacity-0 group-hover:opacity-100 hover:scale-105 z-20 backdrop-blur-sm border border-slate-200/60 dark:border-slate-700/60 cursor-pointer mr-1"
          title="Открыть все товары фабрики"
        >
          <span>К фабрике</span>
          <ArrowUpRight className="w-3.5 h-3.5" />
        </button>

        {isValidation ? (
          <div className="flex flex-col items-end gap-1">
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400">
              <AlertCircle className="w-3 h-3" />
              {invalidCount} ошибок
            </span>
            {Object.entries(errorBreakdown).sort((a,b) => b[1]-a[1]).slice(0,1).map(([label, count]) => (
              <span key={label} className="text-[10px] text-slate-400 truncate max-w-[140px]" title={label}>{label}: {count}</span>
            ))}
          </div>
        ) : (
          renderStatusBadge()
        )}
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
    prevProps.isCached === nextProps.isCached
  );
});
