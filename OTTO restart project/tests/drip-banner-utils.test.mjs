import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import ts from "typescript";

function loadBannerUtils() {
  const directory = mkdtempSync(path.join(tmpdir(), "otto-restart-drip-banner-"));
  const source = readFileSync(
    new URL("../features/catalogs/drip-banner-utils.ts", import.meta.url),
    "utf8",
  );
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const modulePath = path.join(directory, "drip-banner-utils.cjs");
  writeFileSync(modulePath, output);
  const requireFromDirectory = createRequire(modulePath);
  return {
    ...requireFromDirectory("./drip-banner-utils.cjs"),
    cleanup: () => rmSync(directory, { recursive: true, force: true }),
  };
}

function makeBatch(overrides = {}) {
  return {
    id: "batch-1",
    account: "JV",
    period: "12M",
    name: "Plan A",
    status: "ACTIVE",
    itemCount: 100,
    createdAt: "2026-11-16T08:00:00Z",
    factoryId: "407210",
    localStartDate: "2026-11-16",
    dailyTime: "15:22",
    timezone: "Europe/Berlin",
    scheduledStartAt: "2026-11-16T14:22:00Z",
    totalDays: 14,
    version: 3,
    ...overrides,
  };
}

test("computeDripCurrentDay calculates elapsed calendar days in target timezone and clamps range", (t) => {
  const utils = loadBannerUtils();
  t.after(utils.cleanup);

  const day3 = new Date("2026-11-18T12:00:00Z");
  assert.equal(utils.computeDripCurrentDay("2026-11-16", 14, "Europe/Berlin", day3), 3);

  const futureStart = new Date("2026-11-10T12:00:00Z");
  assert.equal(utils.computeDripCurrentDay("2026-11-16", 14, "Europe/Berlin", futureStart), 1);

  const pastEnd = new Date("2026-12-20T12:00:00Z");
  assert.equal(utils.computeDripCurrentDay("2026-11-16", 14, "Europe/Berlin", pastEnd), 14);
});

test("pickBannerBatch prioritizes ACTIVE over SCHEDULED over PAUSED and ignores terminal batches", (t) => {
  const utils = loadBannerUtils();
  t.after(utils.cleanup);

  const completed = makeBatch({ id: "b0", status: "COMPLETED" });
  const paused = makeBatch({ id: "b1", status: "PAUSED" });
  const scheduled = makeBatch({ id: "b2", status: "SCHEDULED" });
  const active = makeBatch({ id: "b3", status: "ACTIVE" });

  assert.equal(utils.pickBannerBatch([completed]), null);
  assert.equal(utils.pickBannerBatch([completed, paused]).id, "b1");
  assert.equal(utils.pickBannerBatch([paused, scheduled]).id, "b2");
  assert.equal(utils.pickBannerBatch([paused, scheduled, active]).id, "b3");
});

test("buildBatchBannerViewModel maps counters, progress, status text, and timezone label", (t) => {
  const utils = loadBannerUtils();
  t.after(utils.cleanup);

  const model = utils.buildBatchBannerViewModel(
    makeBatch({
      status: "ACTIVE",
      itemCount: 1473,
      itemGroupCounts: { all: 1473, queue: 1100, processing: 55, published: 295, errors: 23 },
    }),
    new Date("2026-11-18T12:00:00Z"),
  );

  assert.equal(model.title, "Активный Drip Feed: День 3 из 14");
  assert.equal(model.statusText, "ВЫПОЛНЯЕТСЯ");
  assert.equal(model.statusTone, "running");
  assert.equal(model.dailyTime, "15:22");
  assert.equal(model.timezoneLabel, "Берлин");
  assert.equal(model.publishedCount, 295);
  assert.equal(model.processingCount, 55);
  assert.equal(model.errorsCount, 23);
  assert.equal(model.totalItems, 1473);
  assert.equal(model.percentage, 20);
});

test("buildPlanningBannerViewModel builds async planning progress banner", (t) => {
  const utils = loadBannerUtils();
  t.after(utils.cleanup);

  const model = utils.buildPlanningBannerViewModel({
    id: "req-1",
    account: "XL",
    name: "XL Plan",
    status: "BUILDING",
    itemCount: 1200,
    preparedCount: 480,
    errorCode: null,
    jobId: "job-1",
    jobState: "active",
    jobAttempts: 1,
    jobMaxAttempts: 3,
    batchCount: 0,
    factoryIds: ["f1"],
    startDate: "2026-11-19",
    dailyTime: "10:00",
    timezone: "Europe/Berlin",
    totalDays: 10,
    createdAt: "2026-11-18T08:00:00Z",
    processingStartedAt: "2026-11-18T08:01:00Z",
  });

  assert.equal(model.kind, "planning");
  assert.equal(model.statusText, "СОЗДАЁТСЯ ПЛАН");
  assert.equal(model.preparedCount, 480);
  assert.equal(model.percentage, 40);
});

test("resolveDripApiFactoryId drops demo direct-otto sentinel for OTTO_DIRECT scopes", (t) => {
  const utils = loadBannerUtils();
  t.after(utils.cleanup);

  assert.equal(utils.resolveDripApiFactoryId("DIRECT_OTTO", "direct-otto"), undefined);
  assert.equal(utils.resolveDripApiFactoryId("OTTO_DIRECT", "direct-otto"), undefined);
  assert.equal(utils.resolveDripApiFactoryId("OTTO_DIRECT", "  "), undefined);
  assert.equal(utils.resolveDripApiFactoryId("AFTERBUY", "482558"), "482558");
  assert.equal(utils.resolveDripApiFactoryId("AFTERBUY", "direct-otto"), "direct-otto");
});
