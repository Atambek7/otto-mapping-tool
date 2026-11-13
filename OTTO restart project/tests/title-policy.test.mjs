import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import ts from "typescript";

function loadPolicy() {
  const directory = mkdtempSync(path.join(tmpdir(), "otto-restart-title-policy-"));
  const source = readFileSync(new URL("../features/catalogs/title-policy.ts", import.meta.url), "utf8");
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const modulePath = path.join(directory, "title-policy.cjs");
  writeFileSync(modulePath, output);
  const requireFromDirectory = createRequire(modulePath);
  return { ...requireFromDirectory("./title-policy.cjs"), cleanup: () => rmSync(directory, { recursive: true, force: true }) };
}

test("frontend code-point title length matches server semantics at the 70 boundary", (t) => {
  const policy = loadPolicy();
  t.after(policy.cleanup);
  assert.equal(policy.titleCodePointLength("ä".repeat(69)), 69);
  assert.equal(policy.titleCodePointLength("ä".repeat(70)), 70);
  assert.equal(policy.titleCodePointLength("😀".repeat(70)), 70);
  assert.equal(policy.titleCodePointLength("😀".repeat(71)), 71);
});

test("conservative title proposal keeps identifiers and refuses unsafe truncation", (t) => {
  const policy = loadPolicy();
  t.after(policy.cleanup);
  const result = policy.safelyCompactSourceTitle("Chesterfield Design Luxus Polster Sofa Couch Sitz Garnitur Leder Textil Neu #bl");
  assert.ok(result);
  assert.ok(policy.titleCodePointLength(result) <= 70);
  assert.match(result, /#bl/);
  assert.equal(policy.safelyCompactSourceTitle(`Model-${"9".repeat(80)}`), null);
});
