const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const source = fs.readFileSync(path.join(__dirname, '../src/lib/prices.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const target = { exports: {} };
new Function('exports', 'module', compiled)(target.exports, target);
const { parsePositivePrice, getAftercoolPrice } = target.exports;

test('German grouped prices must not be truncated to 1.40 EUR', () => {
  for (const value of ['1.399,99 €', '1\u202f399,99 EUR', '1399.99', '1,399.99', 1399.99]) {
    assert.equal(parsePositivePrice(value), '1399.99');
  }
});
test('reject nonfinite and partially valid prices', () => {
  for (const value of [Infinity, NaN, 'Infinity', '12garbage', '1_000', true, '', null, -1, 0]) {
    assert.equal(parsePositivePrice(value), '');
  }
});
test('only Startpreis is used, regardless of SofortkaufenPreis', () => {
  assert.equal(getAftercoolPrice({ Startpreis: '2149,99', SofortkaufenPreis: '0,0000' }), '2149.99');
  assert.equal(getAftercoolPrice({ Startpreis: '2149,99', SofortkaufenPreis: '1999,99' }), '2149.99');
  for (const start of [undefined, '', '0,0000', 'invalid']) {
    assert.equal(getAftercoolPrice({ Startpreis: start, SofortkaufenPreis: '1999,99' }), '');
  }
  assert.equal(getAftercoolPrice(), '');
});
