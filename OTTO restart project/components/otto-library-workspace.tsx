"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Clock3, Info, RefreshCw, Search } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { useRestartAccount } from "@/features/state/restart-account";
import { catalogDemoService } from "@/features/catalogs/service";
import type { CatalogEanLinkRecord } from "@/features/catalogs/types";
import { DemoWorkspaceClient } from "@/features/demo/client";
import { DemoReviewClient } from "@/features/review/client";
import { REVIEW_STATUS_LABELS } from "@/features/review/types";
import type { DemoEanEntry, DemoEvent } from "@/features/demo/types";
import type { ReviewItem } from "@/features/review/types";

const workspace = new DemoWorkspaceClient();
const review = new DemoReviewClient();
type LibraryTab = "links" | "pool" | "history";
const tabs: { id: LibraryTab; label: string }[] = [
  { id: "links", label: "EAN-связи" },
  { id: "pool", label: "Пул идентификаторов" },
  { id: "history", label: "История операций" }
];
const poolStatus = {
  AVAILABLE: "Свободен",
  RESERVED: "Резерв",
  USED: "Использован",
  QUARANTINED: "Карантин"
} as const;

export function OttoLibraryWorkspace() {
  const { account, ready } = useRestartAccount();
  const [tab, setTab] = useState<LibraryTab>("links");
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [catalogLinks, setCatalogLinks] = useState<CatalogEanLinkRecord[]>([]);
  const [eans, setEans] = useState<DemoEanEntry[]>([]);
  const [events, setEvents] = useState<DemoEvent[]>([]);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  const load = useCallback(async (active: () => boolean) => {
    setLoading(true);
    setError("");
    try {
      // 1. Primary: Fetch real persistent PostgreSQL data from backend proxy
      const linksRes = await fetch(
        `/restart/api/backend/library/links?account=${account}&q=${encodeURIComponent(query)}`
      );
      if (!linksRes.ok) {
        throw new Error(`HTTP ${linksRes.status}`);
      }
      const linksData = await linksRes.json();
      if (!active()) return;

      const realLinks: CatalogEanLinkRecord[] = (linksData.items || []).map(
        (item: {
          id: string;
          sourceProductId: string;
          account: "JV" | "XL";
          factoryId?: string;
          factoryName?: string;
          title: string;
          sourceEan: string;
          newEan: string;
          createdAt: string;
        }) => ({
          id: item.id,
          productId: item.sourceProductId,
          account: item.account,
          factoryId: item.factoryId,
          factoryName: item.factoryName,
          title: item.title,
          sourceEan: item.sourceEan,
          newEan: item.newEan,
          period: "3M",
          assignedAt: item.createdAt,
        })
      );

      // Merge with any in-memory catalog links not yet synced
      const localLinks = catalogDemoService.getEanLinks(account, query);
      const existingNewEans = new Set(realLinks.map((l) => l.newEan));
      for (const loc of localLinks) {
        if (!existingNewEans.has(loc.newEan)) {
          realLinks.push(loc);
        }
      }

      const poolRes = await fetch(
        `/restart/api/backend/library/ean-pool?account=${account}&q=${encodeURIComponent(query)}`
      );
      const poolData = poolRes.ok ? await poolRes.json() : { items: [] };
      if (!active()) return;

      const eventsRes = await fetch(
        `/restart/api/backend/events?account=${account}&pageSize=50`
      );
      const eventsData = eventsRes.ok ? await eventsRes.json() : { items: [] };
      if (!active()) return;

      setCatalogLinks(realLinks);
      setEans(
        (poolData.items || []).map(
          (e: {
            ean: string;
            account: "JV" | "XL";
            status: "AVAILABLE" | "RESERVED" | "USED" | "QUARANTINED";
            batchId?: string;
            createdAt: string;
          }) => ({
            ean: e.ean,
            account: e.account,
            status: e.status,
            batchId: e.batchId,
            importedAt: e.createdAt,
          })
        )
      );
      setEvents(
        (eventsData.items || []).map(
          (ev: {
            id: string;
            account: "JV" | "XL";
            eventType: string;
            entityType: string;
            entityId: string;
            summary: string;
            createdAt: string;
          }) => ({
            id: ev.id,
            account: ev.account,
            eventType: ev.eventType,
            entityType: ev.entityType,
            entityId: ev.entityId,
            summary: ev.summary,
            createdAt: ev.createdAt,
            at: ev.createdAt,
          })
        )
      );

      // Also load candidates
      const candidates = await review.list(account, query, "", 1);
      if (active()) {
        setItems(candidates.items);
      }
    } catch (cause) {
      console.warn(
        "[otto-library] Backend fetch failed, falling back to local demo service:",
        cause
      );
      try {
        const pool = await workspace.eans(account, "", query);
        if (!active()) return;
        const candidates = await review.list(account, query, "", 1);
        if (!active()) return;
        const audit = await workspace.events(account, query);
        if (active()) {
          setEans(pool.items);
          setItems(candidates.items);
          setCatalogLinks(catalogDemoService.getEanLinks(account, query));
          setEvents(audit.items);
        }
      } catch (innerErr) {
        if (active())
          setError(
            innerErr instanceof Error
              ? innerErr.message
              : "Не удалось загрузить OTTO Library."
          );
      }
    } finally {
      if (active()) setLoading(false);
    }
  }, [account, query]);

  useEffect(() => {
    if (!ready) return;
    let active = true;
    void load(() => active);
    return () => {
      active = false;
    };
  }, [ready, load, reload]);

  const linked = catalogLinks.length + items.filter((item) => item.identity !== null).length;
  const totalLinksCount = catalogLinks.length + items.length;

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setQuery(search.trim());
  }

  return (
    <section className="page-section library-page">
      <div className="page-heading">
        <div>
          <h1>OTTO Library</h1>
          <p>Реестр EAN-связей, пул идентификаторов и журнал операций аккаунта {account}.</p>
        </div>
      </div>

      <div className="library-summary-grid">
        <article className="library-summary-card">
          <span>EAN-связи</span>
          <strong className="tabular-nums">{linked}</strong>
          <small>связано в карточках</small>
        </article>
        <article className="library-summary-card">
          <span>Пул идентификаторов</span>
          <strong className="tabular-nums">{eans.length}</strong>
          <small>доступно в сессии</small>
        </article>
        <article className="library-summary-card">
          <span>События</span>
          <strong className="tabular-nums">{events.length}</strong>
          <small>записей в журнале</small>
        </article>
      </div>

      <section className="surface-card library-card">
        <div className="library-toolbar">
          <div>
            <div className="card-heading">
              <div>
                <h2>Связи и история идентификаторов</h2>
                <p>Данные изолированы по аккаунту {account}.</p>
              </div>
            </div>
          </div>
          <div className="library-toolbar-actions">
            <form className="item-search" onSubmit={submitSearch}>
              <input
                aria-label="Поиск EAN или карточки"
                placeholder="Поиск EAN, товара или ID"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
              <button className="demo-button" type="submit">
                <Search size={14} /> Найти
              </button>
            </form>
            <button
              type="button"
              className="icon-button"
              aria-label="Обновить OTTO Library"
              disabled={loading}
              onClick={() => setReload((value) => value + 1)}
            >
              <RefreshCw size={15} className={loading ? "spin" : ""} />
            </button>
          </div>
        </div>

        <div className="library-tabs" role="tablist" aria-label="Содержимое OTTO Library">
          {tabs.map((value) => {
            const count = value.id === "links" ? totalLinksCount : value.id === "pool" ? eans.length : events.length;
            return (
              <button
                type="button"
                role="tab"
                aria-selected={tab === value.id}
                key={value.id}
                onClick={() => setTab(value.id)}
              >
                {value.label}
                <span className="tabular-nums">{count}</span>
              </button>
            );
          })}
        </div>

        {loading ? <ApiState kind="loading" label="Загружаем данные библиотеки…" /> : null}
        {error ? <ApiState kind="error" description={error} onRetry={() => setReload((value) => value + 1)} /> : null}

        {!loading && !error && tab === "links" ? (
          <>
            <p className="library-lineage-note">
              <Info size={14} />
              Ниже показана связь исходного идентификатора карточки с назначенным EAN для перезапуска.
            </p>
            {totalLinksCount ? (
              <div className="restart-table-wrap library-table-wrap">
                <table className="restart-table library-table">
                  <thead>
                    <tr>
                      <th>Товар / партия</th>
                      <th>Источник Aftercool</th>
                      <th>Исходный EAN</th>
                      <th>Новый OTTO EAN</th>
                      <th>Состояние</th>
                      <th>Последнее событие</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {catalogLinks.map((link) => (
                      <tr key={link.id}>
                        <td>
                          <strong>{link.title}</strong>
                          <small className="demo-subline">
                            {link.factoryName ? `Фабрика ${link.factoryName}` : "Напрямую из OTTO"} ({link.period})
                          </small>
                        </td>
                        <td>
                          <span className="library-unavailable">{link.factoryId ? `Aftercool #${link.factoryId}` : "OTTO Catalog"}</span>
                        </td>
                        <td className="mono-value tabular-nums">{link.sourceEan}</td>
                        <td className="mono-value tabular-nums">{link.newEan}</td>
                        <td>
                          <span className="review-status review-status-ready">Назначен новый EAN</span>
                        </td>
                        <td className="tabular-nums">
                          {new Intl.DateTimeFormat("ru-RU", {
                            dateStyle: "short",
                            timeStyle: "short",
                            timeZone: "UTC",
                          }).format(new Date(link.assignedAt))}
                        </td>
                        <td>
                          <Link
                            className="text-link"
                            href={link.factoryId ? `/afterbuy/${link.factoryId}` : "/direct-otto"}
                            aria-label={`Открыть каталог для ${link.title}`}
                          >
                            <ArrowRight size={15} />
                          </Link>
                        </td>
                      </tr>
                    ))}
                    {items.map((item) => {
                      const lastEvent = [...item.events].sort((a, b) => b.at.localeCompare(a.at))[0];
                      return (
                        <tr key={item.id}>
                          <td>
                            <strong>{item.prepared?.title ?? item.old.title}</strong>
                            <small className="demo-subline">
                              Партия {item.batchId} ({item.id})
                            </small>
                          </td>
                          <td>
                            <span className="library-unavailable">Локальный кэш</span>
                          </td>
                          <td className="mono-value tabular-nums">{item.old.ean}</td>
                          <td className="mono-value tabular-nums">{item.identity?.newEan ?? "Не назначен"}</td>
                          <td>
                            <span className={`review-status review-status-${item.status.toLowerCase()}`}>
                              {REVIEW_STATUS_LABELS[item.status]}
                            </span>
                          </td>
                          <td className="tabular-nums">
                            {lastEvent
                              ? new Intl.DateTimeFormat("ru-RU", {
                                  dateStyle: "short",
                                  timeStyle: "short",
                                  timeZone: "UTC"
                                }).format(new Date(lastEvent.at))
                              : "—"}
                          </td>
                          <td>
                            <Link
                              className="text-link"
                              href="/afterbuy"
                              aria-label={`Открыть каталог для ${item.old.title}`}
                            >
                              <ArrowRight size={15} />
                            </Link>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <ApiState
                kind="empty"
                title="Связей по поиску нет"
                description="Подготовьте товары в разделе «Товары Afterbuy» или «Товары напрямую из OTTO»."
              />
            )}
          </>
        ) : null}

        {!loading && !error && tab === "pool" ? (
          eans.length ? (
            <div className="restart-table-wrap library-table-wrap">
              <table className="restart-table library-table">
                <thead>
                  <tr>
                    <th>Идентификатор EAN</th>
                    <th>Статус</th>
                    <th>Партия</th>
                    <th>Добавлен в сессию</th>
                  </tr>
                </thead>
                <tbody>
                  {eans.map((entry) => (
                    <tr key={entry.ean}>
                      <td className="mono-value tabular-nums">{entry.ean}</td>
                      <td>
                        <span className="status-pill">{poolStatus[entry.status]}</span>
                      </td>
                      <td>{entry.batchId ?? "—"}</td>
                      <td className="tabular-nums">
                        {new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeZone: "UTC" }).format(
                          new Date(entry.importedAt)
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <ApiState
              kind="empty"
              title="В пуле нет записей по этому запросу"
              description="Пул хранит свободные и зарезервированные идентификаторы текущей сессии."
            />
          )
        ) : null}

        {!loading && !error && tab === "history" ? (
          events.length ? (
            <div className="library-event-list">
              {events.map((event) => (
                <article key={event.id}>
                  <span className="library-event-icon">
                    <Clock3 size={14} />
                  </span>
                  <div>
                    <strong>{event.summary}</strong>
                    <small>
                      {event.eventType} ({event.entityId})
                    </small>
                  </div>
                  <time className="tabular-nums">
                    {new Intl.DateTimeFormat("ru-RU", {
                      dateStyle: "short",
                      timeStyle: "short",
                      timeZone: "UTC"
                    }).format(new Date(event.createdAt))}
                  </time>
                </article>
              ))}
            </div>
          ) : (
            <ApiState
              kind="empty"
              title="История пока пуста"
              description="События появятся после выполнения операций в каталоге."
            />
          )
        ) : null}
      </section>
    </section>
  );
}
