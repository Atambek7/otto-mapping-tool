"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BookOpenText,
  Clock3,
  Factory,
  House,
  ScanSearch,
  Send,
  Settings2,
  type LucideIcon,
} from "lucide-react";
import { ProductSwitcher } from "@/components/product-switcher";
import { useTranslation } from "@/features/i18n";

type NavigationItem = { href: string; label: string; icon: LucideIcon };
type NavigationGroup = { label: string; items: NavigationItem[] };

export function RestartSidebar({ desktopHidden = false, mobileOpen = false, onNavigate }: { desktopHidden?: boolean; mobileOpen?: boolean; onNavigate?: (href: string) => void }) {
  const { t } = useTranslation();
  const pathname = usePathname();
  const restartPath = pathname?.startsWith("/restart")
    ? pathname.slice("/restart".length) || "/"
    : pathname || "/";

  const groups: NavigationGroup[] = [
    { label: t("nav.sectionOverview"), items: [{ href: "/", label: t("nav.welcome"), icon: House }] },
    {
      label: t("nav.sectionCatalogs"),
      items: [
        { href: "/discover", label: t("nav.discover"), icon: ScanSearch },
        { href: "/afterbuy", label: t("nav.afterbuy"), icon: Factory },
        { href: "/direct-otto", label: t("nav.directOtto"), icon: Send },
      ],
    },
    { label: t("nav.sectionData"), items: [{ href: "/library", label: t("nav.library"), icon: BookOpenText }] },
  ];

  return (
    <aside id="restart-navigation" className={`restart-sidebar${desktopHidden ? " desktop-hidden" : ""}${mobileOpen ? " mobile-open" : ""}`}>
      <ProductSwitcher />
      <div className="sidebar-rule" />
      <nav aria-label={t("nav.sectionOverview")} className="restart-nav">
        {groups.map((group) => (
          <section className="nav-group" key={group.label}>
            <h2>{group.label}</h2>
            {group.items.map(({ href, label, icon: Icon }) => {
              const active = href === "/"
                ? restartPath === "/"
                : href === "/library"
                  ? ["/library", "/ean-pool", "/history"].some((path) => restartPath === path || restartPath.startsWith(`${path}/`))
                  : restartPath === href || restartPath.startsWith(`${href}/`);
              return (
                <Link
                  aria-current={active ? "page" : undefined}
                  className={`nav-link${active ? " active" : ""}`}
                  href={href}
                  key={href}
                  onClick={(event) => {
                    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                    onNavigate?.(href);
                  }}
                >
                  <Icon size={17} strokeWidth={1.8} />
                  <span>{label}</span>
                </Link>
              );
            })}
          </section>
        ))}

        <div className="sidebar-rule" style={{ margin: "6px 4px 4px" }} />

        <div className="nav-group">
          <Link
            aria-current={restartPath.startsWith("/settings") ? "page" : undefined}
            className={`nav-link${restartPath.startsWith("/settings") ? " active" : ""}`}
            href="/settings"
            onClick={(event) => {
              if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
              onNavigate?.("/settings");
            }}
          >
            <Settings2 size={17} strokeWidth={1.8} />
            <span>{t("nav.settings")}</span>
          </Link>
        </div>
      </nav>
      <div className="sidebar-bottom">
        <div className="sidebar-footnote"><Clock3 size={14} /> {t("nav.footerBrand")}</div>
      </div>
    </aside>
  );
}
