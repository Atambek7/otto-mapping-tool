import type { RestartAccount } from "@/features/api/types";

export interface ProductDraft {
  title: string;
  category: string;
  attributes: Record<string, string>;
}

function draftKey(account: RestartAccount, candidateId: string): string {
  return `ottorestart:product-draft:v1:${account}:${encodeURIComponent(candidateId)}`;
}

export function readProductDraft(storage: Storage, account: RestartAccount, candidateId: string): ProductDraft | null {
  const raw = storage.getItem(draftKey(account, candidateId));
  if (!raw || raw.length > 1_000_000) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const draft = value as Record<string, unknown>;
    if (draft.version !== 1 || typeof draft.title !== "string" || typeof draft.category !== "string"
      || !draft.attributes || typeof draft.attributes !== "object" || Array.isArray(draft.attributes)) return null;
    const entries = Object.entries(draft.attributes);
    if (entries.length > 1000 || entries.some(([key, item]) => !key || typeof item !== "string")) return null;
    return { title: draft.title, category: draft.category, attributes: Object.fromEntries(entries) as Record<string, string> };
  } catch {
    return null;
  }
}

export function writeProductDraft(storage: Storage, account: RestartAccount, candidateId: string, draft: ProductDraft): void {
  const serialized = JSON.stringify({ version: 1, ...draft });
  if (serialized.length > 1_000_000 || Object.keys(draft.attributes).length > 1000) {
    throw new Error("Product draft exceeds storage limits.");
  }
  storage.setItem(draftKey(account, candidateId), serialized);
}

export function removeProductDraftIfUnchanged(storage: Storage, account: RestartAccount, candidateId: string, imported: ProductDraft): boolean {
  const current = readProductDraft(storage, account, candidateId);
  if (!current || JSON.stringify(current) !== JSON.stringify(imported)) return false;
  storage.removeItem(draftKey(account, candidateId));
  return true;
}
