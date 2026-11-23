import type { RestartApiClient } from "../api/client";
import type { RestartAccount, RestartProduct } from "../api/types";

export type CatalogBulkAction = "assign-ean" | "defaults" | "ai-title" | "ai-category" | "ai-attributes";
export interface EditableProductContent {
  title: string;
  category: string;
  attributes: Record<string, string>;
}
export interface BulkContentProposal {
  candidateId: string;
  account: RestartAccount;
  expectedVersion: number;
  before: EditableProductContent;
  after: EditableProductContent;
  changes: Array<{ field: string; before: string; after: string }>;
}

function sourceAttributes(product: RestartProduct): Record<string, string> {
  const attributes: Record<string, string> = {};
  for (const item of product.attributes ?? []) {
    if (typeof item.name !== "string") continue;
    const raw = item.value ?? item.values;
    const value = Array.isArray(raw) ? raw.map((v) => typeof v === "object" && v !== null ? String((v as { value?: unknown }).value ?? "") : String(v)).join("; ") : raw == null ? "" : String(raw);
    if (value.trim()) attributes[item.name] = value;
  }
  return attributes;
}

function requireEditable(product: RestartProduct, account: RestartAccount): void {
  if (product.account !== account) throw new Error("Карточка относится к другому аккаунту.");
  if (product.operationStatus && !["DRAFT", "PREPARING", "QC_PENDING", "NEEDS_REVIEW", "READY"].includes(product.operationStatus)) {
    throw new Error("Карточка уже в очереди, отправлена или закрыта. Правки недоступны.");
  }
}

export async function buildBulkProposal(
  api: RestartApiClient, action: Exclude<CatalogBulkAction, "assign-ean">,
  candidateId: string, account: RestartAccount, signal?: AbortSignal,
): Promise<BulkContentProposal> {
  const [product, { edit }] = await Promise.all([
    api.getProduct(candidateId, account, signal), api.getProductEdit(candidateId, account, signal),
  ]);
  requireEditable(product, account);
  const before: EditableProductContent = edit
    ? { title: edit.title, category: edit.category, attributes: { ...sourceAttributes(product), ...edit.attributes } }
    : { title: product.title ?? "", category: product.category ?? "", attributes: sourceAttributes(product) };
  const after = { ...before, attributes: { ...before.attributes } };
  if (action === "ai-title") {
    after.title = (await api.suggestTitle(account, before.title, signal)).title;
  } else if (action === "ai-category") {
    after.category = (await api.suggestCategory(candidateId, account, signal)).category;
  } else if (action === "ai-attributes") {
    if (!before.category) throw new Error("Сначала выберите и сохраните категорию товара.");
    const result = await api.suggestAttributes(candidateId, account, before.category, before.attributes, signal);
    for (const item of result.attributes) {
      const existing = Object.keys(after.attributes).find((name) => name.toLocaleLowerCase() === item.name.toLocaleLowerCase());
      if ((!existing || !after.attributes[existing]?.trim()) && item.value.trim()) {
        after.attributes[existing ?? item.name] = item.value;
      }
    }
  } else {
    if (!before.category) throw new Error("Сначала выберите и сохраните категорию товара.");
    const defaults = await api.getContentDefaults(account, before.category, signal);
    if (defaults.account !== account) throw new Error("Defaults получены для другого аккаунта.");
    for (const [name, value] of Object.entries(defaults.attributes)) {
      const existingName = Object.keys(after.attributes).find((key) => key.toLowerCase() === name.toLowerCase());
      if (defaults.accountAttributeNames.includes(name)) {
        if (existingName && existingName !== name) delete after.attributes[existingName];
        after.attributes[name] = value;
      } else if (!existingName || !after.attributes[existingName]?.trim()) {
        after.attributes[name] = value;
      }
    }
  }
  const changes: BulkContentProposal["changes"] = [];
  for (const [field, previous, next] of [["Название", before.title, after.title], ["Категория", before.category, after.category]]) {
    if (previous !== next) changes.push({ field, before: previous, after: next });
  }
  for (const name of new Set([...Object.keys(before.attributes), ...Object.keys(after.attributes)])) {
    const previous = before.attributes[name] ?? "", next = after.attributes[name] ?? "";
    if (previous !== next) changes.push({ field: name, before: previous, after: next });
  }
  return { candidateId, account, expectedVersion: edit?.version ?? 0, before, after, changes };
}

export async function saveBulkProposal(api: RestartApiClient, proposal: BulkContentProposal, idempotencyKey: string): Promise<void> {
  await api.saveProductEdit(proposal.candidateId, proposal.account, {
    ...proposal.after, expectedVersion: proposal.expectedVersion,
  }, idempotencyKey);
}

export async function assignSelectedEan(
  api: RestartApiClient, candidateId: string, account: RestartAccount, key: string, signal?: AbortSignal,
): Promise<string> {
  const product = await api.getProduct(candidateId, account, signal);
  requireEditable(product, account);
  if (product.newEan) return product.newEan;
  const operation = product.operationId && product.operationVersion
    ? { id: product.operationId, version: product.operationVersion }
    : await api.createOperation(account, candidateId, `${key}-create`, signal);
  const result = await api.reserveOperationEan(account, operation.id, operation.version, `${key}-reserve`, signal);
  return result.ean;
}
