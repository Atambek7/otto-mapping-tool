"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  FolderOpen,
  Package,
  ListTree,
  ShieldCheck,
  DownloadCloud,
  Settings,
  LogOut,
  Wifi,
  FolderKanban,
  CloudLightning,
  ChevronLeft,
  ChevronRight
} from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { name: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
  { name: "Factories", href: "/dashboard/factories", icon: FolderOpen },
  { name: "Validation", href: "/dashboard/validation", icon: ShieldCheck },
  { name: "Export", href: "/dashboard/export", icon: DownloadCloud },
];

export function Sidebar() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  return (
    <aside className={cn("border-r border-slate-200 dark:border-slate-800 bg-white dark:bg-[#0B0F19] flex flex-col h-screen sticky top-0 shrink-0 transition-all duration-300 relative", collapsed ? "w-20" : "w-64")}>

      {/* Toggle Button */}
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="absolute -right-3 top-6 bg-white dark:bg-[#0B0F19] border border-slate-200 dark:border-slate-800 rounded-full p-1 shadow-sm hover:bg-slate-50 dark:hover:bg-slate-800/50 z-10 text-slate-500"
      >
        {collapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
      </button>

      {/* Logo */}
      <div className="h-16 flex items-center px-4 border-b border-slate-100 dark:border-slate-800 shrink-0 overflow-hidden bg-gradient-to-r from-slate-50 to-white dark:from-[#0B0F19] dark:to-[#111827]">
        <div className={cn("w-9 h-9 shrink-0 rounded-xl bg-gradient-to-br from-indigo-500 via-purple-500 to-fuchsia-500 flex items-center justify-center shadow-lg shadow-indigo-500/30 transition-all ring-1 ring-white/20 dark:ring-white/10", collapsed ? "mx-auto" : "ml-1 mr-3")}>
          <svg className="w-5 h-5 text-white drop-shadow-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
          </svg>
        </div>
        {!collapsed && (
          <div className="flex flex-col justify-center">
            <span className="font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-indigo-700 to-purple-600 dark:from-indigo-400 dark:to-purple-400 tracking-tight text-xl leading-none whitespace-nowrap drop-shadow-sm">OttoFlow</span>
            <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 tracking-[0.2em] uppercase leading-none mt-1 ml-0.5">AI PIM</span>
          </div>
        )}
      </div>

      {/* Main Nav */}
      <nav className="flex-1 overflow-y-auto py-6 px-3 space-y-1 overflow-x-hidden">
        {!collapsed && <div className="text-xs font-semibold text-slate-400 dark:text-white uppercase tracking-wider mb-3 px-2">Main Menu</div>}
        {NAV_ITEMS.map((item) => {
          const isActive = pathname === item.href || (pathname?.startsWith(item.href) && item.href !== "/dashboard");
          return (
            <Link
              key={item.name}
              href={item.href}
              className={cn(
                "flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors whitespace-nowrap",
                isActive
                  ? "bg-indigo-50 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-100"
                  : "text-slate-600 hover:bg-slate-50 hover:text-slate-900 dark:text-white dark:hover:bg-slate-800/50 dark:hover:text-slate-200",
                collapsed && "justify-center px-0"
              )}
              title={collapsed ? item.name : undefined}
            >
              <item.icon className={cn("w-5 h-5 shrink-0", isActive ? "text-indigo-600" : "text-slate-400")} />
              {!collapsed && <span>{item.name}</span>}
            </Link>
          );
        })}
      </nav>

      {/* Footer Nav */}
      <div className="p-3 border-t border-slate-100 dark:border-slate-800 space-y-1 overflow-hidden">
        {!collapsed && (
          <div className="flex items-center justify-between px-3 py-2 text-xs whitespace-nowrap">
            <div className="flex items-center gap-2 text-slate-500 dark:text-white">
              <div className="w-2 h-2 rounded-full bg-emerald-500 relative">
                <div className="absolute inset-0 rounded-full bg-emerald-500 animate-ping opacity-50" />
              </div>
              Online
            </div>
            <span className="text-slate-400 dark:text-white">Synced</span>
          </div>
        )}

        <Link
          href="/dashboard/settings"
          className={cn(
            "flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium text-slate-600 dark:text-white hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors whitespace-nowrap",
            collapsed && "justify-center px-0"
          )}
          title={collapsed ? "Settings" : undefined}
        >
          <Settings className="w-5 h-5 shrink-0 text-slate-400" />
          {!collapsed && <span>Settings</span>}
        </Link>
        <button
          className={cn(
            "w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/30 transition-colors whitespace-nowrap",
            collapsed && "justify-center px-0"
          )}
          title={collapsed ? "Sign Out" : undefined}
          onClick={() => {
            localStorage.removeItem("token");
            localStorage.removeItem("openai_api_key");
            window.location.href = "/login";
          }}
        >
          <LogOut className="w-5 h-5 shrink-0 text-red-400" />
          {!collapsed && <span>Sign Out</span>}
        </button>
      </div>
    </aside>
  );
}
