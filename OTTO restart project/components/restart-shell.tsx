"use client";

import type { FormEvent, ReactNode } from "react";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Bell, CircleHelp, LogOut, Menu, Moon, PanelLeftClose, PanelLeftOpen, Search, Sun, X } from "lucide-react";
import { AccountSwitcher } from "@/components/account-switcher";
import { ApiConnectionStatus } from "@/components/api-connection-status";
import { RestartSidebar } from "@/components/restart-sidebar";
import { RestartAccountProvider } from "@/features/state/restart-account";

export function RestartShell({ children }: { children: ReactNode }) {
  return <RestartAccountProvider><ShellContent>{children}</ShellContent></RestartAccountProvider>;
}

function ShellContent({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [themeReady, setThemeReady] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [sidebarHidden, setSidebarHidden] = useState(false);
  const [sidebarReady, setSidebarReady] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => {
    const saved = window.localStorage.getItem("ottoRestart:theme");
    if (saved === "dark" || saved === "light") setTheme(saved);
    setThemeReady(true);
  }, []);
  useEffect(() => {
    if (themeReady) window.localStorage.setItem("ottoRestart:theme", theme);
  }, [theme, themeReady]);
  useEffect(() => {
    setSidebarHidden(window.localStorage.getItem("ottoRestart:sidebar-hidden") === "true");
    setSidebarReady(true);
  }, []);
  useEffect(() => {
    if (sidebarReady) window.localStorage.setItem("ottoRestart:sidebar-hidden", String(sidebarHidden));
  }, [sidebarHidden, sidebarReady]);
  useEffect(() => {
    if (!mobileMenuOpen) {
      document.body.style.overflow = "";
      return;
    }
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, [mobileMenuOpen]);

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = search.trim();
    if (!query) return;
    router.push(`/afterbuy?q=${encodeURIComponent(query)}`);
    setSearchOpen(false);
  }

  async function handleLogout() {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await fetch("/restart/api/auth/logout", { method: "POST" });
    } catch {
      // Ignore errors on logout
    } finally {
      router.replace("/login");
      router.refresh();
      setLoggingOut(false);
    }
  }

  // Standalone layout for login page: no sidebar, no topbar
  if (pathname === "/login") {
    return (
      <div className="restart-app restart-app-login" data-theme={theme}>
        {children}
      </div>
    );
  }

  return (
    <div className="restart-app" data-theme={theme}>
      <RestartSidebar desktopHidden={sidebarHidden} mobileOpen={mobileMenuOpen} onNavigate={() => setMobileMenuOpen(false)} />
      {mobileMenuOpen ? <button className="restart-nav-scrim" type="button" aria-label="Закрыть навигацию" onClick={() => setMobileMenuOpen(false)} /> : null}
      <main className="restart-main">
        <header className="restart-topbar">
          <div className="topbar-context">
            <button className="icon-button mobile-nav-toggle" type="button" aria-label={mobileMenuOpen ? "Закрыть меню" : "Открыть меню"} aria-expanded={mobileMenuOpen} onClick={() => setMobileMenuOpen((open) => !open)}>{mobileMenuOpen ? <X size={18} /> : <Menu size={18} />}</button>
            <button className="icon-button desktop-sidebar-toggle" type="button" aria-label={sidebarHidden ? "Показать боковую панель" : "Скрыть боковую панель"} title={sidebarHidden ? "Показать боковую панель" : "Скрыть боковую панель"} aria-controls="restart-navigation" aria-expanded={!sidebarHidden} onClick={() => setSidebarHidden((hidden) => !hidden)}>{sidebarHidden ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}</button>
            <span className="topbar-brand-label">OttoRestart</span>
            <span className="topbar-divider" />
            <span>Управление каталогом 3/12M</span>
          </div>
          <div className="topbar-actions">
            <ApiConnectionStatus />
            <AccountSwitcher />
            <button
              className="icon-button theme-toggle"
              type="button"
              aria-label={`Включить ${theme === "dark" ? "светлую" : "тёмную"} тему OttoRestart`}
              title={`Включить ${theme === "dark" ? "светлую" : "тёмную"} тему`}
              onClick={() => setTheme((current) => current === "dark" ? "light" : "dark")}
            >
              {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            <div className="topbar-search-slot">{searchOpen ? <form className="topbar-search-form" onSubmit={submitSearch}><input autoFocus value={search} onChange={(event) => setSearch(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") setSearchOpen(false); }} placeholder="Поиск по EAN или названию" aria-label="Поиск по товарам" /><button type="submit" aria-label="Найти"><Search size={15} /></button></form> : <button className="icon-button" type="button" aria-label="Поиск товара" onClick={() => setSearchOpen(true)}><Search size={17} /></button>}</div>
            <Link className="icon-button" href="/library" title="Журнал операций в OTTO Library" aria-label="Открыть журнал в OTTO Library"><Bell size={17} /></Link>
            <details className="topbar-help"><summary className="icon-button help-button" aria-label="Справка OttoRestart"><CircleHelp size={17} /></summary><div className="topbar-help-popover"><strong>Справка OttoRestart</strong><span>Инструмент подготовки и перезапуска товаров 3 и 12 месяцев с планировщиком Drip Feed.</span><Link href="/settings">Открыть настройки</Link></div></details>
            <button
              className="icon-button logout-button"
              type="button"
              aria-label="Выйти из системы"
              title="Выйти из системы (admin)"
              disabled={loggingOut}
              onClick={handleLogout}
            >
              <LogOut size={16} />
            </button>
          </div>
        </header>
        <div className="restart-content">{children}</div>
      </main>
    </div>
  );
}
