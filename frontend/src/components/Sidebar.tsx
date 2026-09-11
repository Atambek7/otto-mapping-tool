"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  FolderTree,
  ShieldCheck,
  DownloadCloud,
  Settings,
  LogOut,
  ChevronLeft,
  ChevronRight,
  Layers,
  PackageSearch
} from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { fetchWithAuth } from "@/lib/api";
import { useTranslation } from "react-i18next";

const NAV_ITEMS = [
  { key: "dashboard", href: "/dashboard", icon: LayoutDashboard },
  { key: "factories", href: "/dashboard/factories", icon: FolderTree },
  { key: "products", href: "/dashboard/products", icon: PackageSearch },
  { key: "validation", href: "/dashboard/validation", icon: ShieldCheck },
  { key: "export", href: "/dashboard/export", icon: DownloadCloud },
];

export function Sidebar() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const { t } = useTranslation();

  return (
    <aside
      className={cn(
        "border-r border-slate-200 dark:border-slate-800/80 bg-white dark:bg-[#0E131F] flex flex-col h-screen sticky top-0 shrink-0 transition-all duration-200 z-30 select-none",
        collapsed ? "w-[68px]" : "w-[240px]"
      )}
    >
      {/* Collapse Toggle Button */}
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="absolute -right-3 top-5 bg-white dark:bg-[#111827] border border-slate-200 dark:border-slate-800 rounded-full w-6 h-6 flex items-center justify-center shadow-sm hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors z-40 text-slate-500 hover:text-slate-900 dark:hover:text-slate-200 cursor-pointer"
        aria-label={collapsed ? t("nav.expand") : t("nav.collapse")}
      >
        {collapsed ? <ChevronRight className="w-3.5 h-3.5" /> : <ChevronLeft className="w-3.5 h-3.5" />}
      </button>

      {/* Brand Header */}
      <div className="h-16 flex items-center px-4 border-b border-slate-100 dark:border-slate-800/80 shrink-0">
        <Link href="/dashboard" className="flex items-center gap-3 overflow-hidden group">
          <div className="w-9 h-9 shrink-0 rounded-xl bg-indigo-600 dark:bg-indigo-500 flex items-center justify-center text-white shadow-sm shadow-indigo-500/20 transition-transform group-hover:scale-105">
            <Layers className="w-5 h-5" />
          </div>
          {!collapsed && (
            <div className="flex flex-col">
              <div className="flex items-center gap-1.5">
                <span className="font-bold text-slate-900 dark:text-white tracking-tight text-[17px] leading-none">
                  OttoFlow
                </span>
                <span className="text-[10px] font-semibold bg-indigo-50 dark:bg-indigo-950/60 text-indigo-600 dark:text-indigo-400 px-1.5 py-0.5 rounded border border-indigo-100 dark:border-indigo-900/50 uppercase tracking-wide">
                  PIM
                </span>
              </div>
              <span className="text-[11px] font-medium text-slate-400 dark:text-slate-500 mt-1 truncate">
                Otto Operations
              </span>
            </div>
          )}
        </Link>
      </div>

      {/* Main Navigation */}
      <nav className="flex-1 overflow-y-auto py-5 px-3 space-y-1">
        {!collapsed && (
          <div className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider px-3 mb-2">
            {t("nav.operations")}
          </div>
        )}
        {NAV_ITEMS.map((item) => {
          const label = t(`nav.${item.key}`);
          const isActive =
            pathname === item.href ||
            (pathname?.startsWith(item.href) && item.href !== "/dashboard");

          return (
            <Link
              key={item.key}
              href={item.href}
              className={cn(
                "flex items-center gap-3 px-3 h-10 rounded-lg text-sm font-medium transition-all",
                isActive
                  ? "bg-indigo-50/80 dark:bg-indigo-950/40 text-indigo-600 dark:text-indigo-300 font-semibold shadow-xs"
                  : "text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800/60 hover:text-slate-900 dark:hover:text-white",
                collapsed && "justify-center px-0"
              )}
              title={collapsed ? label : undefined}
            >
              <item.icon
                className={cn(
                  "w-4 h-4 shrink-0 transition-colors",
                  isActive ? "text-indigo-600 dark:text-indigo-400" : "text-slate-400 dark:text-slate-400"
                )}
              />
              {!collapsed && <span>{label}</span>}
            </Link>
          );
        })}
      </nav>

      {/* Footer / System Status & User Actions */}
      <div className="p-3 border-t border-slate-100 dark:border-slate-800/80 space-y-1">
        {!collapsed && (
          <div className="flex items-center justify-between px-3 py-2 text-xs text-slate-500 dark:text-slate-400">
            <div className="flex items-center gap-2">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
              </span>
              <span className="font-medium text-slate-600 dark:text-slate-300">
                {t("nav.live_sync")}
              </span>
            </div>
            <span className="text-[11px] font-mono text-slate-400">{t("nav.ready")}</span>
          </div>
        )}

        <Link
          href="/dashboard/settings"
          className={cn(
            "flex items-center gap-3 px-3 h-10 rounded-lg text-sm font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800/60 transition-colors",
            collapsed && "justify-center px-0",
            pathname === "/dashboard/settings" && "bg-slate-100 dark:bg-slate-800/80 text-slate-900 dark:text-white"
          )}
          title={collapsed ? t("nav.settings") : undefined}
        >
          <Settings className="w-4 h-4 shrink-0 text-slate-400" />
          {!collapsed && <span>{t("nav.settings")}</span>}
        </Link>

        <button
          className={cn(
            "w-full flex items-center gap-3 px-3 h-10 rounded-lg text-sm font-medium text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/30 transition-colors cursor-pointer",
            collapsed && "justify-center px-0"
          )}
          title={collapsed ? t("nav.logout") : undefined}
          onClick={async () => {
            try {
              await fetchWithAuth("/logout", { method: "POST" });
            } catch (e) {
              console.error(e);
            }
            localStorage.removeItem("token");
            localStorage.removeItem("openai_api_key");
            window.location.href = "/login";
          }}
        >
          <LogOut className="w-4 h-4 shrink-0 text-rose-500" />
          {!collapsed && <span>{t("nav.logout")}</span>}
        </button>
      </div>
    </aside>
  );
}

