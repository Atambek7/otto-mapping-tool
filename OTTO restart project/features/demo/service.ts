import { randomUUID } from "node:crypto";
import type { RestartAccount, RestartPeriod } from "../api/types";
import { DemoReviewService, ReviewProblem } from "../review/demo-service";
import type { ReviewItem, ReviewStatus } from "../review/types";
import type { DemoBatch, DemoBatchDetails, DemoBatchItem, DemoBatchPreview, DemoEanEntry, DemoEvent, DemoOverview, DemoPage, DemoSettings } from "./types";

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const ACCOUNTS = ["JV", "XL"] as const;

/** Session-only synthetic data for the OttoRestart prototype. No disk or external API access. */
export class DemoWorkspaceService {
  private batches = new Map<string, DemoBatchDetails>();
  private pool = new Map<RestartAccount, DemoEanEntry[]>(ACCOUNTS.map((account) => [account, []]));
  private settings = new Map<RestartAccount, DemoSettings>();
  private events: DemoEvent[] = [];
  private nextPoolNumber = new Map<RestartAccount, number>(ACCOUNTS.map((account) => [account, 1]));

  constructor(private readonly clock: () => Date = () => new Date(), private readonly createId: () => string = randomUUID) {
    for (const account of ACCOUNTS) {
      const now = this.now();
      this.settings.set(account, this.defaultSettings(account));
      this.batches.set(`demo-${account.toLowerCase()}`, {
        id: `demo-${account.toLowerCase()}`, account, period: "12M", name: `Демо-партия ${account}`,
        status: "DEMO_REVIEW", itemCount: 5, validatedCount: 0, errorCount: 3, approvedCount: 0,
        createdAt: now, fileName: `demo-restart-${account.toLowerCase()}.csv`, demoOnly: true,
        items: [],
      });
      this.event(account, "DEMO_FIXTURE_LOADED", "batch", `demo-${account.toLowerCase()}`, "Загружен синтетический демонстрационный набор; реальные товары не используются.");
    }
  }

  private now() { return this.clock().toISOString(); }
  private defaultSettings(account: RestartAccount): DemoSettings {
    return { account, defaultPeriod: "12M", maximumDemoBatchItems: 100, strictAttributeChecks: true,
      restartPublishEnabled: false, priceRule: { source: "DEMO STANDARD PRICE", JV: "−10.00 EUR", XL: "−15.00 EUR" }, updatedAt: this.now(), demoOnly: true };
  }
  private event(account: RestartAccount, eventType: string, entityType: string, entityId: string, summary: string) {
    this.events.unshift({ id: this.createId(), account, eventType, entityType, entityId, summary, createdAt: this.now(), demoOnly: true });
    if (this.events.length > 500) this.events.length = 500;
  }
  private batchItems(account: RestartAccount, batchId: string, review: DemoReviewService): DemoBatchItem[] {
    const source = review.allItems(account).filter((item) => item.batchId === batchId);
    return source.map((item, index) => ({
      id: item.id, rowNumber: index + 1, sourceEan: item.old.ean, title: item.prepared?.title ?? item.old.title,
      status: item.status, newEan: item.identity?.newEan ?? null, standardPriceCents: item.price?.baseCents ?? null,
      newStandardPriceCents: item.price?.newCents ?? null, issue: item.issues[0]?.message ?? this.scenarioIssue(item),
    }));
  }
  private scenarioIssue(item: ReviewItem): string | null {
    const scenario: Record<string, string> = {
      "Цена отсутствует": "В синтетическом источнике нет standardPrice.amount.",
      "Не указан материал": "В исходном fixture не заполнен материал.",
      "Ограничение Marketplace": "Сценарий демонстрирует ограничение Marketplace.",
      "Цена ниже вычета": "Синтетическая цена ниже суммы скидки аккаунта.",
    };
    return scenario[item.scenario] ?? null;
  }
  private seededBatch(account: RestartAccount, review: DemoReviewService): DemoBatchDetails {
    const id = `demo-${account.toLowerCase()}`;
    const items = this.batchItems(account, id, review);
    const approvedCount = items.filter((item) => item.status === "APPROVED").length;
    const validatedCount = items.filter((item) => ["READY_FOR_APPROVAL", "APPROVED"].includes(item.status)).length;
    const errorCount = items.filter((item) => !!item.issue).length;
    const current = this.batches.get(id)!;
    return { ...current, itemCount: items.length, approvedCount, validatedCount, errorCount, status: "DEMO_REVIEW", items };
  }
  private allBatches(account: RestartAccount, review: DemoReviewService): DemoBatchDetails[] {
    return [this.seededBatch(account, review), ...[...this.batches.values()].filter((batch) => batch.account === account && batch.id !== "demo-jv" && batch.id !== "demo-xl").map(clone)];
  }

  listBatches(account: RestartAccount, review: DemoReviewService, query = "", status = "", page = 1, pageSize = 20, period = ""): DemoPage<DemoBatch> {
    if (!Number.isInteger(page) || page < 1 || !Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100) throw new ReviewProblem(422, "VALIDATION_ERROR", "Некорректная пагинация.");
    const q = query.toLocaleLowerCase();
    const matches = this.allBatches(account, review).filter((batch) => (!status || batch.status === status) && (!period || batch.period === period) && [batch.id, batch.name, batch.fileName, batch.period].some((value) => value.toLocaleLowerCase().includes(q))).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    return { mode: "DEMO", items: clone(matches.slice((page - 1) * pageSize, page * pageSize).map(({ items: _items, ...batch }) => batch)), page: { page, pageSize, totalItems: matches.length, totalPages: Math.ceil(matches.length / pageSize) } };
  }
  getBatch(account: RestartAccount, id: string, review: DemoReviewService): DemoBatchDetails {
    const batch = id === `demo-${account.toLowerCase()}` ? this.seededBatch(account, review) : this.batches.get(id);
    if (!batch || batch.account !== account) throw new ReviewProblem(404, "NOT_FOUND", "Демо-партия не найдена в выбранном аккаунте.");
    return clone(batch);
  }
  getBatchSummary(account: RestartAccount, id: string, review: DemoReviewService): DemoBatch {
    const { items: _items, ...summary } = this.getBatch(account, id, review);
    return summary;
  }
  listBatchItems(account: RestartAccount, id: string, review: DemoReviewService, query = "", page = 1, pageSize = 50): DemoPage<DemoBatchItem> {
    if (!Number.isInteger(page) || page < 1 || !Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100) throw new ReviewProblem(422, "VALIDATION_ERROR", "Некорректная пагинация.");
    const search = query.toLocaleLowerCase();
    const matches = this.getBatch(account, id, review).items.filter((item) => [item.sourceEan, item.title, item.id].some((value) => value.toLocaleLowerCase().includes(search)));
    return { mode: "DEMO", items: clone(matches.slice((page - 1) * pageSize, page * pageSize)), page: { page, pageSize, totalItems: matches.length, totalPages: Math.ceil(matches.length / pageSize) } };
  }
  previewBatch(account: RestartAccount, input: { period: RestartPeriod; name: string; itemCount?: number }): DemoBatchPreview {
    if (!input || !["3M", "12M"].includes(input.period) || typeof input.name !== "string" || !input.name.trim() || input.name.length > 120) throw new ReviewProblem(422, "VALIDATION_ERROR", "Укажите имя демо-партии и период 3M или 12M.");
    const maximum = this.settings.get(account)!.maximumDemoBatchItems;
    const inputCount = input.itemCount ?? 3;
    if (!Number.isInteger(inputCount) || inputCount < 1 || inputCount > Math.min(maximum, 1000)) throw new ReviewProblem(422, "VALIDATION_ERROR", `Размер демо-партии должен быть от 1 до ${Math.min(maximum, 1000)} строк.`);
    return { mode: "DEMO", account, period: input.period, name: input.name.trim(), inputCount, uniqueCount: inputCount, duplicateCount: 0, invalidCount: 0,
      sampleIds: Array.from({ length: Math.min(inputCount, 3) }, (_, index) => `DEMO-SOURCE-${account}-${String(index + 1).padStart(2, "0")}`),
      warnings: ["Создаются только синтетические строки; данные товаров и цены недоступны.", "Проверки статусов OTTO, Aftercool и GTIN не выполнялись."], writesPerformed: false };
  }
  createBatch(account: RestartAccount, input: { period: RestartPeriod; name: string; itemCount?: number }, review: DemoReviewService): DemoBatchDetails {
    if (!input || !["3M", "12M"].includes(input.period) || typeof input.name !== "string" || !input.name.trim() || input.name.length > 120) throw new ReviewProblem(422, "VALIDATION_ERROR", "Укажите имя демо-партии и период 3M или 12M.");
    const maximum = this.settings.get(account)!.maximumDemoBatchItems;
    const itemCount = input.itemCount ?? 3;
    if (!Number.isInteger(itemCount) || itemCount < 1 || itemCount > Math.min(maximum, 1000)) throw new ReviewProblem(422, "VALIDATION_ERROR", `Размер демо-партии должен быть от 1 до ${Math.min(maximum, 1000)} строк.`);
    const suffix = this.createId().replace(/-/g, "").slice(0, 8);
    const id = `demo-new-${account.toLowerCase()}-${suffix}`;
    const createdAt = this.now();
    const items: DemoBatchItem[] = Array.from({ length: itemCount }, (_, index) => ({
      id: `${id}-item-${index + 1}`, rowNumber: index + 1, sourceEan: `DEMO-SOURCE-${account}-${suffix}-${String(index + 1).padStart(2, "0")}`,
      title: `Тестовый кандидат ${index + 1} · ${account}`, status: "IMPORTED", newEan: null,
      standardPriceCents: null, newStandardPriceCents: null, issue: "Синтетическая строка; исходный товар не подключён.",
    }));
    const batch: DemoBatchDetails = { id, account, period: input.period, name: input.name.trim(), status: "DEMO_IMPORTED", itemCount, validatedCount: 0, errorCount: itemCount, approvedCount: 0, createdAt, fileName: "generated-demo-fixture.csv", demoOnly: true, items };
    this.batches.set(id, batch);
    this.event(account, "DEMO_BATCH_CREATED", "batch", id, `Создана синтетическая партия «${batch.name}» на ${itemCount} строк.`);
    return clone(batch);
  }

  listEans(account: RestartAccount, review: DemoReviewService, status = "", query = ""): DemoPage<DemoEanEntry> {
    const q = query.toLocaleLowerCase();
    const reviewPool: DemoEanEntry[] = review.demoPoolEntries(account).map((item) => ({ ...item, account, demoOnly: true }));
    const matches = [...reviewPool, ...this.pool.get(account)!].filter((item) => (!status || item.status === status) && item.ean.toLocaleLowerCase().includes(q));
    return { mode: "DEMO", items: clone(matches.slice(0, 100)), page: { page: 1, pageSize: 100, totalItems: matches.length, totalPages: 1 } };
  }
  addDemoEans(account: RestartAccount, count = 4) {
    if (!Number.isInteger(count) || count < 1 || count > 20) throw new ReviewProblem(422, "VALIDATION_ERROR", "Можно добавить от 1 до 20 условных DEMO-идентификаторов.");
    const added: DemoEanEntry[] = [];
    for (let index = 0; index < count; index += 1) {
      const number = this.nextPoolNumber.get(account)!;
      this.nextPoolNumber.set(account, number + 1);
      const entry: DemoEanEntry = { ean: `DEMO-POOL-${account}-X${number}`, account, status: "AVAILABLE", batchId: null, importedAt: this.now(), demoOnly: true };
      this.pool.get(account)!.push(entry); added.push(entry);
    }
    this.event(account, "DEMO_EAN_POOL_ADDED", "ean-pool", account, `В демонстрационный пул добавлено ${added.length} условных идентификаторов.`);
    return { mode: "DEMO" as const, account, importedCount: added.length, entries: clone(added) };
  }

  listEvents(account: RestartAccount, review: DemoReviewService, query = "", queue?: { snapshot(account: RestartAccount): { tasks: { id: string; status: string; title: string; createdAt: string; completedAt: string | null; result: string | null }[] } }): DemoPage<DemoEvent> {
    const q = query.toLocaleLowerCase();
    const reviewEvents: DemoEvent[] = review.allItems(account).flatMap((item) => item.events.map((event) => ({ id: `${item.id}-${event.version}`, account, eventType: event.action, entityType: "item", entityId: item.id, summary: `${item.old.ean} · ${event.description}`, createdAt: event.at, demoOnly: true as const })));
    const queueEvents: DemoEvent[] = (queue?.snapshot(account).tasks ?? []).map((task) => ({ id: `${task.id}-${task.status}`, account, eventType: `DEMO_QUEUE_${task.status}`, entityType: "queue-task", entityId: task.id, summary: task.result ?? `Демо-задача создана для ${task.title}.`, createdAt: task.completedAt ?? task.createdAt, demoOnly: true }));
    const matches = [...reviewEvents, ...queueEvents, ...this.events.filter((event) => event.account === account)].filter((event) => [event.eventType, event.entityId, event.summary].some((value) => value.toLocaleLowerCase().includes(q))).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    return { mode: "DEMO", items: clone(matches.slice(0, 100)), page: { page: 1, pageSize: 100, totalItems: matches.length, totalPages: 1 } };
  }
  getSettings(account: RestartAccount) { return clone(this.settings.get(account)!); }
  patchSettings(account: RestartAccount, patch: { defaultPeriod?: RestartPeriod; maximumDemoBatchItems?: number; strictAttributeChecks?: boolean }) {
    if (!patch || typeof patch !== "object" || Object.keys(patch).some((key) => !["defaultPeriod", "maximumDemoBatchItems", "strictAttributeChecks"].includes(key))) throw new ReviewProblem(422, "VALIDATION_ERROR", "Разрешены только настройки демонстрационного режима.");
    const current = this.settings.get(account)!;
    const next = { ...current };
    if (patch.defaultPeriod !== undefined) {
      if (patch.defaultPeriod !== "3M" && patch.defaultPeriod !== "12M") throw new ReviewProblem(422, "VALIDATION_ERROR", "Период должен быть 3M или 12M.");
      next.defaultPeriod = patch.defaultPeriod;
    }
    if (patch.maximumDemoBatchItems !== undefined) {
      if (!Number.isInteger(patch.maximumDemoBatchItems) || patch.maximumDemoBatchItems < 1 || patch.maximumDemoBatchItems > 1000) throw new ReviewProblem(422, "VALIDATION_ERROR", "Размер партии должен быть от 1 до 1000 строк.");
      next.maximumDemoBatchItems = patch.maximumDemoBatchItems;
    }
    if (patch.strictAttributeChecks !== undefined) {
      if (typeof patch.strictAttributeChecks !== "boolean") throw new ReviewProblem(422, "VALIDATION_ERROR", "Флаг проверки атрибутов должен быть логическим.");
      next.strictAttributeChecks = patch.strictAttributeChecks;
    }
    next.updatedAt = this.now(); this.settings.set(account, next);
    this.event(account, "DEMO_SETTINGS_UPDATED", "settings", account, "Изменены локальные настройки демонстрационного режима.");
    return clone(next);
  }

  overview(account: RestartAccount, review: DemoReviewService, queue: { snapshot(account: RestartAccount): { tasks: { id: string; status: string; title: string; createdAt: string; completedAt: string | null; result: string | null }[] } }): DemoOverview {
    const items = review.allItems(account);
    const batches = this.allBatches(account, review).map(({ items: _items, ...batch }) => batch);
    const eans = this.pool.get(account)!;
    const activity = this.listEvents(account, review, "", queue).items.slice(0, 6);
    const attentionItems = items.filter((item) => item.issues.length || this.scenarioIssue(item) || ["PRICE_UNAVAILABLE", "PRICE_REVIEW_REQUIRED", "BLOCKED_BY_OTTO", "VALIDATION_FAILED", "PRICE_CHANGED_REAPPROVAL_REQUIRED"].includes(item.status));
    const workflowItems = items.filter((item) => !attentionItems.includes(item));
    const preparedCount = workflowItems.filter((item) => item.status === "CONTENT_PREPARED").length;
    const readyCount = workflowItems.filter((item) => item.status === "READY_FOR_APPROVAL").length;
    const approvedCount = workflowItems.filter((item) => item.status === "APPROVED").length;
    const candidateCount = Math.max(0, workflowItems.length - preparedCount - readyCount - approvedCount);
    const statusBreakdown = [
      { key: "candidate", label: "Кандидаты", count: candidateCount },
      { key: "prepared", label: "Подготовлены", count: preparedCount },
      { key: "ready", label: "Готовы к утверждению", count: readyCount },
      { key: "approved", label: "Утверждены в демо", count: approvedCount },
      { key: "attention", label: "Требуют внимания", count: attentionItems.length },
    ];
    const attention = attentionItems.slice(0, 6).map((item) => ({ id: item.id, title: item.prepared?.title ?? item.old.title, reason: item.issues[0]?.message ?? this.scenarioIssue(item) ?? "Требуется ручная проверка.", status: item.status }));
    return { account, mode: "DEMO", generatedAt: this.now(), counts: {
      candidates: items.length + batches.filter((batch) => !batch.id.startsWith("demo-")).reduce((sum, batch) => sum + batch.itemCount, 0),
      awaitingReview: items.filter((item) => item.status === "READY_FOR_APPROVAL").length,
      needsAttention: attentionItems.length, queued: queue.snapshot(account).tasks.filter((task) => task.status === "QUEUED").length,
      onlineConfirmed: 0, availableEans: eans.filter((entry) => entry.status === "AVAILABLE").length + review.demoPoolCounts(account).available,
      reservedEans: eans.filter((entry) => entry.status === "RESERVED").length + review.demoPoolCounts(account).reserved,
    }, batches, statusBreakdown, attention, activity };
  }
}
