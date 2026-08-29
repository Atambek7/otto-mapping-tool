"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Command } from "cmdk";
import { 
  FolderOpen, ShieldCheck, DownloadCloud, Settings, 
  Search, LayoutDashboard, Database
} from "lucide-react";
import { useAccount } from "@/lib/useAccount";

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const { account } = useAccount();

  // Toggle the menu when ⌘K is pressed
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((open) => !open);
      }
    };
    document.addEventListener("keydown", down);
    return () => document.removeEventListener("keydown", down);
  }, []);

  const runCommand = (command: () => void) => {
    setOpen(false);
    command();
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]">
      <div 
        className="fixed inset-0 bg-slate-900/40 dark:bg-black/60 backdrop-blur-sm" 
        onClick={() => setOpen(false)}
      />
      <Command 
        className="relative w-full max-w-2xl bg-white dark:bg-[#0B0F19] rounded-2xl shadow-2xl overflow-hidden border border-slate-200 dark:border-slate-800 flex flex-col"
        shouldFilter={true}
      >
        <div className="flex items-center px-4 border-b border-slate-100 dark:border-slate-800">
          <Search className="w-5 h-5 text-slate-400 shrink-0" />
          <Command.Input 
            autoFocus 
            placeholder="Что вы ищете? Поиск страниц, фабрик..." 
            className="w-full bg-transparent border-0 h-14 px-3 text-slate-900 dark:text-slate-100 placeholder:text-slate-400 focus:outline-none text-base"
          />
        </div>
        
        <Command.List className="max-h-[300px] overflow-y-auto p-2 scroll-smooth">
          <Command.Empty className="py-6 text-center text-sm text-slate-500">
            Ничего не найдено.
          </Command.Empty>

          <Command.Group heading="Навигация" className="text-xs font-semibold text-slate-500 px-2 py-1">
            <Command.Item 
              onSelect={() => runCommand(() => router.push("/dashboard"))}
              className="flex items-center gap-3 px-3 py-2.5 mt-1 rounded-xl cursor-pointer text-sm font-medium text-slate-700 dark:text-slate-300 aria-selected:bg-indigo-50 aria-selected:text-indigo-700 dark:aria-selected:bg-indigo-500/20 dark:aria-selected:text-indigo-300 transition-colors"
            >
              <LayoutDashboard className="w-4 h-4" />
              <span>Главная (Сводка)</span>
            </Command.Item>
            
            <Command.Item 
              onSelect={() => runCommand(() => router.push("/dashboard/factories"))}
              className="flex items-center gap-3 px-3 py-2.5 mt-1 rounded-xl cursor-pointer text-sm font-medium text-slate-700 dark:text-slate-300 aria-selected:bg-indigo-50 aria-selected:text-indigo-700 dark:aria-selected:bg-indigo-500/20 dark:aria-selected:text-indigo-300 transition-colors"
            >
              <FolderOpen className="w-4 h-4" />
              <span>Маппинг Workspace</span>
            </Command.Item>
            
            <Command.Item 
              onSelect={() => runCommand(() => router.push("/dashboard/validation"))}
              className="flex items-center gap-3 px-3 py-2.5 mt-1 rounded-xl cursor-pointer text-sm font-medium text-slate-700 dark:text-slate-300 aria-selected:bg-indigo-50 aria-selected:text-indigo-700 dark:aria-selected:bg-indigo-500/20 dark:aria-selected:text-indigo-300 transition-colors"
            >
              <ShieldCheck className="w-4 h-4" />
              <span>Validation Center</span>
            </Command.Item>

            <Command.Item 
              onSelect={() => runCommand(() => router.push("/dashboard/export"))}
              className="flex items-center gap-3 px-3 py-2.5 mt-1 rounded-xl cursor-pointer text-sm font-medium text-slate-700 dark:text-slate-300 aria-selected:bg-indigo-50 aria-selected:text-indigo-700 dark:aria-selected:bg-indigo-500/20 dark:aria-selected:text-indigo-300 transition-colors"
            >
              <DownloadCloud className="w-4 h-4" />
              <span>Publish Center</span>
            </Command.Item>
            
            <Command.Item 
              onSelect={() => runCommand(() => router.push("/dashboard/settings"))}
              className="flex items-center gap-3 px-3 py-2.5 mt-1 rounded-xl cursor-pointer text-sm font-medium text-slate-700 dark:text-slate-300 aria-selected:bg-indigo-50 aria-selected:text-indigo-700 dark:aria-selected:bg-indigo-500/20 dark:aria-selected:text-indigo-300 transition-colors"
            >
              <Settings className="w-4 h-4" />
              <span>Настройки & API</span>
            </Command.Item>
          </Command.Group>
          
          <Command.Group heading="Текущий аккаунт" className="text-xs font-semibold text-slate-500 px-2 py-1 mt-2">
            <Command.Item className="flex items-center gap-3 px-3 py-2.5 mt-1 rounded-xl text-sm font-medium text-slate-700 dark:text-slate-300 aria-selected:bg-slate-50 dark:aria-selected:bg-slate-800 transition-colors pointer-events-none">
              <Database className="w-4 h-4 text-emerald-500" />
              <span>{account || "Не выбран"}</span>
            </Command.Item>
          </Command.Group>
        </Command.List>
      </Command>
    </div>
  );
}

export function SearchHint() {
  return (
    <div 
      className="hidden sm:flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 text-slate-500 shadow-sm cursor-pointer hover:border-indigo-300 transition-colors" 
      onClick={() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }))}
    >
      <Search className="w-3.5 h-3.5" />
      <span className="text-xs font-medium">Поиск...</span>
      <div className="flex items-center gap-0.5 ml-2">
        <kbd className="bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold text-slate-500">⌘</kbd>
        <kbd className="bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold text-slate-500">K</kbd>
      </div>
    </div>
  );
}
