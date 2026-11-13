import { test } from "node:test";
import assert from "node:assert/strict";
import { readProductDraft, writeProductDraft, removeProductDraftIfUnchanged } from "../features/catalogs/product-drafts.ts";

function memoryStorage() {
  const data = new Map();
  return {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => data.set(key, value),
    removeItem: (key) => data.delete(key),
    data,
  };
}

test("drafts persist independently for each account and candidate", () => {
  const storage = memoryStorage();
  const draft = { title: "Sofa", category: "Sofa", attributes: { Farbe: "Blau", "Breite": "200" } };
  writeProductDraft(storage, "JV", "candidate/1", draft);
  assert.deepEqual(readProductDraft(storage, "JV", "candidate/1"), draft);
  assert.equal(readProductDraft(storage, "XL", "candidate/1"), null);
  assert.equal(readProductDraft(storage, "JV", "candidate/2"), null);
  writeProductDraft(storage, "XL", "candidate/1", { ...draft, title: "XL" });
  assert.equal(readProductDraft(storage, "JV", "candidate/1").title, "Sofa");
});

test("a browser draft is removed only if it is still the version imported", () => {
  const storage = memoryStorage();
  const draft = { title: "Sofa", category: "Sofa", attributes: { Farbe: "Blau" } };
  writeProductDraft(storage, "JV", "1", draft);
  writeProductDraft(storage, "JV", "1", { ...draft, title: "Updated elsewhere" });
  assert.equal(removeProductDraftIfUnchanged(storage, "JV", "1", draft), false);
  assert.equal(readProductDraft(storage, "JV", "1").title, "Updated elsewhere");
  const newer = readProductDraft(storage, "JV", "1");
  assert.equal(removeProductDraftIfUnchanged(storage, "JV", "1", newer), true);
  assert.equal(readProductDraft(storage, "JV", "1"), null);
});

test("invalid stored data is ignored and storage failures reach the caller", () => {
  const storage = memoryStorage();
  writeProductDraft(storage, "JV", "1", { title: "Sofa", category: "Sofa", attributes: {} });
  const [key] = storage.data.keys();
  for (const raw of ["{", JSON.stringify({ version: 1, title: "Sofa", category: "Sofa", attributes: { Farbe: 5 } }), "x".repeat(1_000_001)]) {
    storage.data.set(key, raw);
    assert.equal(readProductDraft(storage, "JV", "1"), null);
  }
  assert.throws(() => writeProductDraft({ setItem() { throw new Error("quota"); } }, "JV", "1", { title: "Sofa", category: "Sofa", attributes: {} }), /quota/);
  assert.throws(() => writeProductDraft(storage, "JV", "1", { title: "x".repeat(1_000_001), category: "Sofa", attributes: {} }), /limits/);
});
