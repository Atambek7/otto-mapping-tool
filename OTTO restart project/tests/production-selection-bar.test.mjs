import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const source = readFileSync(new URL("../components/catalogs/product-selection-bar.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022,
} }).outputText;
const loaded = { exports: {} };
vm.runInNewContext(compiled, {
  module: loaded, exports: loaded.exports, require: createRequire(new URL("../package.json", import.meta.url)),
  setInterval, clearInterval, setTimeout,
});

test("production selection exposes all real bulk actions instead of hiding them behind demoMode", () => {
  const html = renderToStaticMarkup(createElement(loaded.exports.ProductSelectionBar, {
    demoMode: false, selectedCount: 2, totalCount: 3,
    onClearSelection() {}, onProductionAction() {}, onSyncFromOtto() {},
    onApplyAiCategories() {}, onApplyAiAttributes() {},
  }));
  for (const title of ["Новый EAN", "Defaults", "ИИ-название", "Синхронизация", "AI Категории", "AI Атрибуты"]) {
    assert.ok(html.includes(title), `Missing production bulk control: ${title}`);
  }
  assert.doesNotMatch(html, /DEMO:/);
});
