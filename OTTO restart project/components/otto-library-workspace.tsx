"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Clock, Clock3, Database, Info, RefreshCw, Search } from "lucide-react";
import { ApiState } from "@/components/api-state";
import { RuntimeModeGate } from "@/components/runtime-mode-gate";
import { useRestartAccount } from "@/features/state/restart-account";
import { getOttoRestartMode, isOttoRestartDemoMode } from "@/features/runtime-mode";
import { ProductionLibraryView } from "@/components/catalogs/production-library-view";
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
  const isDemo = isOttoRestartDemoMode("library");
  const mode = getOttoRestartMode();
  const [tab, setTab] = useState<LibraryTab>("links");
  const [items, setItems] = useState<ReviewItem[]>([]);
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
    if (!isDemo) {
      setItems([]);
      setEans([]);
      setEvents([]);
      setLoading(false);
      return;
    }

    try {
      const [pool, candidates, audit] = await Promise.all([
        workspace.eans(account, "", query),
        review.list(account, query, "", 1),
        workspace.events(account, query),
      ]);
      if (!active()) return;
      setEans(pool.items);
      setItems(candidates.items);
      setEvents(audit.items);
    } catch (cause) {
      if (active()) setError(cause instanceof Error ? cause.message : "Не удалось загрузить demo OTTO Library.");
    } finally {
      if (active()) setLoading(false);
    }
  }, [account, query, isDemo]);

  useEffect(() => {
    if (!ready) return;
    let active = true;
    void load(() => active);
    return () => {
      active = false;
    };
  }, [ready, load, reload]);

  const linkedItems = items.filter((item) => item.identity !== null);
  const linked = linkedItems.length;
  const totalLinksCount = linkedItems.length;

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setQuery(search.trim());
  }

  if (mode === "PRODUCTION" || mode === "TEST") {
    return <ProductionLibraryView />;
  }

  if (!isDemo) {
    return <RuntimeModeGate title="OTTO Library недоступна" demoEnabled={false} />;
  }

  return (
    <RuntimeModeGate title="OTTO Library недоступна" demoEnabled={isDemo} showDemoBanner={false}>
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
                    {linkedItems.map((item) => {
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
            <div className="library-history-container">
              {events.map((event) => {
                const d = new Date(event.createdAt);
                const dateStr = Number.isNaN(d.getTime()) ? "—" : new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "UTC" }).format(d);
                const timeStr = Number.isNaN(d.getTime()) ? "—" : new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "UTC" }).format(d);
                return (
                  <div key={event.id} className="library-history-item">
                    <div className="library-history-left">
                      <div className="library-history-icon-box">
                        <Clock size={16} />
                      </div>
                      <div className="library-history-details">
                        <div className="library-history-headline">
                          <span className="library-history-title">{event.summary}</span>
                          <span className="library-history-type-pill">{event.eventType}</span>
                        </div>
                        <div className="library-history-meta">
                          <span>{event.entityType}</span>
                          {event.entityId && !event.entityId.includes("T") ? <span>· ID: {event.entityId}</span> : null}
                        </div>
                      </div>
                    </div>
                    <div className="library-history-time-col">
                      <div className="library-history-time-badge" title={`${dateStr}, ${timeStr} UTC`}>
                        <Clock size={13} className="library-history-clock-icon" />
                        <span className="library-history-date">{dateStr}</span>
                        <span className="library-history-dot">·</span>
                        <span className="library-history-clock">{timeStr}</span>
                        <span className="library-history-tz">UTC</span>
                      </div>
                    </div>
                  </div>
                );
              })}
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
    </RuntimeModeGate>
  );
}
