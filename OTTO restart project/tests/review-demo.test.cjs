// Compile features/review/demo-service.ts to a temporary directory before running.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { DemoReviewService, ReviewProblem } = require(process.env.OTTORESTART_DEMO_TEST_MODULE);
function prepare(service, account = 'JV', id = 'jv-demo-1') {
  const item = service.get(account, id);
  return service.act(account, id, item.version, 'prepare');
}
function approve(service, item) {
  item = service.act(item.account, item.id, item.version, 'validate');
  return service.act(item.account, item.id, item.version, 'approve');
}
const patchOf = (item) => ({ title: item.prepared.title, category: item.prepared.category, attributes: { ...item.prepared.attributes }, images: [...item.prepared.images] });

test('prices use integer cents and account-specific discounts; retries keep one reservation', () => {
  const service = new DemoReviewService();
  for (const account of ['JV', 'XL']) {
    const id = `${account.toLowerCase()}-demo-1`;
    const first = prepare(service, account, id);
    const retry = service.act(account, id, first.version, 'prepare');
    assert.equal(first.price.discountCents, account === 'JV' ? 1000 : 1500);
    assert.equal(first.price.newCents, account === 'JV' ? 9000 : 9500);
    assert.deepEqual(first.identity, retry.identity);
    assert.equal(first.price.newCents, retry.price.newCents);
    assert.equal(service.list(account).pool.reserved, 1);
    assert.equal(first.publishAllowed, false);
    assert.match(first.identity.newEan, /^DEMO-/);
  }
});
test('missing, invalid prices and restricted marketplace never reserve identifiers', () => {
  const service = new DemoReviewService();
  for (const [id, status] of [['jv-demo-2', 'PRICE_UNAVAILABLE'], ['jv-demo-4', 'BLOCKED_BY_OTTO'], ['jv-demo-5', 'PRICE_REVIEW_REQUIRED']]) {
    const item = prepare(service, 'JV', id);
    assert.equal(item.status, status);
    assert.equal(item.identity, null);
    assert.equal(item.approval, null);
  }
  assert.equal(service.list('JV').pool.reserved, 0);
});
test('server denies stale writes and cross-account access', () => {
  const service = new DemoReviewService();
  const item = prepare(service);
  assert.throws(() => service.act('JV', item.id, 1, 'validate'), (error) => error instanceof ReviewProblem && error.status === 409);
  assert.throws(() => service.get('XL', item.id), (error) => error.status === 404);
  assert.throws(() => service.act('XL', item.id, item.version, 'approve'), (error) => error.status === 404);
  assert.equal(service.list('XL').pool.reserved, 0);
});
test('approval requires validation and keeps an immutable versioned payload', () => {
  const service = new DemoReviewService();
  let item = prepare(service);
  assert.throws(() => service.act('JV', item.id, item.version, 'approve'), (error) => error.status === 409);
  item = approve(service, item);
  assert.equal(item.status, 'APPROVED');
  assert.equal(item.approval.payloadVersion, item.payloadVersion);
  assert.equal(item.approval.priceSnapshotVersion, item.price.snapshotVersion);
  assert.equal(item.approval.payloadHash.length, 64);
  const payload = service.approvedPayload('JV', item.id);
  payload.content.title = 'mutated clone';
  assert.notEqual(service.approvedPayload('JV', item.id).content.title, payload.content.title);
  assert.equal(service.approvedPayload('JV', item.id).demoOnly, true);
});
test('essential edits invalidate approval without releasing or consuming the reservation', () => {
  const service = new DemoReviewService();
  let item = approve(service, prepare(service));
  const previousPayloadVersion = item.payloadVersion;
  const patch = patchOf(item); patch.title = 'Neuer Titel';
  item = service.patch('JV', item.id, item.version, patch);
  assert.equal(item.approval, null);
  assert.equal(service.approvedPayload('JV', item.id), null);
  assert.ok(item.payloadVersion > previousPayloadVersion);
  assert.equal(service.list('JV').pool.reserved, 1);
  assert.equal(item.status, 'CONTENT_PREPARED');
});
test('price is re-read on approval; changed base requires a new validation and approval', () => {
  const service = new DemoReviewService();
  let item = approve(service, prepare(service));
  item = service.act('JV', item.id, item.version, 'simulate-price-change');
  item = service.act('JV', item.id, item.version, 'approve');
  assert.equal(item.status, 'PRICE_CHANGED_REAPPROVAL_REQUIRED');
  assert.equal(item.approval, null);
  assert.equal(item.price.baseCents, 11000);
  assert.equal(item.price.newCents, 10000);
  item = approve(service, item);
  assert.equal(item.status, 'APPROVED');
  assert.equal(service.approvedPayload('JV', item.id).price.newCents, 10000);
});
test('validation reports missing attributes and images; a correction unblocks demo approval', () => {
  const service = new DemoReviewService();
  let item = prepare(service, 'JV', 'jv-demo-3');
  item = service.act('JV', item.id, item.version, 'validate');
  assert.ok(item.issues.some((issue) => issue.field === 'material'));
  const patch = patchOf(item); patch.attributes.material = 'Holz'; patch.images = [];
  item = service.patch('JV', item.id, item.version, patch);
  item = service.act('JV', item.id, item.version, 'validate');
  assert.ok(item.issues.some((issue) => issue.code === 'IMAGE_MISSING'));
  patch.images = ['chair'];
  item = service.patch('JV', item.id, item.version, patch);
  item = approve(service, item);
  assert.equal(item.status, 'APPROVED');
});
test('client cannot inject price, identifiers, product-reference mode or remote images', () => {
  const service = new DemoReviewService();
  const item = prepare(service);
  for (const patch of [{ ...patchOf(item), price: 1 }, { ...patchOf(item), newEan: 'real-ean' }, { ...patchOf(item), mode: 'REUSE_OLD_PRODUCT_REFERENCE' }, { ...patchOf(item), images: ['https://example.com/image.jpg'] }]) {
    assert.throws(() => service.patch('JV', item.id, item.version, patch), (error) => error.status === 422);
  }
  assert.equal(service.get('JV', item.id).version, item.version);
});
test('independent demo sessions and accounts do not share mutations', () => {
  const first = new DemoReviewService(); const second = new DemoReviewService();
  prepare(first);
  assert.equal(second.get('JV', 'jv-demo-1').status, 'IMPORTED');
  assert.equal(second.list('JV').pool.reserved, 0);
  assert.equal(first.list('XL').pool.reserved, 0);
});
test('pagination, search and status filters do not return another account', () => {
  const service = new DemoReviewService();
  assert.equal(service.list('JV', '', '', 1, 2).items.length, 2);
  assert.equal(service.list('JV', '', '', 1, 2).page.totalItems, 5);
  assert.equal(service.list('JV', 'DEMO-OLD-XL').items.length, 0);
  prepare(service);
  assert.equal(service.list('JV', '', 'CONTENT_PREPARED').items.length, 1);
});
