"use client";

import type { FormEvent, ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Bell, CircleHelp, LogOut, Menu, Moon, PanelLeftClose, PanelLeftOpen, Search, Sun, X } from "lucide-react";
import { AccountSwitcher } from "@/components/account-switcher";
import { ApiConnectionStatus } from "@/components/api-connection-status";
import { RestartSidebar } from "@/components/restart-sidebar";
import { RestartAccountProvider } from "@/features/state/restart-account";
import { RestartTimezoneProvider } from "@/features/state/restart-timezone";
import { clearCsrfToken, getCsrfToken } from "@/features/auth/csrf-client";
import { useTranslation, setAppLanguage } from "@/features/i18n";

export function RestartShell({ children }: { children: ReactNode }) {
  return (
    <RestartAccountProvider>
      <RestartTimezoneProvider>
        <ShellContent>{children}</ShellContent>
      </RestartTimezoneProvider>
    </RestartAccountProvider>
  );
}

function ShellContent({ children }: { children: ReactNode }) {
  const { t, i18n } = useTranslation();
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
  const [navigationPending, setNavigationPending] = useState(false);
  const navigationTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  useEffect(() => {
    if (navigationTimeout.current) clearTimeout(navigationTimeout.current);
    navigationTimeout.current = null;
    setNavigationPending(false);
  }, [pathname]);

  useEffect(() => () => {
    if (navigationTimeout.current) clearTimeout(navigationTimeout.current);
  }, []);

  function handleSidebarNavigation(href: string) {
    setMobileMenuOpen(false);
    const currentPath = pathname?.startsWith("/restart") ? pathname.slice("/restart".length) || "/" : pathname || "/";
    const targetPath = href.split(/[?#]/, 1)[0] || "/";
    const normalize = (path: string) => path.replace(/\/+$/, "") || "/";
    if (normalize(currentPath) === normalize(targetPath)) return;

    if (navigationTimeout.current) clearTimeout(navigationTimeout.current);
    setNavigationPending(true);
    navigationTimeout.current = setTimeout(() => {
      setNavigationPending(false);
      navigationTimeout.current = null;
    }, 12000);
  }

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
      const csrfToken = await getCsrfToken();
      await fetch("/restart/api/auth/logout", {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRF-Token": csrfToken },
      });
    } catch {
      // Ignore errors on logout
    } finally {
      clearCsrfToken();
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
      {navigationPending ? <div className="restart-route-progress" role="status" aria-live="polite"><span className="restart-route-progress-bar" /><span className="restart-route-progress-label">Загружаем раздел OttoRestart…</span></div> : null}
      <RestartSidebar desktopHidden={sidebarHidden} mobileOpen={mobileMenuOpen} onNavigate={handleSidebarNavigation} />
      {mobileMenuOpen ? <button className="restart-nav-scrim" type="button" aria-label="Закрыть навигацию" onClick={() => setMobileMenuOpen(false)} /> : null}
      <main className="restart-main">
        <header className="restart-topbar">
          <div className="topbar-context">
            <button className="icon-button mobile-nav-toggle" type="button" aria-label={mobileMenuOpen ? "Закрыть меню" : "Открыть меню"} aria-expanded={mobileMenuOpen} onClick={() => setMobileMenuOpen((open) => !open)}>{mobileMenuOpen ? <X size={18} /> : <Menu size={18} />}</button>
            <button className="icon-button desktop-sidebar-toggle" type="button" aria-label={sidebarHidden ? "Показать боковую панель" : "Скрыть боковую панель"} title={sidebarHidden ? "Показать боковую панель" : "Скрыть боковую панель"} aria-controls="restart-navigation" aria-expanded={!sidebarHidden} onClick={() => setSidebarHidden((hidden) => !hidden)}>{sidebarHidden ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}</button>
            <span className="topbar-brand-label">OttoRestart</span>
            <span className="topbar-divider" />
            <span>{t("nav.catalogManagement")}</span>
          </div>
          <div className="topbar-actions">
            <ApiConnectionStatus />
            <AccountSwitcher />
            <button
              className="icon-button lang-toggle-pill"
              type="button"
              style={{ fontSize: 11, fontWeight: 700, width: "auto", minWidth: 36, padding: "0 6px" }}
              aria-label={t("topbar.quickLang")}
              title={i18n.language === "en" ? "Переключить на Русский" : "Switch to English"}
              onClick={() => setAppLanguage(i18n.language === "en" ? "ru" : "en")}
            >
              {i18n.language === "en" ? "EN" : "RU"}
            </button>
            <button
              className="icon-button theme-toggle"
              type="button"
              aria-label={theme === "dark" ? t("topbar.themeLight") : t("topbar.themeDark")}
              title={theme === "dark" ? t("topbar.themeLight") : t("topbar.themeDark")}
              onClick={() => setTheme((current) => current === "dark" ? "light" : "dark")}
            >
              {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            <div className="topbar-search-slot">{searchOpen ? <form className="topbar-search-form" onSubmit={submitSearch}><input autoFocus value={search} onChange={(event) => setSearch(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") setSearchOpen(false); }} placeholder={t("topbar.searchPlaceholder")} aria-label={t("common.search")} /><button type="submit" aria-label={t("common.search")}><Search size={15} /></button></form> : <button className="icon-button" type="button" aria-label={t("common.search")} onClick={() => setSearchOpen(true)}><Search size={17} /></button>}</div>
            <Link className="icon-button" href="/library" title="OTTO Library" aria-label="OTTO Library"><Bell size={17} /></Link>
            <details className="topbar-help"><summary className="icon-button help-button" aria-label="Справка"><CircleHelp size={17} /></summary><div className="topbar-help-popover"><strong>OttoRestart</strong><span>{t("welcome.heroSubtitle")}</span><Link href="/settings">{t("nav.settings")}</Link></div></details>
            <button
              className="icon-button logout-button"
              type="button"
              aria-label={t("nav.logout")}
              title={`${t("nav.logout")} (admin)`}
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
