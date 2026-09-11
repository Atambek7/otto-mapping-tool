"use client";

import { Sidebar } from "@/components/Sidebar";
import { ThemeToggle } from "@/components/ThemeToggle";
import { CacheCleaner } from "@/components/CacheCleaner";
import { CommandPalette, SearchHint } from "@/components/CommandPalette";
import { AccountSwitcher } from "@/components/AccountSwitcher";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { ChevronRight, Home } from "lucide-react";

import { useTranslation } from "react-i18next";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const { t } = useTranslation();

  const getBreadcrumbs = () => {
    const parts = (pathname || "").split("/").filter(Boolean);
    const navLabels: Record<string, string> = {
      dashboard: t("nav.dashboard"),
      factories: t("nav.factories"),
      products: t("nav.products"),
      validation: t("nav.validation"),
      export: t("nav.export"),
      settings: t("nav.settings"),
    };

    if (parts.length <= 1) {
      return [{ label: navLabels.dashboard || "Dashboard", href: "/dashboard" }];
    }
    const items = [{ label: navLabels.dashboard || "Dashboard", href: "/dashboard" }];
    let acc = "/dashboard";
    for (let i = 1; i < parts.length; i++) {
      const part = parts[i];
      acc += `/${part}`;
      const label =
        navLabels[part] ||
        decodeURIComponent(part)
          .replace(/_/g, " ")
          .replace(/\b\w/g, (c) => c.toUpperCase());
      items.push({ label, href: acc });
    }
    return items;
  };

  const breadcrumbs = getBreadcrumbs();

  return (
    <div className="flex min-h-screen bg-[#F7F8FA] dark:bg-[#0B0F19] font-sans antialiased text-slate-900 dark:text-slate-100">
      <CacheCleaner />
      <CommandPalette />
      <Sidebar />
      <main className="flex-1 overflow-x-hidden flex flex-col h-screen">
        {/* Global Topbar */}
        <header className="h-16 px-6 lg:px-8 border-b border-slate-200/80 dark:border-slate-800/80 bg-white/80 dark:bg-[#0E131F]/80 backdrop-blur-md flex items-center justify-between shrink-0 sticky top-0 z-20">
          {/* Breadcrumb Context */}
          <nav className="flex items-center gap-1.5 text-xs font-medium text-slate-500 dark:text-slate-400">
            <Link
              href="/dashboard"
              className="flex items-center gap-1 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 transition-colors"
            >
              <Home className="w-3.5 h-3.5" />
            </Link>
            {breadcrumbs.map((crumb, idx) => {
              const isLast = idx === breadcrumbs.length - 1;
              return (
                <div key={crumb.href} className="flex items-center gap-1.5">
                  <ChevronRight className="w-3 h-3 text-slate-300 dark:text-slate-600" />
                  {isLast ? (
                    <span className="font-semibold text-slate-900 dark:text-white truncate max-w-[280px]">
                      {crumb.label}
                    </span>
                  ) : (
                    <Link
                      href={crumb.href}
                      className="hover:text-slate-700 dark:hover:text-slate-200 transition-colors truncate max-w-[200px]"
                    >
                      {crumb.label}
                    </Link>
                  )}
                </div>
              );
            })}
          </nav>

          {/* Right Action Tools */}
          <div className="flex items-center gap-2.5">
            <SearchHint />
            <div className="w-px h-4 bg-slate-200 dark:border-slate-800 hidden sm:block" />
            <AccountSwitcher />
            <ThemeToggle />
          </div>
        </header>

        {/* Page Content Container */}
        <div className="flex-1 flex flex-col overflow-y-auto px-6 lg:px-8 py-8 relative">
          <div className="w-full max-w-[1720px] mx-auto flex-1 flex flex-col">
            {children}
          </div>
        </div>
      </main>
    </div>
  );
}
