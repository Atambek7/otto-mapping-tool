import type { RestartAccount } from "../api/types";

export type ReviewStatus = "IMPORTED" | "CONTENT_PREPARED" | "VALIDATION_FAILED" | "READY_FOR_APPROVAL" | "APPROVED" | "PRICE_UNAVAILABLE" | "PRICE_REVIEW_REQUIRED" | "BLOCKED_BY_OTTO" | "PRICE_CHANGED_REAPPROVAL_REQUIRED";
export type ReviewTab = "candidates" | "preparation" | "approval" | "publication" | "history";
export const DEMO_TITLE_LIMIT = 70;
export const REVIEW_STATUS_LABELS: Record<ReviewStatus, string> = {
  IMPORTED: "Кандидат", CONTENT_PREPARED: "Подготовлен", VALIDATION_FAILED: "Есть ошибки",
  READY_FOR_APPROVAL: "Готов к утверждению", APPROVED: "Утверждён в демо",
  PRICE_UNAVAILABLE: "Нет цены", PRICE_REVIEW_REQUIRED: "Проверить цену",
  BLOCKED_BY_OTTO: "Блокировка OTTO (демо)", PRICE_CHANGED_REAPPROVAL_REQUIRED: "Цена изменилась",
};
export const DEMO_CATEGORIES = [{ id: "demo-chair", label: "Кресло · демо" }, { id: "demo-sofa", label: "Диван · демо" }];
export interface ReviewContent {
  title: string;
  category: string;
  attributes: Record<string, string>;
  images: string[];
  titleOrigin: "SOURCE" | "DEMO_AI_STUB" | "OPERATOR";
}
export interface PriceSnapshot {
  source: "DEMO_OTTO_STANDARD_PRICE";
  account: RestartAccount;
  oldSku: string;
  baseCents: number;
  discountCents: number;
  newCents: number;
  currency: "EUR";
  fetchedAt: string;
  snapshotVersion: number;
}
export interface ReviewIssue { code: string; message: string; field?: string }
export interface ReviewEvent { at: string; action: string; description: string; version: number }
export interface ReviewItem {
  id: string;
  account: RestartAccount;
  batchId: string;
  scenario: string;
  mode: "DEMO";
  publishAllowed: false;
  version: number;
  payloadVersion: number;
  status: ReviewStatus;
  old: ReviewContent & { ean: string; sku: string; productReference: string; activeStatus: string; marketplaceStatus: string };
  prepared: ReviewContent | null;
  identity: { newEan: string; newSku: string; newProductReference: string; mode: "NEW_PRODUCT_REFERENCE" } | null;
  price: PriceSnapshot | null;
  issues: ReviewIssue[];
  approval: { payloadVersion: number; payloadHash: string; priceSnapshotVersion: number; approvedAt: string; demoOnly: true } | null;
  events: ReviewEvent[];
}
export interface ReviewPatch { title: string; category: string; attributes: Record<string, string>; images: string[] }
export type ReviewAction = "prepare" | "suggest" | "refresh-price" | "validate" | "approve" | "simulate-price-change";
export interface ReviewList {
  mode: "DEMO";
  items: ReviewItem[];
  page: { page: number; pageSize: number; totalItems: number; totalPages: number };
  pool: { available: number; reserved: number };
}
