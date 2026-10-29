"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BookOpenText,
  Clock3,
  Factory,
  House,
  Send,
  Settings2,
  type LucideIcon,
} from "lucide-react";
import { ProductSwitcher } from "@/components/product-switcher";

type NavigationItem = { href: string; label: string; icon: LucideIcon };
type NavigationGroup = { label: string; items: NavigationItem[] };

const groups: NavigationGroup[] = [
  { label: "Обзор", items: [{ href: "/", label: "Добро пожаловать", icon: House }] },
  {
    label: "Каталоги 3/12M",
    items: [
      { href: "/afterbuy", label: "Товары Afterbuy", icon: Factory },
      { href: "/direct-otto", label: "Товары напрямую из ОТТО", icon: Send },
    ],
  },
  { label: "Данные", items: [{ href: "/library", label: "OTTO Library", icon: BookOpenText }] },
];

export function RestartSidebar({ desktopHidden = false, mobileOpen = false, onNavigate }: { desktopHidden?: boolean; mobileOpen?: boolean; onNavigate?: () => void }) {
  const pathname = usePathname();
  const restartPath = pathname?.startsWith("/restart")
    ? pathname.slice("/restart".length) || "/"
    : pathname || "/";

  return (
    <aside id="restart-navigation" className={`restart-sidebar${desktopHidden ? " desktop-hidden" : ""}${mobileOpen ? " mobile-open" : ""}`}>
      <ProductSwitcher />
      <div className="sidebar-rule" />
      <nav aria-label="Навигация OttoRestart" className="restart-nav">
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
                  onClick={onNavigate}
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
            onClick={onNavigate}
          >
            <Settings2 size={17} strokeWidth={1.8} />
            <span>Настройки</span>
          </Link>
        </div>
      </nav>
      <div className="sidebar-bottom">
        <div className="sidebar-footnote"><Clock3 size={14} /> Restart 3/12M</div>
      </div>
    </aside>
  );
}
