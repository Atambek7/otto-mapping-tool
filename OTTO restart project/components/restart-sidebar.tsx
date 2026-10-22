"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  BookOpenText,
  Clock3,
  ClipboardCheck,
  Layers3,
  LayoutDashboard,
  PackageSearch,
  Settings2,
  type LucideIcon,
} from "lucide-react";
import { ProductSwitcher } from "@/components/product-switcher";

type NavigationItem = { href: string; label: string; icon: LucideIcon };
type NavigationGroup = { label: string; items: NavigationItem[] };

const groups: NavigationGroup[] = [
  { label: "Обзор", items: [{ href: "/", label: "Панель управления", icon: LayoutDashboard }] },
  {
    label: "Рабочий процесс",
    items: [
      { href: "/review", label: "Кандидаты 3/12M", icon: PackageSearch },
      { href: "/batches", label: "Партии Restart", icon: Layers3 },
      { href: "/quality-control", label: "Quality Control", icon: ClipboardCheck },
    ],
  },
  { label: "Данные", items: [{ href: "/library", label: "OTTO Library", icon: BookOpenText }] },
  {
    label: "Публикация",
    items: [{ href: "/queue", label: "Очередь и планировщик", icon: Activity }],
  },
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
      </nav>
      <div className="sidebar-bottom">
        <Link
          aria-current={restartPath.startsWith("/settings") ? "page" : undefined}
          className={`nav-link${restartPath.startsWith("/settings") ? " active" : ""}`}
          href="/settings"
          onClick={onNavigate}
        >
          <Settings2 size={17} strokeWidth={1.8} />
          <span>Настройки</span>
        </Link>
        <div className="sidebar-footnote"><Clock3 size={14} /> Restart 3/12M</div>
      </div>
    </aside>
  );
}
