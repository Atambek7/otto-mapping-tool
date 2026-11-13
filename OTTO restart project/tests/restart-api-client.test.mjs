import assert from "node:assert/strict";
import { File } from "node:buffer";
import { createRequire } from "node:module";
import { readFileSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import ts from "typescript";

function loadApiClient() {
  const temporaryDirectory = mkdtempSync(path.join(tmpdir(), "otto-restart-client-test-"));
  const clientPath = new URL("../features/api/client.ts", import.meta.url);
  const csrfPath = new URL("../features/auth/csrf-client.ts", import.meta.url);
  const clientSource = readFileSync(clientPath, "utf8").replace(
    "@/features/auth/csrf-client",
    "./csrf-client.cjs",
  );
  const csrfSource = readFileSync(csrfPath, "utf8");
  const compilerOptions = {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  };
  writeFileSync(path.join(temporaryDirectory, "client.cjs"), ts.transpileModule(clientSource, { compilerOptions }).outputText);
  writeFileSync(path.join(temporaryDirectory, "csrf-client.cjs"), ts.transpileModule(csrfSource, { compilerOptions }).outputText);
  const requireFromTemporary = createRequire(path.join(temporaryDirectory, "client.cjs"));
  return {
    ...requireFromTemporary("./client.cjs"),
    cleanup: () => rmSync(temporaryDirectory, { recursive: true, force: true }),
  };
}

const emptyProducts = {
  items: [],
  page: { page: 2, pageSize: 50, total: 0, totalPages: 0 },
  metrics: { scope: "ACCOUNT_OPEN_CANDIDATES", total: 0, count3m: 0, count12m: 0, statusCounts: {} },
};

test("settings, defaults and EAN reservation preserve account, version and CSRF contracts", async (t) => {
  const { RestartApiClient, cleanup } = loadApiClient();
  t.after(cleanup);
  const calls = [];
  const client = new RestartApiClient({ csrfToken: () => "offline-csrf", fetchImpl: async (input, init) => {
    calls.push({ url: new URL(String(input)), init });
    return new Response(JSON.stringify({}), { status: 200, headers: { "Content-Type": "application/json" } });
  } });
  await client.getSettings("XL");
  await client.getContentDefaults("XL", "Skulptur");
  await client.reserveOperationEan("XL", "op-1", 3, "offline-reservation-key");
  assert.equal(calls[0].url.pathname, "/restart/api/backend/settings");
  assert.equal(calls[1].url.searchParams.get("category"), "Skulptur");
  assert.equal(calls[2].url.pathname, "/restart/api/backend/operations/op-1/reserve-ean");
  for (const call of calls) assert.equal(call.url.searchParams.get("account"), "XL");
  assert.equal(calls[2].init.headers.get("X-CSRF-Token"), "offline-csrf");
  assert.equal(calls[2].init.headers.get("Idempotency-Key"), "offline-reservation-key");
  assert.deepEqual(JSON.parse(calls[2].init.body), { account: "XL", expectedVersion: 3 });
});

test("product edits use account scoped BFF with version, CSRF and idempotency", async (t) => {
  const { RestartApiClient, cleanup } = loadApiClient();
  t.after(cleanup);
  const calls = [];
  const client = new RestartApiClient({ csrfToken: () => "offline-csrf", fetchImpl: async (input, init) => {
    calls.push({ url: new URL(String(input)), init });
    return new Response(JSON.stringify({ edit: null }), { status: 200, headers: { "Content-Type": "application/json" } });
  } });
  await client.getProductEdit("candidate-1", "JV");
  await client.saveProductEdit("candidate-1", "JV", { expectedVersion: 3, title: "Title", category: "Sofa", attributes: { Farbe: "Blau" } }, "edit-key-123");
  assert.equal(calls[0].url.pathname, "/restart/api/backend/products/candidate-1/edit");
  assert.equal(calls[0].url.searchParams.get("account"), "JV");
  assert.equal(calls[1].init.method, "PUT");
  assert.equal(calls[1].init.headers.get("X-CSRF-Token"), "offline-csrf");
  assert.equal(calls[1].init.headers.get("Idempotency-Key"), "edit-key-123");
  assert.equal(JSON.parse(calls[1].init.body).expectedVersion, 3);
  assert.equal(JSON.parse(calls[1].init.body).account, "JV");
});

test("EAN pool preview and import send account, CSRF and a stable idempotency key", async (t) => {
  const { RestartApiClient, cleanup } = loadApiClient();
  t.after(cleanup);
  const calls = [];
  const client = new RestartApiClient({ csrfToken: () => "offline-csrf", fetchImpl: async (input, init) => {
    calls.push({ url: new URL(String(input)), init });
    return new Response(JSON.stringify({ account: "XL", uniqueCount: 1, uniqueEans: ["4000000000001"] }), { status: 200, headers: { "Content-Type": "application/json" } });
  } });
  await client.previewEanPoolFile("XL", new File(["4000000000001"], "eans.txt", { type: "text/plain" }));
  await client.importEans("XL", ["4000000000001"], "ean-stable-key");
  assert.equal(calls[0].url.pathname, "/restart/api/backend/ean-pool/preview");
  assert.equal(calls[0].url.searchParams.get("account"), "XL");
  assert.equal(calls[0].init.headers.get("X-CSRF-Token"), "offline-csrf");
  assert.equal(calls[1].url.pathname, "/restart/api/backend/ean-pool/import");
  assert.equal(calls[1].init.headers.get("Idempotency-Key"), "ean-stable-key");
  assert.deepEqual(JSON.parse(calls[1].init.body), { account: "XL", eans: ["4000000000001"] });
});

test("listProducts sends read filters through the authenticated same-origin BFF", async (t) => {
  const { RestartApiClient, cleanup } = loadApiClient();
  t.after(cleanup);
  const calls = [];
  const client = new RestartApiClient({
    fetchImpl: async (input, init) => {
      calls.push({ url: new URL(String(input)), init });
      return new Response(JSON.stringify(emptyProducts), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });

  const response = await client.listProducts("XL", {
    page: 2,
    pageSize: 50,
    period: "12M",
    source: "OTTO_DIRECT",
    query: "SKU 123",
    category: "Sofa",
    titleOver70: true,
    sortBy: "name",
    sortDir: "desc",
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url.pathname, "/restart/api/backend/products");
  assert.equal(calls[0].url.searchParams.get("account"), "XL");
  assert.equal(calls[0].url.searchParams.get("page"), "2");
  assert.equal(calls[0].url.searchParams.get("pageSize"), "50");
  assert.equal(calls[0].url.searchParams.get("period"), "12M");
  assert.equal(calls[0].url.searchParams.get("source"), "OTTO_DIRECT");
  assert.equal(calls[0].url.searchParams.get("query"), "SKU 123");
  assert.equal(calls[0].url.searchParams.get("category"), "Sofa");
  assert.equal(calls[0].url.searchParams.get("titleOver70"), "true");
  assert.equal(calls[0].url.searchParams.get("sortBy"), "name");
  assert.equal(calls[0].url.searchParams.get("sortDir"), "desc");
  assert.equal(calls[0].init.method, "GET");
  assert.equal(calls[0].init.credentials, "include");
  assert.equal(response.page.page, 2);
});

test("backend errors remain explicit and are not replaced with local products", async (t) => {
  const { RestartApiClient, RestartApiError, cleanup } = loadApiClient();
  t.after(cleanup);
  const client = new RestartApiClient({
    fetchImpl: async () => new Response(JSON.stringify({
      code: "BACKEND_UNAVAILABLE",
      message: "Backend is unavailable.",
      requestId: "request-1",
    }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    }),
  });

  await assert.rejects(
    client.listProducts("JV"),
    (error) => error instanceof RestartApiError
      && error.status === 503
      && error.code === "BACKEND_UNAVAILABLE",
  );
});

test("factory reads preserve account scope and recalculation requires idempotency plus CSRF", async (t) => {
  const { RestartApiClient, cleanup } = loadApiClient();
  t.after(cleanup);
  const calls = [];
  const client = new RestartApiClient({
    csrfToken: () => "offline-csrf",
    fetchImpl: async (input, init) => {
      calls.push({ url: new URL(String(input)), init });
      return new Response(JSON.stringify({ items: [], count: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });

  await client.listFactories("XL", { status: "UNKNOWN", sortByNew: true });
  await client.recalculateFactory("factory/1", "XL", "factory-check-001");

  assert.equal(calls[0].url.pathname, "/restart/api/backend/factories");
  assert.equal(calls[0].url.searchParams.get("account"), "XL");
  assert.equal(calls[0].url.searchParams.get("status"), "UNKNOWN");
  assert.equal(calls[0].url.searchParams.get("sort_by_new"), "true");
  assert.equal(calls[0].init.method, "GET");
  assert.equal(calls[1].url.pathname, "/restart/api/backend/factories/factory%2F1/complete");
  assert.equal(calls[1].url.searchParams.get("account"), "XL");
  assert.equal(calls[1].init.method, "POST");
  assert.equal(calls[1].init.headers.get("Idempotency-Key"), "factory-check-001");
  assert.equal(calls[1].init.headers.get("X-CSRF-Token"), "offline-csrf");
});

test("selected product synchronization is account-scoped and sends version, CSRF, and idempotency", async (t) => {
  const { RestartApiClient, cleanup } = loadApiClient();
  t.after(cleanup);
  const calls = [];
  const client = new RestartApiClient({
    csrfToken: () => "offline-csrf",
    fetchImpl: async (input, init) => {
      calls.push({ url: new URL(String(input)), init });
      return new Response(JSON.stringify({ candidateEpisodeId: "episode-1", account: "XL" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });

  await client.syncProduct("episode/1", "XL", "sync-product-0001", 7);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url.pathname, "/restart/api/backend/products/episode%2F1/sync");
  assert.equal(calls[0].url.searchParams.get("account"), "XL");
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.headers.get("Idempotency-Key"), "sync-product-0001");
  assert.equal(calls[0].init.headers.get("X-CSRF-Token"), "offline-csrf");
  assert.deepEqual(JSON.parse(calls[0].init.body), { account: "XL", expectedVersion: 7 });
});

test("production Library reads are account-scoped and paginated through the authenticated BFF", async (t) => {
  const { RestartApiClient, cleanup } = loadApiClient();
  t.after(cleanup);
  const calls = [];
  const client = new RestartApiClient({
    fetchImpl: async (input, init) => {
      calls.push({ url: new URL(String(input)), init });
      return new Response(JSON.stringify({ items: [], page: { page: 2, pageSize: 25, totalItems: 0, totalPages: 0 } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });

  await client.listLibraryLinks("XL", { page: 2, pageSize: 25, query: "4006381333931", factoryId: "factory-1" });
  await client.listEanPool("XL", { page: 2, pageSize: 25, query: "400638" });

  assert.equal(calls[0].url.pathname, "/restart/api/backend/library/links");
  assert.equal(calls[0].url.searchParams.get("account"), "XL");
  assert.equal(calls[0].url.searchParams.get("page"), "2");
  assert.equal(calls[0].url.searchParams.get("pageSize"), "25");
  assert.equal(calls[0].url.searchParams.get("q"), "4006381333931");
  assert.equal(calls[0].url.searchParams.get("factory_id"), "factory-1");
  assert.equal(calls[1].url.pathname, "/restart/api/backend/ean-pool");
  assert.equal(calls[1].url.searchParams.get("account"), "XL");
  assert.equal(calls[1].url.searchParams.get("q"), "400638");
  assert.equal(calls.every((call) => call.init.method === "GET"), true);
});

test("baseline import and bounded discovery use account-scoped authenticated API contracts", async (t) => {
  const { RestartApiClient, cleanup } = loadApiClient();
  t.after(cleanup);
  const calls = [];
  const client = new RestartApiClient({
    csrfToken: () => "offline-csrf",
    fetchImpl: async (input, init) => {
      calls.push({ url: new URL(String(input)), init });
      return new Response(JSON.stringify({ accepted: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });

  await client.commitSourceImport("JV", "4066381333931\n4066381333948", "baseline-import-001");
  await client.createDiscoveryScan("XL", "manual-scan-0001", "2026-11-01T00:00:00Z");
  await client.confirmDiscoveryBaseline("JV", "baseline-run-1");
  await client.getDiscoverySchedule("XL");
  await client.updateDiscoverySchedule({
    account: "XL",
    enabled: false,
    timezone: "Europe/Berlin",
    weekday: 0,
    localTime: "03:00",
  });

  assert.deepEqual(calls.map(({ url }) => url.pathname), [
    "/restart/api/backend/imports/paste",
    "/restart/api/backend/scans",
    "/restart/api/backend/scans/baseline/confirm",
    "/restart/api/backend/scans/schedule/config",
    "/restart/api/backend/scans/schedule",
  ]);
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.headers.get("Idempotency-Key"), "baseline-import-001");
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    account: "JV",
    raw_text: "4066381333931\n4066381333948",
    filename: "paste_input.txt",
    dryRun: false,
    runMode: "BASELINE",
  });
  assert.equal(calls[1].url.searchParams.get("account"), null);
  assert.equal(calls[1].init.headers.get("Idempotency-Key"), "manual-scan-0001");
  assert.deepEqual(JSON.parse(calls[1].init.body), {
    account: "XL",
    fromDate: "2026-11-01T00:00:00Z",
  });
  assert.equal(calls[3].url.searchParams.get("account"), "XL");
  assert.equal(calls[4].init.method, "PUT");
  assert.equal(calls[4].init.headers.get("X-CSRF-Token"), "offline-csrf");
});

test("uploaded import previews and commits the selected file with account scope and idempotency", async (t) => {
  const { RestartApiClient, cleanup } = loadApiClient();
  t.after(cleanup);
  const calls = [];
  const client = new RestartApiClient({
    csrfToken: () => "offline-csrf",
    fetchImpl: async (input, init) => {
      calls.push({ url: new URL(String(input)), init });
      return new Response(JSON.stringify({ previewCompleted: true, counts: { valid: 1 }, runId: "run-1" }), {
        status: calls.length === 1 ? 200 : 202,
        headers: { "Content-Type": "application/json" },
      });
    },
  });
  const file = new File(["ean\n4260606059116"], "manual-list.csv", { type: "text/csv" });

  await client.previewSourceImportFile("XL", file);
  await client.commitSourceImportFile("XL", file, "manual-import-xl-001");
  await client.getImportRunRows("run-1", "XL", { page: 2, pageSize: 50 });

  assert.equal(calls.length, 3);
  assert.equal(calls[0].url.pathname, "/restart/api/backend/imports/upload");
  assert.equal(calls[0].url.searchParams.get("account"), "XL");
  assert.equal(calls[0].url.searchParams.get("dry_run"), "true");
  assert.ok(calls[0].init.body instanceof FormData);
  assert.equal(calls[0].init.body.get("file").name, "manual-list.csv");
  assert.equal(calls[0].init.headers.has("Content-Type"), false);
  assert.equal(calls[1].url.searchParams.get("account"), "XL");
  assert.equal(calls[1].url.searchParams.get("dry_run"), "false");
  assert.equal(calls[1].url.searchParams.get("runMode"), "BASELINE");
  assert.equal(calls[1].init.headers.get("Idempotency-Key"), "manual-import-xl-001");
  assert.equal(calls[1].init.headers.get("X-CSRF-Token"), "offline-csrf");
  assert.equal(calls[2].url.pathname, "/restart/api/backend/imports/runs/run-1/rows");
  assert.equal(calls[2].url.searchParams.get("account"), "XL");
  assert.equal(calls[2].url.searchParams.get("page"), "2");
  assert.equal(calls[2].url.searchParams.get("pageSize"), "50");
});

test("uploaded import validates and commits the original file through the same account scope", async (t) => {
  const { RestartApiClient, cleanup } = loadApiClient();
  t.after(cleanup);
  const calls = [];
  const client = new RestartApiClient({
    csrfToken: () => "offline-csrf",
    fetchImpl: async (input, init) => {
      calls.push({ url: new URL(String(input)), init });
      return new Response(JSON.stringify({ success: true, previewCompleted: true, runId: "run-1" }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      });
    },
  });
  const file = new File(["EAN\n4006381333931\n"], "pilot.csv", { type: "text/csv" });

  await client.previewSourceImportFile("XL", file);
  await client.commitSourceImportFile("XL", file, "file-import-0001");
  await client.getImportRunRows("run-1", "XL", { page: 1, pageSize: 100 });

  assert.equal(calls.map(({ url }) => url.pathname).join(","), [
    "/restart/api/backend/imports/upload",
    "/restart/api/backend/imports/upload",
    "/restart/api/backend/imports/runs/run-1/rows",
  ].join(","));
  assert.equal(calls[0].url.searchParams.get("account"), "XL");
  assert.equal(calls[0].url.searchParams.get("dry_run"), "true");
  assert.equal(calls[0].init.body.get("file").name, "pilot.csv");
  assert.equal(calls[1].url.searchParams.get("account"), "XL");
  assert.equal(calls[1].url.searchParams.get("dry_run"), "false");
  assert.equal(calls[1].url.searchParams.get("runMode"), "BASELINE");
  assert.equal(calls[1].init.headers.get("Idempotency-Key"), "file-import-0001");
  assert.equal(calls[1].init.body.get("file").name, "pilot.csv");
  assert.equal(calls[2].url.searchParams.get("account"), "XL");
  assert.equal(calls[2].url.searchParams.get("pageSize"), "100");
});

test("Drip Feed preview and controls use account scope, CSRF, and idempotency without publish calls", async (t) => {
  const { RestartApiClient, cleanup } = loadApiClient();
  t.after(cleanup);
  const calls = [];
  const client = new RestartApiClient({
    csrfToken: () => "offline-csrf",
    fetchImpl: async (input, init) => {
      calls.push({ url: new URL(String(input)), init });
      return new Response(JSON.stringify({ publicationEnabled: false }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });
  const schedule = {
    account: "XL", name: "XL · 3M", operationIds: ["operation-1"],
    startDate: "2026-11-10", dailyTime: "08:30", timezone: "Europe/Berlin", totalDays: 7,
  };

  await client.listDripBatches("XL", { page: 2, pageSize: 25, status: "PAUSED" });
  await client.listDripBatchItems("batch-1", "XL", { page: 3, pageSize: 10 });
  const preview = await client.previewDripSchedule(schedule);
  await client.createDripBatch(schedule, "drip-create-0001");
  await client.controlDripBatch("batch-1", "XL", "start", 1, "drip-start-0001");

  assert.equal(calls[0].url.pathname, "/restart/api/backend/batches");
  assert.equal(calls[0].url.searchParams.get("account"), "XL");
  assert.equal(calls[0].url.searchParams.get("page"), "2");
  assert.equal(calls[0].url.searchParams.get("status"), "PAUSED");
  assert.equal(calls[1].url.pathname, "/restart/api/backend/batches/batch-1/items");
  assert.equal(calls[1].url.searchParams.get("account"), "XL");
  assert.equal(calls[2].url.pathname, "/restart/api/backend/batches/preview");
  assert.equal(JSON.parse(calls[2].init.body).timezone, "Europe/Berlin");
  assert.equal(calls[2].init.headers.get("X-CSRF-Token"), "offline-csrf");
  assert.equal(preview.publicationEnabled, false);
  assert.equal(calls[3].init.headers.get("Idempotency-Key"), "drip-create-0001");
  assert.equal(calls[4].url.pathname, "/restart/api/backend/batches/batch-1/start");
  assert.equal(JSON.parse(calls[4].init.body).expectedVersion, 1);
  assert.equal(calls[4].init.headers.get("Idempotency-Key"), "drip-start-0001");
  assert.equal(calls.every((call) => !call.url.pathname.includes("/products/")), true);
});
