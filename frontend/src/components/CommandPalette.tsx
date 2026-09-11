"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Command } from "cmdk";
import { 
  FolderOpen, ShieldCheck, DownloadCloud, Settings, 
  Search, LayoutDashboard, Database, PackageSearch
} from "lucide-react";
import { useAccount } from "@/lib/useAccount";
import { useTranslation } from "react-i18next";

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const { account } = useAccount();
  const { t } = useTranslation();

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
            placeholder={t("command_palette.search_placeholder")} 
            className="w-full bg-transparent border-0 h-14 px-3 text-slate-900 dark:text-slate-100 placeholder:text-slate-400 focus:outline-none text-base"
          />
        </div>
        
        <Command.List className="max-h-[300px] overflow-y-auto p-2 scroll-smooth">
          <Command.Empty className="py-6 text-center text-sm text-slate-500">
            {t("common.no_results", "Ничего не найдено.")}
          </Command.Empty>

          <Command.Group heading={t("command_palette.navigation")} className="text-xs font-semibold text-slate-500 px-2 py-1">
            <Command.Item 
              onSelect={() => runCommand(() => router.push("/dashboard"))}
              className="flex items-center gap-3 px-3 py-2.5 mt-1 rounded-lg cursor-pointer text-sm font-medium text-slate-700 dark:text-slate-300 aria-selected:bg-indigo-50 aria-selected:text-indigo-700 dark:aria-selected:bg-indigo-950/40 dark:aria-selected:text-indigo-300 transition-colors"
            >
              <LayoutDashboard className="w-4 h-4 text-indigo-500" />
              <span>{t("nav.dashboard")}</span>
            </Command.Item>
            
            <Command.Item 
              onSelect={() => runCommand(() => router.push("/dashboard/factories"))}
              className="flex items-center gap-3 px-3 py-2.5 mt-1 rounded-lg cursor-pointer text-sm font-medium text-slate-700 dark:text-slate-300 aria-selected:bg-indigo-50 aria-selected:text-indigo-700 dark:aria-selected:bg-indigo-950/40 dark:aria-selected:text-indigo-300 transition-colors"
            >
              <FolderOpen className="w-4 h-4 text-indigo-500" />
              <span>{t("nav.factories")}</span>
            </Command.Item>

            <Command.Item 
              onSelect={() => runCommand(() => router.push("/dashboard/products"))}
              className="flex items-center gap-3 px-3 py-2.5 mt-1 rounded-lg cursor-pointer text-sm font-medium text-slate-700 dark:text-slate-300 aria-selected:bg-indigo-50 aria-selected:text-indigo-700 dark:aria-selected:bg-indigo-950/40 dark:aria-selected:text-indigo-300 transition-colors"
            >
              <PackageSearch className="w-4 h-4 text-indigo-500" />
              <span>{t("nav.products")}</span>
            </Command.Item>
            
            <Command.Item 
              onSelect={() => runCommand(() => router.push("/dashboard/validation"))}
              className="flex items-center gap-3 px-3 py-2.5 mt-1 rounded-lg cursor-pointer text-sm font-medium text-slate-700 dark:text-slate-300 aria-selected:bg-indigo-50 aria-selected:text-indigo-700 dark:aria-selected:bg-indigo-950/40 dark:aria-selected:text-indigo-300 transition-colors"
            >
              <ShieldCheck className="w-4 h-4 text-amber-500" />
              <span>{t("nav.validation")}</span>
            </Command.Item>

            <Command.Item 
              onSelect={() => runCommand(() => router.push("/dashboard/export"))}
              className="flex items-center gap-3 px-3 py-2.5 mt-1 rounded-lg cursor-pointer text-sm font-medium text-slate-700 dark:text-slate-300 aria-selected:bg-indigo-50 aria-selected:text-indigo-700 dark:aria-selected:bg-indigo-950/40 dark:aria-selected:text-indigo-300 transition-colors"
            >
              <DownloadCloud className="w-4 h-4 text-emerald-500" />
              <span>{t("nav.export")}</span>
            </Command.Item>
            
            <Command.Item 
              onSelect={() => runCommand(() => router.push("/dashboard/settings"))}
              className="flex items-center gap-3 px-3 py-2.5 mt-1 rounded-lg cursor-pointer text-sm font-medium text-slate-700 dark:text-slate-300 aria-selected:bg-indigo-50 aria-selected:text-indigo-700 dark:aria-selected:bg-indigo-950/40 dark:aria-selected:text-indigo-300 transition-colors"
            >
              <Settings className="w-4 h-4 text-slate-500" />
              <span>{t("nav.settings")}</span>
            </Command.Item>
          </Command.Group>
          
          <Command.Group heading={account === "JV" ? "JV Möbel" : "XL Möbel"} className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 px-2 py-1 mt-2">
            <Command.Item className="flex items-center gap-3 px-3 py-2.5 mt-1 rounded-lg text-sm font-medium text-slate-700 dark:text-slate-300 pointer-events-none">
              <Database className="w-4 h-4 text-indigo-500" />
              <span>{account || "None selected"}</span>
            </Command.Item>
          </Command.Group>
        </Command.List>
      </Command>
    </div>
  );
}

export function SearchHint() {
  const { t } = useTranslation();

  return (
    <div 
      className="hidden sm:flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-white dark:bg-[#111827] border border-slate-200 dark:border-slate-800 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:border-slate-300 dark:hover:border-slate-700 shadow-xs cursor-pointer transition-all select-none" 
      onClick={() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }))}
    >
      <Search className="w-3.5 h-3.5" />
      <span className="text-xs font-medium">{t("command_palette.hint")}</span>
      <div className="flex items-center gap-0.5 ml-3">
        <kbd className="bg-slate-100 dark:bg-slate-800 px-1 py-0.5 rounded text-[10px] font-mono font-semibold text-slate-500 border border-slate-200/60 dark:border-slate-700/60">⌘</kbd>
        <kbd className="bg-slate-100 dark:bg-slate-800 px-1 py-0.5 rounded text-[10px] font-mono font-semibold text-slate-500 border border-slate-200/60 dark:border-slate-700/60">K</kbd>
      </div>
    </div>
  );
}
