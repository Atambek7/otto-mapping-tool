const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const modulePath = process.env.OTTORESTART_DEMO_QUEUE_TEST_MODULE;
if (!modulePath) throw new Error("Set OTTORESTART_DEMO_QUEUE_TEST_MODULE to compiled features/queue/demo-queue-service.js");
const { DemoQueueService } = require(modulePath);
const { DemoReviewService, ReviewProblem } = require(path.resolve(path.dirname(modulePath), "../review/demo-service.js"));

let uuid = 0;
function fixture() {
  let tick = 0;
  const clock = () => new Date(Date.UTC(2026, 9, 13, 12, 0, tick++));
  return {
    review: new DemoReviewService(clock),
    queue: new DemoQueueService(clock, () => `task-${++uuid}`),
  };
}

function approve(review, account = "JV", id = `${account.toLowerCase()}-demo-1`) {
  let item = review.get(account, id);
  item = review.act(account, id, item.version, "prepare");
  item = review.act(account, id, item.version, "validate");
  assert.equal(item.status, "READY_FOR_APPROVAL");
  item = review.act(account, id, item.version, "approve");
  assert.equal(item.status, "APPROVED");
  return item;
}

function add(queue, review, item) {
  return queue.enqueueDryRun(item.account, [{ id: item.id, expectedVersion: item.version }], review);
}

test("only an approved demo payload enters the account-scoped queue", () => {
  const { review, queue } = fixture();
  const item = approve(review);
  const result = add(queue, review, item);
  assert.equal(result.queue.mode, "DEMO");
  assert.equal(result.queue.publicationEnabled, false);
  assert.equal(result.queue.tasks.length, 1);
  assert.equal(result.queue.tasks[0].status, "QUEUED");
  assert.equal(queue.snapshot("XL").tasks.length, 0);
});

test("unapproved items and stale item versions are rejected per ID", () => {
  const { review, queue } = fixture();
  const item = review.get("JV", "jv-demo-1");
  assert.match(add(queue, review, item).results[0].error, /утвержд[её]нн/i);
  const approved = approve(review);
  assert.match(add(queue, review, { ...approved, version: approved.version - 1 }).results[0].error, /изменилась/i);
  assert.equal(queue.snapshot("JV").tasks.length, 0);
});

test("enqueue is idempotent for the same approved payload", () => {
  const { review, queue } = fixture();
  const item = approve(review);
  const first = add(queue, review, item).results[0].task;
  const second = add(queue, review, item).results[0].task;
  assert.equal(first.id, second.id);
  assert.equal(queue.snapshot("JV").tasks.length, 1);
});

test("pause blocks dry-run, resume lets only the local check complete", () => {
  const { review, queue } = fixture();
  const item = approve(review);
  const task = add(queue, review, item).results[0].task;
  queue.pause("JV");
  assert.throws(() => queue.runDryRun("JV", task.id, review), (error) => error instanceof ReviewProblem && error.code === "QUEUE_PAUSED");
  queue.resume("JV");
  const result = queue.runDryRun("JV", task.id, review);
  assert.equal(result.status, "DRY_RUN_COMPLETE");
  assert.match(result.result, /OTTO не выполнялись/);
});

test("stopping task intake prevents more tasks without stopping an existing dry-run", () => {
  const { review, queue } = fixture();
  const first = approve(review);
  const task = add(queue, review, first).results[0].task;
  queue.stopAccepting("JV");
  assert.throws(() => queue.enqueueDryRun("JV", [{ id: first.id, expectedVersion: first.version }], review), (error) => error instanceof ReviewProblem && error.code === "QUEUE_NOT_ACCEPTING");
  assert.equal(queue.runDryRun("JV", task.id, review).status, "DRY_RUN_COMPLETE");
  queue.startAccepting("JV");
  assert.equal(queue.snapshot("JV").acceptingTasks, true);
});

test("dry-run detects a changed source price and clears the approval", () => {
  const { review, queue } = fixture();
  const item = approve(review);
  const task = add(queue, review, item).results[0].task;
  const changed = review.act("JV", item.id, item.version, "simulate-price-change");
  assert.equal(changed.status, "APPROVED");
  const result = queue.runDryRun("JV", task.id, review);
  assert.equal(result.status, "BLOCKED");
  const latest = review.get("JV", item.id);
  assert.equal(latest.status, "PRICE_CHANGED_REAPPROVAL_REQUIRED");
  assert.equal(latest.approval, null);
});

test("a content edit after queueing blocks the stale queue snapshot", () => {
  const { review, queue } = fixture();
  const item = approve(review);
  const task = add(queue, review, item).results[0].task;
  const edited = review.patch("JV", item.id, item.version, { title: "Sessel Nord, red", category: item.prepared.category, attributes: item.prepared.attributes, images: item.prepared.images });
  assert.equal(edited.status, "CONTENT_PREPARED");
  assert.equal(queue.runDryRun("JV", task.id, review).status, "BLOCKED");
});

test("cancel only applies while queued and is visible in account counts", () => {
  const { review, queue } = fixture();
  const item = approve(review);
  const task = add(queue, review, item).results[0].task;
  assert.equal(queue.cancel("JV", task.id).status, "CANCELLED");
  assert.equal(queue.snapshot("JV").counts.CANCELLED, 1);
  assert.throws(() => queue.runDryRun("JV", task.id, review), (error) => error instanceof ReviewProblem && error.code === "INVALID_STATE");
});

test("another account cannot inspect or act on a task", () => {
  const { review, queue } = fixture();
  const item = approve(review);
  const task = add(queue, review, item).results[0].task;
  assert.throws(() => queue.cancel("XL", task.id), (error) => error instanceof ReviewProblem && error.status === 404);
});

test("queue input rejects duplicate IDs and caps one request at twenty", () => {
  const { review, queue } = fixture();
  assert.throws(() => queue.enqueueDryRun("JV", [{ id: "jv-demo-1", expectedVersion: 1 }, { id: "jv-demo-1", expectedVersion: 1 }], review), (error) => error instanceof ReviewProblem && error.status === 422);
  assert.throws(() => queue.enqueueDryRun("JV", Array.from({ length: 21 }, (_, index) => ({ id: `jv-demo-${index}`, expectedVersion: 1 })), review), (error) => error instanceof ReviewProblem && error.status === 422);
});
