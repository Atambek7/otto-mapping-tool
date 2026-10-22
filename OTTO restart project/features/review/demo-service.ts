import { createHash } from "node:crypto";
import type { RestartAccount } from "../api/types";
import { DEMO_CATEGORIES, DEMO_TITLE_LIMIT } from "./types";
import type { PriceSnapshot, ReviewAction, ReviewContent, ReviewIssue, ReviewItem, ReviewList, ReviewPatch } from "./types";

export class ReviewProblem extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}
type DemoPrice = { amount: number | null; currency: string; revision: number; account: RestartAccount; sku: string };
type ApprovedPayload = { identity: NonNullable<ReviewItem["identity"]>; content: ReviewContent; price: PriceSnapshot; demoOnly: true };
export type DemoApprovedSnapshot = { item: ReviewItem; payload: ApprovedPayload };
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const ALLOWED_ATTRIBUTES = ["material", "color", "width", "height"];

/** Only fixture adapters. No database, HTTP client, credentials, or production payloads. */
export class DemoReviewService {
  private items = new Map<string, ReviewItem>();
  private prices = new Map<string, DemoPrice>();
  private pool = new Map<RestartAccount, Map<string, string | null>>();
  private fixtureLoadedAt = "";
  private approvedPayloads = new Map<string, ApprovedPayload>();
  private strictAttributeChecks = new Map<RestartAccount, boolean>([["JV", true], ["XL", true]]);
  constructor(private readonly clock: () => Date = () => new Date()) {
    this.fixtureLoadedAt = this.now();
    for (const account of ["JV", "XL"] as const) {
      // Supplied demo pool: deliberately non-GTIN identifiers, never generated as real EANs.
      this.pool.set(account, new Map(["A", "B", "C", "D"].map((suffix) => [`DEMO-NEW-${account}-${suffix}`, null])));
      const scenarios = ["Готов к подготовке", "Цена отсутствует", "Не указан материал", "Ограничение Marketplace", "Цена ниже вычета"];
      scenarios.forEach((scenario, index) => {
        const id = `${account.toLowerCase()}-demo-${index + 1}`;
        const oldSku = `DEMO-OLD-${account}-${index + 1}`;
        const content: ReviewContent = { title: index === 2 ? "Sessel Nord, grau" : "Sessel Nord mit Holzgestell, grau", category: "demo-chair", attributes: { material: index === 2 ? "" : "Holz / Stoff", color: "Grau", width: "80", height: "95" }, images: ["chair"], titleOrigin: "SOURCE" };
        this.items.set(id, { id, account, batchId: `demo-${account.toLowerCase()}`, scenario, mode: "DEMO", publishAllowed: false, version: 1, payloadVersion: 0, status: "IMPORTED", old: { ...content, ean: oldSku, sku: oldSku, productReference: `DEMO-REF-${account}-${index + 1}`, activeStatus: "INACTIVE", marketplaceStatus: index === 3 ? "RESTRICTED" : "OFFLINE" }, prepared: null, identity: null, price: null, issues: [], approval: null, events: [] });
        this.prices.set(id, { account, sku: oldSku, amount: index === 1 ? null : index === 4 ? 500 : account === "JV" ? 10000 : 11000, currency: "EUR", revision: 1 });
      });
    }
  }
  private now() { return this.clock().toISOString(); }
  setStrictAttributeChecks(account: RestartAccount, value: boolean) { this.strictAttributeChecks.set(account, value); }
  private item(account: RestartAccount, id: string) {
    const item = this.items.get(id);
    if (!item || item.account !== account) throw new ReviewProblem(404, "NOT_FOUND", "Карточка не найдена в выбранном аккаунте.");
    return item;
  }
  get(account: RestartAccount, id: string) { return clone(this.item(account, id)); }
  allItems(account: RestartAccount) { return clone([...this.items.values()].filter((item) => item.account === account)); }
  demoPoolCounts(account: RestartAccount) {
    const values = [...this.pool.get(account)!.values()];
    return { available: values.filter((value) => value === null).length, reserved: values.filter((value) => value !== null).length };
  }
  demoPoolEntries(account: RestartAccount) {
    return [...this.pool.get(account)!.entries()].map(([ean, itemId]) => ({ ean, status: itemId === null ? "AVAILABLE" as const : "RESERVED" as const, batchId: itemId ? this.items.get(itemId)?.batchId ?? null : null, importedAt: this.fixtureLoadedAt }));
  }
  list(account: RestartAccount, query = "", status = "", page = 1, pageSize = 20): ReviewList {
    if (!Number.isInteger(page) || page < 1 || !Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100) throw new ReviewProblem(422, "VALIDATION_ERROR", "Некорректная пагинация.");
    const search = query.toLocaleLowerCase();
    const matches = [...this.items.values()].filter((item) => item.account === account && (!status || item.status === status) && [item.id, item.batchId, item.old.ean, item.identity?.newEan ?? "", item.old.title, item.prepared?.title ?? "", item.scenario].some((value) => value.toLocaleLowerCase().includes(search)));
    const reservations = [...this.pool.get(account)!.values()];
    return { mode: "DEMO", items: clone(matches.slice((page - 1) * pageSize, page * pageSize)), page: { page, pageSize, totalItems: matches.length, totalPages: Math.ceil(matches.length / pageSize) }, pool: { available: reservations.filter((value) => value === null).length, reserved: reservations.filter((value) => value !== null).length } };
  }
  private checkVersion(item: ReviewItem, expectedVersion: number) {
    if (!Number.isInteger(expectedVersion) || expectedVersion !== item.version) throw new ReviewProblem(409, "CONFLICT", "Карточка изменилась. Обновите её перед действием.");
  }
  private event(item: ReviewItem, action: string, description: string) {
    item.version += 1;
    item.events.push({ at: this.now(), action, description, version: item.version });
  }
  private invalidate(item: ReviewItem) {
    item.approval = null;
    this.approvedPayloads.delete(item.id);
  }
  private snapshot(item: ReviewItem): PriceSnapshot | ReviewIssue {
    const source = this.prices.get(item.id)!;
    if (source.account !== item.account || source.sku !== item.old.sku) return { code: "PRICE_UNAVAILABLE", message: "Цена относится к другому аккаунту или SKU.", field: "price" };
    if (source.amount === null) return { code: "PRICE_UNAVAILABLE", message: "В демонстрационном ответе OTTO нет standardPrice.amount.", field: "price" };
    const discountCents = item.account === "JV" ? 1000 : 1500;
    if (source.currency !== "EUR" || !Number.isSafeInteger(source.amount) || source.amount <= discountCents) return { code: "PRICE_REVIEW_REQUIRED", message: "Цена должна быть в EUR и больше суммы вычета.", field: "price" };
    return { source: "DEMO_OTTO_STANDARD_PRICE", account: item.account, oldSku: item.old.sku, baseCents: source.amount, discountCents, newCents: source.amount - discountCents, currency: "EUR", fetchedAt: this.now(), snapshotVersion: source.revision };
  }
  private refresh(item: ReviewItem): { changed: boolean; valid: boolean } {
    const snapshot = this.snapshot(item);
    if ("code" in snapshot) {
      this.invalidate(item);
      item.price = null;
      item.status = snapshot.code as "PRICE_UNAVAILABLE" | "PRICE_REVIEW_REQUIRED";
      item.issues = [snapshot];
      return { changed: true, valid: false };
    }
    const changed = !!item.price && (item.price.baseCents !== snapshot.baseCents || item.price.snapshotVersion !== snapshot.snapshotVersion);
    item.price = snapshot;
    if (changed) {
      item.payloadVersion += 1;
      this.invalidate(item);
      item.status = "PRICE_CHANGED_REAPPROVAL_REQUIRED";
      item.issues = [{ code: "PRICE_CHANGED_REAPPROVAL_REQUIRED", field: "price", message: "StandardPrice старого SKU изменился. Проверьте новую цену и утвердите карточку заново." }];
    } else if (item.status === "PRICE_UNAVAILABLE" || item.status === "PRICE_REVIEW_REQUIRED") {
      item.status = item.prepared ? "CONTENT_PREPARED" : "IMPORTED";
      item.issues = item.issues.filter((issue) => issue.field !== "price");
    }
    return { changed, valid: true };
  }
  private validateItem(item: ReviewItem): ReviewIssue[] {
    const issues: ReviewIssue[] = [];
    if (item.old.marketplaceStatus === "RESTRICTED") issues.push({ code: "BLOCKED_BY_OTTO", message: "В демо-сценарии Marketplace ограничивает старый товар.", field: "status" });
    if (!item.price) issues.push({ code: "PRICE_UNAVAILABLE", message: "Нет снимка standardPrice старого SKU.", field: "price" });
    if (!item.identity) issues.push({ code: "GTIN_REVIEW_REQUIRED", message: "Условный демо-EAN ещё не зарезервирован.", field: "identity" });
    if (!item.prepared) return [...issues, { code: "NEEDS_INPUT", message: "Сначала подготовьте карточку." }];
    const content = item.prepared;
    if (!content.title.trim() || [...content.title].length > DEMO_TITLE_LIMIT) issues.push({ code: "TITLE_INVALID", field: "title", message: `Название должно содержать 1–${DEMO_TITLE_LIMIT} символов по демо-правилу.` });
    if (!DEMO_CATEGORIES.some((category) => category.id === content.category)) issues.push({ code: "CATEGORY_INVALID", field: "category", message: "Выберите демонстрационную категорию." });
    if (this.strictAttributeChecks.get(item.account)) for (const attribute of ALLOWED_ATTRIBUTES) if (!content.attributes[attribute]?.trim()) issues.push({ code: "ATTRIBUTE_MISSING", field: attribute, message: `Заполните атрибут «${attribute}».` });
    for (const attribute of ["width", "height"]) if (content.attributes[attribute] && (!/^\d+(\.\d{1,2})?$/.test(content.attributes[attribute]) || Number(content.attributes[attribute]) <= 0)) issues.push({ code: "ATTRIBUTE_INVALID", field: attribute, message: `«${attribute}» должно быть положительным размером в сантиметрах.` });
    if (!content.images.length) issues.push({ code: "IMAGE_MISSING", field: "images", message: "Добавьте демонстрационное изображение." });
    return issues;
  }
  patch(account: RestartAccount, id: string, expectedVersion: number, patch: ReviewPatch) {
    const item = this.item(account, id);
    this.checkVersion(item, expectedVersion);
    if (!item.prepared) throw new ReviewProblem(409, "INVALID_STATE", "Сначала подготовьте карточку.");
    if (!patch || Object.keys(patch).some((key) => !["title", "category", "attributes", "images"].includes(key)) || typeof patch.title !== "string" || typeof patch.category !== "string" || !patch.attributes || typeof patch.attributes !== "object" || Array.isArray(patch.attributes) || !Array.isArray(patch.images) || patch.title.length > 500 || Object.entries(patch.attributes).some(([key, value]) => !ALLOWED_ATTRIBUTES.includes(key) || typeof value !== "string" || value.length > 200) || patch.images.some((image) => image !== "chair")) throw new ReviewProblem(422, "VALIDATION_ERROR", "Разрешены только название, категория, демо-атрибуты и локальные изображения. Цену и идентификаторы менять вручную нельзя.");
    item.prepared = { title: patch.title.trim(), category: patch.category, attributes: clone(patch.attributes), images: [...new Set(patch.images)], titleOrigin: "OPERATOR" };
    item.payloadVersion += 1;
    item.status = "CONTENT_PREPARED";
    item.issues = [];
    this.invalidate(item);
    this.event(item, "EDITED", "Изменены поля карточки; прежнее утверждение сброшено.");
    return clone(item);
  }
  act(account: RestartAccount, id: string, expectedVersion: number, action: ReviewAction): ReviewItem {
    const item = this.item(account, id);
    this.checkVersion(item, expectedVersion);
    if (action === "simulate-price-change") {
      const price = this.prices.get(id)!;
      if (price.amount === null) throw new ReviewProblem(409, "PRICE_UNAVAILABLE", "В этом сценарии нет цены для изменения.");
      price.amount += 1000;
      price.revision += 1;
      this.event(item, "DEMO_PRICE_CHANGED", "Условный источник standardPrice увеличен на 10 €. Нажмите «Обновить цену».");
      return clone(item);
    }
    if (action === "prepare") {
      if (item.old.marketplaceStatus === "RESTRICTED") {
        item.status = "BLOCKED_BY_OTTO";
        item.issues = [{ code: "BLOCKED_BY_OTTO", message: "Marketplace ограничивает товар в этом демо-сценарии." }];
      } else if (this.refresh(item).valid) {
        if (!item.identity) {
          const pool = this.pool.get(account)!;
          const entry = [...pool.entries()].find(([, owner]) => owner === null);
          if (!entry) throw new ReviewProblem(409, "POOL_EXHAUSTED", "В демо-пуле нет свободных идентификаторов.");
          pool.set(entry[0], item.id);
          item.identity = { newEan: entry[0], newSku: entry[0], newProductReference: entry[0], mode: "NEW_PRODUCT_REFERENCE" };
        }
        if (!item.prepared) {
          item.prepared = { title: item.old.title, category: item.old.category, attributes: clone(item.old.attributes), images: [...item.old.images], titleOrigin: "SOURCE" };
          item.payloadVersion += 1;
          item.status = "CONTENT_PREPARED";
          item.issues = [];
        }
      }
    } else if (action === "refresh-price") {
      this.refresh(item);
    } else if (action === "suggest") {
      if (!item.prepared) throw new ReviewProblem(409, "INVALID_STATE", "Сначала подготовьте карточку.");
      item.prepared.title = "Sessel Nord aus Holz und Stoff – Grau, 80 cm";
      item.prepared.titleOrigin = "DEMO_AI_STUB";
      item.payloadVersion += 1;
      item.status = "CONTENT_PREPARED";
      item.issues = [];
      this.invalidate(item);
    } else if (action === "validate") {
      item.issues = this.validateItem(item);
      item.status = item.issues.length ? "VALIDATION_FAILED" : "READY_FOR_APPROVAL";
      this.invalidate(item);
    } else if (action === "approve") {
      const refreshed = this.refresh(item);
      if (!refreshed.valid || refreshed.changed) {
        this.event(item, "APPROVAL_BLOCKED", "Цена перепроверена; требуется исправление или повторное утверждение.");
        return clone(item);
      }
      const issues = this.validateItem(item);
      if (issues.length) {
        item.issues = issues;
        item.status = "VALIDATION_FAILED";
        this.invalidate(item);
      } else {
        if (item.status !== "READY_FOR_APPROVAL" && item.status !== "APPROVED") throw new ReviewProblem(409, "INVALID_STATE", "Сначала выполните проверку подготовленной версии.");
        const payload: ApprovedPayload = { identity: clone(item.identity!), content: clone(item.prepared!), price: clone(item.price!), demoOnly: true };
        const hash = createHash("sha256").update(JSON.stringify(payload)).digest("hex");
        item.approval = { payloadVersion: item.payloadVersion, payloadHash: hash, priceSnapshotVersion: item.price!.snapshotVersion, approvedAt: this.now(), demoOnly: true };
        item.status = "APPROVED";
        item.issues = [];
        this.approvedPayloads.set(item.id, clone(payload));
      }
    } else {
      throw new ReviewProblem(404, "NOT_FOUND", "Действие недоступно в демо-режиме.");
    }
    this.event(item, action.toUpperCase(), { prepare: "Подготовка выполнена с проверкой цены и условного EAN.", "refresh-price": "Прочитан демо-standardPrice старого SKU.", suggest: "Предложено имитированное AI-название. Внешний AI не вызывался.", validate: "Проверены атрибуты, идентификаторы и снимок цены.", approve: "Проверена свежая цена и утверждена конкретная демо-версия.", "simulate-price-change": "Демо-цена изменена." }[action]);
    return clone(item);
  }
  approvedPayload(account: RestartAccount, id: string) {
    this.item(account, id);
    const payload = this.approvedPayloads.get(id);
    return payload ? clone(payload) : null;
  }

  /** Fresh local-only preflight for a demo dry-run queue task. Never calls OTTO. */
  verifyApprovedSnapshot(account: RestartAccount, id: string, payloadVersion: number, priceSnapshotVersion: number, payloadHash: string): { ok: true; snapshot: DemoApprovedSnapshot } | { ok: false; item: ReviewItem; message: string } {
    const item = this.item(account, id);
    const payload = this.approvedPayloads.get(id);
    if (item.status !== "APPROVED" || !item.approval || !payload) return { ok: false, item: clone(item), message: "Утверждение карточки уже недействительно. Проверьте и утвердите её заново." };
    if (item.approval.payloadVersion !== payloadVersion || item.approval.priceSnapshotVersion !== priceSnapshotVersion || item.approval.payloadHash !== payloadHash) return { ok: false, item: clone(item), message: "Версия утверждения отличается от снимка задачи." };

    const fresh = this.snapshot(item);
    if ("code" in fresh) {
      this.invalidate(item);
      item.price = null;
      item.status = fresh.code as "PRICE_UNAVAILABLE" | "PRICE_REVIEW_REQUIRED";
      item.issues = [fresh];
      this.event(item, "DRY_RUN_BLOCKED", "Локальная проверка очереди остановлена: стандартная цена недоступна или требует проверки.");
      return { ok: false, item: clone(item), message: fresh.message };
    }
    if (!item.price || item.price.baseCents !== fresh.baseCents || item.price.snapshotVersion !== fresh.snapshotVersion || item.price.currency !== fresh.currency) {
      item.price = fresh;
      item.payloadVersion += 1;
      this.invalidate(item);
      item.status = "PRICE_CHANGED_REAPPROVAL_REQUIRED";
      item.issues = [{ code: "PRICE_CHANGED_REAPPROVAL_REQUIRED", field: "price", message: "Цена изменилась после утверждения. Повторите проверку и утверждение." }];
      this.event(item, "DRY_RUN_BLOCKED", "Локальная проверка очереди остановлена: цена изменилась после утверждения.");
      return { ok: false, item: clone(item), message: "Цена изменилась после утверждения; требуется повторная проверка." };
    }

    const actualHash = createHash("sha256").update(JSON.stringify(payload)).digest("hex");
    if (actualHash !== payloadHash || payload.demoOnly !== true || item.publishAllowed !== false || item.approval.demoOnly !== true) return { ok: false, item: clone(item), message: "Снимок утверждённой демо-карточки не прошёл проверку целостности." };
    return { ok: true, snapshot: { item: clone(item), payload: clone(payload) } };
  }
}
