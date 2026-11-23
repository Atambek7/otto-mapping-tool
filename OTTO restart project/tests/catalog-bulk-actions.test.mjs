import test from "node:test";
import assert from "node:assert/strict";
import { buildBulkProposal, saveBulkProposal, assignSelectedEan } from "../features/catalogs/bulk-actions.ts";
import { describeImportError, describeImportRunError } from "../features/catalogs/import-diagnostics.ts";

function fixture(account = "XL") {
  const calls = [];
  const edit = { title: "Operator title", category: "Sofa", attributes: { Farbe: "Blau", Marke: "JVMOEBEL" }, version: 4 };
  const product = { id: "candidate", account, title: "Source title", category: "Sofa", attributes: [], operationId: null, operationVersion: null, newEan: null };
  const api = {
    async getProduct(id, scope) { calls.push(["get", id, scope]); return product; },
    async getProductEdit(id, scope) { calls.push(["edit", id, scope]); return { edit }; },
    async suggestTitle(scope, title) { calls.push(["ai", scope, title]); return { title: "Proposed title" }; },
    async suggestCategory(id, scope) { calls.push(["category", id, scope]); return { category: "Sessel" }; },
    async suggestAttributes(id, scope, category) { calls.push(["attributes", id, scope, category]); return { attributes: [{ name: "Material", value: "Holz" }] }; },
    async getContentDefaults(scope, category) { calls.push(["defaults", scope, category]); return { account: scope, attributes: { Farbe: "Weiß", Marke: "XLMOEBEL", Breite: "100" }, accountAttributeNames: ["Marke"] }; },
    async saveProductEdit(...args) { calls.push(["save", ...args]); },
    async createOperation(scope, id, key) { calls.push(["create", scope, id, key]); return { id: "operation", version: 1 }; },
    async reserveOperationEan(...args) { calls.push(["reserve", ...args]); return { ean: "4990000000001", status: "RESERVED" }; },
  };
  return { calls, edit, product, api };
}

test("bulk AI proposes latest server edit; saving is separate and version bound", async () => {
  const { api, calls, edit } = fixture();
  const proposal = await buildBulkProposal(api, "ai-title", "candidate", "XL");
  assert.deepEqual(calls.find((call) => call[0] === "ai"), ["ai", "XL", "Operator title"]);
  assert.equal(calls.some((call) => call[0] === "save"), false);
  assert.equal(edit.title, "Operator title");
  assert.equal(proposal.expectedVersion, 4);
  await saveBulkProposal(api, proposal, "stable-save-key");
  assert.deepEqual(calls.at(-1), ["save", "candidate", "XL", { title: "Proposed title", category: "Sofa", attributes: edit.attributes, expectedVersion: 4 }, "stable-save-key"]);
});

test("defaults preserve nonempty operator values while isolating account brand", async () => {
  const { api, edit } = fixture();
  const proposal = await buildBulkProposal(api, "defaults", "candidate", "XL");
  assert.equal(proposal.after.attributes.Farbe, "Blau");
  assert.equal(proposal.after.attributes.Marke, "XLMOEBEL");
  assert.equal(proposal.after.attributes.Breite, "100");
  assert.equal(edit.attributes.Marke, "JVMOEBEL");
});

test("category suggestions keep all existing attributes for review", async () => {
  const { api, edit } = fixture();
  const proposal = await buildBulkProposal(api, "ai-category", "candidate", "XL");
  assert.equal(proposal.after.category, "Sessel");
  assert.deepEqual(proposal.after.attributes, edit.attributes);
});

test("cross-account or submitted cards are rejected before spending AI requests", async () => {
  const { api, calls, product } = fixture("JV");
  await assert.rejects(buildBulkProposal(api, "ai-title", "candidate", "XL"), /другому аккаунту/);
  assert.equal(calls.some((call) => call[0] === "ai"), false);
  product.account = "XL"; product.operationStatus = "SUBMITTED";
  await assert.rejects(buildBulkProposal(api, "ai-title", "candidate", "XL"), /уже в очереди/);
  assert.equal(calls.some((call) => call[0] === "ai"), false);
});

test("EAN assignment uses a scoped operation; repeat uses the existing EAN", async () => {
  const { api, calls, product } = fixture();
  assert.equal(await assignSelectedEan(api, "candidate", "XL", "action"), "4990000000001");
  assert.deepEqual(calls.find((call) => call[0] === "reserve").slice(0, 5), ["reserve", "XL", "operation", 1, "action-reserve"]);
  product.newEan = "4990000000001";
  await assignSelectedEan(api, "candidate", "XL", "retry");
  assert.equal(calls.filter((call) => call[0] === "reserve").length, 1);
  assert.equal(calls.some((call) => call[0] === "ai"), false);
});

test("version conflicts are propagated without overwriting or retrying another revision", async () => {
  const { api } = fixture();
  const proposal = await buildBulkProposal(api, "ai-title", "candidate", "XL");
  let attempts = 0;
  api.saveProductEdit = async () => { attempts += 1; throw new Error("VERSION_CONFLICT"); };
  await assert.rejects(saveBulkProposal(api, proposal, "save-key"), /VERSION_CONFLICT/);
  assert.equal(attempts, 1);
});

test("import diagnostics distinguish protected source matches from missing OTTO products", () => {
  assert.match(describeImportError("PROTECTED_FACTORY_MATCH"), /найден в OTTO/);
  assert.match(describeImportError("PROTECTED_FACTORY_MATCH"), /защищённым фабрикам/);
  assert.match(describeImportRunError({ rowStates: { VALID: 3, IDENTITY_UNRESOLVED: 1 } }), /принято 3, требуют разбора 1/);
  assert.doesNotMatch(describeImportRunError({ rowStates: { VALID: 3 } }), /IMPORT_RUN_ERROR/);
});
