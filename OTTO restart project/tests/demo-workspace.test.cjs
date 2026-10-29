const test = require("node:test");
const assert = require("node:assert/strict");

const { DemoWorkspaceService } = require(process.env.OTTORESTART_DEMO_WORKSPACE_TEST_MODULE);
const { DemoReviewService } = require(process.env.OTTORESTART_DEMO_TEST_MODULE);
const { DemoQueueService } = require(process.env.OTTORESTART_DEMO_QUEUE_TEST_MODULE);

function fixture() {
  let id = 0;
  const clock = () => new Date("2026-10-13T10:00:00.000Z");
  const createId = () => `fixture-${++id}`;
  return { workspace: new DemoWorkspaceService(clock, createId), review: new DemoReviewService(clock), queue: new DemoQueueService(clock, createId) };
}

test("overview aggregates session fixtures and never confirms ONLINE", () => {
  const { workspace, review, queue } = fixture();
  const overview = workspace.overview("JV", review, queue);
  assert.equal(overview.mode, "DEMO");
  assert.equal(overview.counts.candidates, 5);
  assert.equal(overview.counts.onlineConfirmed, 0);
  assert.equal(overview.counts.availableEans, 4);
  assert.equal(overview.attention.length, 4);
  assert.equal(overview.statusBreakdown.reduce((sum, row) => sum + row.count, 0), 5);
  assert.equal(overview.batches[0].id, "demo-jv");
});

test("batch preview is read-only and creation uses only generated demo IDs", () => {
  const { workspace, review } = fixture();
  const before = workspace.listBatches("JV", review);
  const preview = workspace.previewBatch("JV", { period: "3M", name: "Тест", itemCount: 2 });
  assert.equal(preview.writesPerformed, false);
  assert.equal(preview.invalidCount, 0);
  assert.equal(workspace.listBatches("JV", review).page.totalItems, before.page.totalItems);
  const batch = workspace.createBatch("JV", { period: "3M", name: "Тест", itemCount: 2 }, review);
  assert.equal(batch.items.length, 2);
  assert.match(batch.items[0].sourceEan, /^DEMO-SOURCE-JV-/);
  assert.equal(workspace.listBatches("JV", review).page.totalItems, 2);
  assert.equal(workspace.listBatches("XL", review).page.totalItems, 1);
  assert.throws(() => workspace.getBatch("XL", batch.id, review), { status: 404 });
});

test("batch detail rows are paginated and searchable on the server service", () => {
  const { workspace, review } = fixture();
  workspace.patchSettings("XL", { maximumDemoBatchItems: 200 });
  const batch = workspace.createBatch("XL", { period: "12M", name: "Большой demo batch", itemCount: 125 }, review);
  const second = workspace.listBatchItems("XL", batch.id, review, "", 2, 50);
  assert.equal(second.page.totalItems, 125);
  assert.equal(second.page.totalPages, 3);
  assert.equal(second.items.length, 50);
  assert.equal(second.items[0].rowNumber, 51);
  const filtered = workspace.listBatchItems("XL", batch.id, review, "DEMO-SOURCE-XL-", 1, 50);
  assert.equal(filtered.page.totalItems, 125);
});

test("demo EAN additions stay synthetic and isolated by account", () => {
  const { workspace } = fixture();
  const review = new DemoReviewService();
  const before = workspace.listEans("JV", review).page.totalItems;
  const imported = workspace.addDemoEans("JV", 3);
  assert.equal(imported.importedCount, 3);
  assert.equal(workspace.listEans("JV", review).page.totalItems, before + 3);
  assert.equal(workspace.listEans("XL", review).page.totalItems, 4);
  assert.ok(imported.entries.every((entry) => entry.demoOnly && entry.ean.startsWith("DEMO-POOL-JV-X")));
});

test("EAN pool reflects temporary demo reservations from the review workflow", () => {
  const { workspace, review } = fixture();
  review.act("JV", "jv-demo-1", 1, "prepare");
  const pool = workspace.listEans("JV", review).items;
  assert.equal(pool.find((entry) => entry.ean === "DEMO-NEW-JV-A").status, "RESERVED");
  assert.equal(pool.find((entry) => entry.ean === "DEMO-NEW-JV-B").status, "AVAILABLE");
});

test("demo settings are account scoped and publication cannot be enabled", () => {
  const { workspace } = fixture();
  const saved = workspace.patchSettings("JV", { defaultPeriod: "3M", maximumDemoBatchItems: 12, strictAttributeChecks: false });
  assert.equal(saved.defaultPeriod, "3M");
  assert.equal(saved.maximumDemoBatchItems, 12);
  assert.equal(saved.strictAttributeChecks, false);
  assert.equal(saved.restartPublishEnabled, false);
  assert.equal(workspace.getSettings("XL").defaultPeriod, "12M");
  assert.throws(() => workspace.patchSettings("JV", { restartPublishEnabled: true }), { status: 422 });
});

test("events include fixtures and local workflow changes only", () => {
  const { workspace, review } = fixture();
  workspace.createBatch("JV", { period: "12M", name: "Журнал", itemCount: 1 }, review);
  const events = workspace.listEvents("JV", review).items;
  assert.ok(events.some((event) => event.eventType === "DEMO_BATCH_CREATED"));
  assert.ok(events.some((event) => event.eventType === "DEMO_FIXTURE_LOADED"));
  assert.ok(events.every((event) => event.demoOnly));
});
