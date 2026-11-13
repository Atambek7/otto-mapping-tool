import assert from "node:assert/strict";
import test from "node:test";
import { buildProxyHeaders, readProxyBody } from "../features/auth/proxy-transport.ts";

test("proxy preserves multipart bytes and boundary content type", async () => {
  const sourceBytes = new Uint8Array([0, 255, 14, 128, 10]);
  const form = new FormData();
  form.set("account", "JV");
  form.set("file", new Blob([sourceBytes]), "binary.dat");
  const request = new Request("http://restart.local/restart/api/backend/imports/upload", {
    method: "POST",
    headers: { "Idempotency-Key": "binary-import-0001", Origin: "http://restart.local" },
    body: form,
  });

  const body = await readProxyBody(request.method, request);
  assert.ok(body instanceof ArrayBuffer);
  const headers = buildProxyHeaders(request.headers, "signed-test-token", "request-0001");
  assert.match(headers.get("content-type") ?? "", /^multipart\/form-data; boundary=/);
  assert.equal(headers.get("idempotency-key"), "binary-import-0001");

  const forwarded = new Request("http://restart.local/backend", { method: "POST", headers, body });
  const parsed = await forwarded.formData();
  const file = parsed.get("file");
  assert.ok(file instanceof File);
  assert.deepEqual(new Uint8Array(await file.arrayBuffer()), sourceBytes);
  assert.equal(parsed.get("account"), "JV");
});

test("proxy forwards only allowlisted browser headers and replaces authorization", () => {
  const source = new Headers({
    Accept: "application/json",
    Authorization: "Bearer forged-browser-value",
    "Content-Type": "application/json",
    "Idempotency-Key": "retry-key-0001",
    "X-Actor-ID": "forged-actor",
    "X-Account": "XL",
  });
  const headers = buildProxyHeaders(source, "signed-test-token", "request-0002");
  assert.equal(headers.get("authorization"), "Bearer signed-test-token");
  assert.equal(headers.get("idempotency-key"), "retry-key-0001");
  assert.equal(headers.get("x-actor-id"), null);
  assert.equal(headers.get("x-account"), null);
  assert.equal(headers.get("x-request-id"), "request-0002");
});

test("safe reads do not consume request bodies", async () => {
  let readCount = 0;
  const request = { arrayBuffer: async () => { readCount += 1; return new ArrayBuffer(0); } };
  assert.equal(await readProxyBody("GET", request), undefined);
  assert.equal(await readProxyBody("HEAD", request), undefined);
  assert.equal(readCount, 0);
});
