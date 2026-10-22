"""Isolated regression checks. All upstream calls are mocked; no live accounts."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace
import unittest
from datetime import datetime, timezone
from contextlib import ExitStack
from unittest.mock import AsyncMock, Mock, patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from services import aftercool_service as ac
from services import okb_sync
from tasks_availability_sync import run_availability_sync, fetch_aftercool_products_for_factory


def item(ean='DEMO-PRICE', start='2.149,99', sofort='0,0000'):
    return {'ean': ean, 'row': {'EAN': ean, 'Artikelbeschreibung': 'Sessel',
            'Startpreis': start, 'SofortkaufenPreis': sofort, 'Menge': '0',
            'CustomItemSpecifics': '<ItemSpecifics><NameValueList><Name>Produktart</Name><Value>Sessel</Value></NameValueList><NameValueList><Name>Farbe</Name><Value>Weiß</Value></NameValueList></ItemSpecifics>'}}


def response(items, total=None, code=200):
    data = {'items': items}
    if total is not None:
        data['total'] = total
    return httpx.Response(code, json=data)


class ParsingTests(unittest.TestCase):
    def test_price_formats_and_invalid_values(self):
        for value in ('1.399,99 €', '1\u202f399,99 EUR', '1399.99', '1,399.99', 1399.99, Decimal('1399.99')):
            with self.subTest(value=value):
                self.assertEqual(ac.parse_price(value), Decimal('1399.99'))
        for value in (None, '', 0, -1, True, float('inf'), 'Infinity', 'NaN', '12garbage', '1_000'):
            with self.subTest(value=value):
                self.assertIsNone(ac.parse_price(value))

    def test_startpreis_only_and_identity(self):
        self.assertEqual(ac.parse_aftercool_product(item(sofort='1.999,99')).price, Decimal('2149.99'))
        self.assertEqual(ac.parse_aftercool_product(item()).price, Decimal('2149.99'))
        for start in ('', '0,0000', 'invalid'):
            with self.subTest(start=start):
                product = ac.parse_aftercool_product(item(start=start, sofort='1999.99'))
                self.assertIsNone(product.price)
                self.assertIsNone(product.price_af)
        bad = item()
        bad['row']['EAN'] = 'OTHER'
        self.assertIsNone(ac.parse_aftercool_product(bad))


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_page_price_and_max_500(self):
        first = [item(str(i)) for i in range(500)]
        request = AsyncMock(side_effect=[response(first, 501), response([item('TARGET')], 501)])
        with patch.object(ac, 'request_aftercool', request):
            pages = [page async for page in ac.iter_products('DEMO-F', page_size=5000)]
        self.assertEqual(pages[1][0].price, Decimal('2149.99'))
        self.assertEqual([c.kwargs['params']['offset'] for c in request.call_args_list], [0, 500])
        self.assertTrue(all(c.kwargs['params']['limit'] == 500 for c in request.call_args_list))

    async def test_incomplete_page_and_cap_are_errors(self):
        cases = [([response([item()], 2), response([], 2)], 10),
                 ([response([item()], 2), response([], code=503)], 10),
                 ([response([item()], 2)], 1)]
        for responses, max_pages in cases:
            with self.subTest(max_pages=max_pages), patch.object(ac, 'request_aftercool', AsyncMock(side_effect=responses)):
                with self.assertRaises(RuntimeError):
                    _ = [p async for p in ac.iter_products('DEMO-F', page_size=1, max_pages=max_pages)]

    async def test_force_sync_reports_partial_after_failure(self):
        async def pages(**kwargs):
            yield [ac.parse_aftercool_product(item())]
            raise RuntimeError('page failed')
        with patch.object(ac, 'resolve_aftercool_account', return_value='JV'), \
             patch.object(ac, 'iter_products', pages), \
             patch.object(ac, 'sync_products_to_db', return_value={'created': 1, 'updated': 0}), \
             patch.object(ac, 'invalidate_products_cache', AsyncMock()):
            result = await ac.force_sync_factory_metadata('DEMO-F')
        self.assertEqual(result['status'], 'partial_error')
        self.assertEqual(result['synced'], 0)

    async def test_exact_ean_lookup_and_source_account(self):
        request = AsyncMock(return_value=response([item('OTHER'), item()]))
        with patch.object(ac, 'request_aftercool', request), \
             patch.object(ac, 'resolve_aftercool_account', return_value='JV'):
            product = await ac.fetch_aftercool_product('DEMO-PRICE', 'DEMO-F', 'XL')
        self.assertEqual(product.ean, 'DEMO-PRICE')
        self.assertEqual(request.call_args.kwargs['params']['account'], 'JV')
        with patch.object(ac, 'request_aftercool', AsyncMock(return_value=response([item('OTHER')]))), \
             patch.object(ac, 'resolve_aftercool_account', return_value='JV'):
            self.assertIsNone(await ac.fetch_aftercool_product('DEMO-PRICE', 'DEMO-F', 'JV'))

    async def test_exact_lookup_rejects_conflicting_duplicate_prices(self):
        with patch.object(ac, 'request_aftercool', AsyncMock(return_value=response([item(start='839,99'), item(start='799,99')]))), \
             patch.object(ac, 'resolve_aftercool_account', return_value='JV'):
            with self.assertRaisesRegex(ValueError, 'Conflicting'):
                await ac.fetch_aftercool_product('DEMO-PRICE', 'DEMO-F', 'JV')

    async def test_cross_page_conflicts_are_known_before_any_metadata_write(self):
        async def pages(**kwargs):
            yield [ac.parse_aftercool_product(item(start='839,99'))]
            yield [ac.parse_aftercool_product(item(start='799,99'))]
        with patch.object(ac, 'resolve_aftercool_account', return_value='JV'), \
             patch.object(ac, 'iter_products', pages), \
             patch.object(ac, 'sync_products_to_db', return_value={'updated': 1}) as sync, \
             patch.object(ac, 'invalidate_products_cache', AsyncMock()):
            result = await ac.force_sync_factory_metadata('DEMO-F')
        self.assertEqual(result['status'], 'partial_error')
        self.assertEqual(result['price_conflicts'], ['DEMO-PRICE'])
        self.assertTrue(all('DEMO-PRICE' in call.args[-1] for call in sync.call_args_list))

    async def test_availability_detects_duplicate_even_after_target_was_found(self):
        request = AsyncMock(side_effect=[response([item(start='839,99')], 2), response([item(start='799,99')], 2)])
        with patch.object(ac, 'request_aftercool', request), patch.object(ac, 'resolve_aftercool_account', return_value='JV'):
            result = await fetch_aftercool_products_for_factory('DEMO-F', 'JV', target_eans={'DEMO-PRICE'})
        self.assertTrue(result['DEMO-PRICE']['price_conflict'])
        self.assertEqual(request.await_count, 2)

    async def test_explicit_sync_prefers_fresh_aftercool_not_okb_or_browser(self):
        okb = {'sku': 'DEMO-PRICE', 'ean': 'DEMO-PRICE', 'productDescription': {
            'category': 'Polstergarnitur', 'attributes': [{'name': 'Farbe', 'values': ['Rot']}]}}
        with patch.object(okb_sync, 'fetch_product_from_okb', AsyncMock(return_value=okb)), \
             patch.object(okb_sync, 'fetch_product_from_aftercool', AsyncMock(return_value=item()['row'])):
            result = await okb_sync.sync_product_data('DEMO-PRICE', 'DEMO-F', row=item(start='1')['row'])
        self.assertEqual(result['category'], 'Sessel')
        self.assertEqual(result['category_source'], 'aftercool')
        self.assertEqual(result['attributes']['Farbe'], 'Weiß')
        self.assertEqual(result['metadata']['price'], '2149.99')

    async def test_source_failure_does_not_apply_cached_browser_price(self):
        with patch.object(okb_sync, 'fetch_product_from_okb', AsyncMock(return_value={})), \
             patch.object(okb_sync, 'fetch_product_from_aftercool', AsyncMock(side_effect=RuntimeError('offline'))):
            result = await okb_sync.sync_product_data('DEMO-PRICE', 'DEMO-F', row=item(start='1')['row'])
        self.assertEqual(result['status'], 'error')
        self.assertNotIn('metadata', result)

    async def test_availability_fetch_reads_beyond_first_page(self):
        request = AsyncMock(side_effect=[response([item(str(i)) for i in range(500)], 501), response([item()], 501)])
        with patch.object(ac, 'request_aftercool', request), patch.object(ac, 'resolve_aftercool_account', return_value='JV'):
            result = await fetch_aftercool_products_for_factory('DEMO-F', 'JV', target_eans={'DEMO-PRICE'})
        self.assertEqual(list(result), ['DEMO-PRICE'])
        self.assertEqual(request.await_count, 2)

    async def test_metadata_response_waits_for_db_sync_and_propagates_failure(self):
        from routers.aftercool import get_products
        cached = {'items': [item()]}
        with patch('routers.aftercool.resolve_aftercool_account', return_value='JV'), \
             patch('routers.aftercool.get_cached_json', AsyncMock(return_value=cached)), \
             patch('routers.aftercool.sync_products_to_db', side_effect=RuntimeError('DB failure')) as sync:
            with self.assertRaisesRegex(RuntimeError, 'DB failure'):
                await get_products(factory_id='DEMO-F', account='JV', include_row=1, current_user=None, db=Mock())
            sync.assert_called_once()


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        for table in (models.ProductMapping, models.ProductAttribute, models.AccountSettings, models.AvailabilitySyncLog, models.CategoryCache, models.PublishPlan, models.PublishQueueItem):
            table.__table__.create(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.session()
        self.jv = models.ProductMapping(product_ean='DEMO-PRICE', account_name='JV', factory_id='DEMO-F',
                                        category_id='25785', status='Published', price='1399.99', price_af='1399.99')
        self.xl = models.ProductMapping(product_ean='DEMO-PRICE', account_name='XL', factory_id='DEMO-F',
                                        category_id='25785', status='Ready', price='777')
        self.db.add_all([self.jv, self.xl, models.AccountSettings(account_name='JV', otto_client_id='fixture', otto_client_secret='fixture')])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def sync(self, **kwargs):
        with patch('tasks_availability_sync.fetch_aftercool_products_for_factory', AsyncMock(return_value={'DEMO-PRICE': item()})), \
             patch('tasks_availability_sync.OttoMarketClient', autospec=True) as client:
            client.return_value.update_quantities.return_value = {'status_code': 200}
            result = run_availability_sync(db=self.db, batch_delay_sec=0, **kwargs)
        return result, client

    def test_availability_updates_local_price_and_stock_not_otto_price(self):
        result, client = self.sync()
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['price_updated'], 1)
        self.assertEqual(self.jv.price, '2149.99')
        self.assertEqual(self.jv.price_af, '2149.99')
        self.assertEqual(self.xl.price, '777')
        client.assert_called_once_with('fixture', 'fixture')
        client.return_value.update_quantities.assert_called_once_with([{'sku': 'DEMO-PRICE', 'quantity': 0}])
        client.return_value.publish_products.assert_not_called()
        self.assertEqual(self.db.query(models.AvailabilitySyncLog).count(), 1)

    def test_manual_price_and_missing_source_price_preserved(self):
        self.jv.price = '999.99'
        self.jv.has_manual_price = True
        self.db.commit()
        result, _ = self.sync()
        self.assertEqual(self.jv.price, '999.99')
        self.assertEqual(self.jv.price_af, '2149.99')
        self.assertEqual(result['price_updated'], 0)

    def test_dry_run_does_not_write_even_if_caller_commits(self):
        result, client = self.sync(dry_run=True)
        self.db.commit()
        self.db.expire_all()
        self.assertEqual(self.jv.price, '1399.99')
        self.assertEqual(self.db.query(models.AvailabilitySyncLog).count(), 0)
        client.assert_not_called()
        self.assertEqual(result['price_updated'], 0)
        self.assertEqual(result['price_changes_detected'], 1)

    def test_missing_or_failed_source_never_sends_default_stock(self):
        for source in ({}, RuntimeError('second page failed')):
            fetch = AsyncMock(side_effect=source) if isinstance(source, Exception) else AsyncMock(return_value=source)
            with patch('tasks_availability_sync.fetch_aftercool_products_for_factory', fetch), \
                 patch('tasks_availability_sync.OttoMarketClient', autospec=True) as client:
                result = run_availability_sync(db=self.db, batch_delay_sec=0)
            client.return_value.update_quantities.assert_not_called()
            self.assertTrue(result['errors'])
            self.assertEqual(self.jv.price, '1399.99')

    def test_bulk_sync_keeps_selected_jv_and_saves_prices(self):
        from routers.mapping import sync_okb_bulk, SyncOkbBulkRequest
        result = {'ean': 'DEMO-PRICE', 'status': 'success', 'category_id': '25545',
                  'attributes': {'Farbe': 'Weiß'}, 'metadata': {'price': '2149.99', 'price_af': '2149.99'}}
        with patch.object(okb_sync, 'sync_product_data', AsyncMock(return_value=result)) as sync:
            asyncio.run(sync_okb_bulk(SyncOkbBulkRequest(product_eans=['DEMO-PRICE'], factory_id='DEMO-F', account='JV'), db=self.db))
        self.assertEqual(sync.call_args.args[2], 'JV')
        self.assertEqual(self.jv.price, '2149.99')
        self.assertEqual(self.jv.category_id, '25545')
        self.assertEqual(self.jv.status, 'Published')
        self.assertEqual(self.xl.price, '777')
        self.assertEqual(self.xl.category_id, '25785')
        self.assertTrue(self.jv.missing_attributes)

    def run_publisher(self, source_items, partial=False):
        from services.executors.otto_api import OttoApiExecutor
        self.jv.product_name = 'Sessel'
        self.jv.image_url = 'https://fixture.invalid/image.jpg'
        plan = models.PublishPlan(factory_id='DEMO-F', account_name='JV', total_products=1, total_days=1)
        self.db.add(plan)
        self.db.flush()
        entry = models.PublishQueueItem(plan_id=plan.id, product_ean='DEMO-PRICE', account_name='JV',
            category_id='25785', scheduled_day=1, scheduled_date=datetime.now(timezone.utc), status='pending', attempts=0)
        self.db.add(entry)
        self.db.commit()
        context = AsyncMock()
        context.__aenter__.return_value.get = (
            AsyncMock(side_effect=[response([item()] * 500, 501), response([], code=503)])
            if partial else AsyncMock(return_value=response(source_items))
        )
        with ExitStack() as stack:
            stack.enter_context(patch('routers.aftercool.get_aftercool_session', AsyncMock(return_value='fixture')))
            stack.enter_context(patch.object(ac, 'resolve_aftercool_account', return_value='JV'))
            stack.enter_context(patch.object(ac, 'fetch_aftercool_product', AsyncMock(return_value=None)))
            stack.enter_context(patch('httpx.AsyncClient', return_value=context))
            stack.enter_context(patch('services.categories_service.resolve_category_names', return_value={'25785': 'Polstergarnitur'}))
            stack.enter_context(patch('services.image_processor.ensure_image_dimensions_url', AsyncMock(return_value=self.jv.image_url)))
            formatter = stack.enter_context(patch('services.executors.otto_api.format_otto_product', return_value={'sku': 'DEMO-PRICE'}))
            client = stack.enter_context(patch('services.executors.otto_api.OttoMarketClient', autospec=True))
            client.return_value.publish_products.return_value = {'status_code': 202, 'response': {'links': [{'rel': 'self', 'href': '/fixture-task'}]}}
            result = OttoApiExecutor().publish_batch(plan, [entry], self.db)
        return result, entry, client, formatter

    def test_publisher_blocks_conflict_and_does_not_send_old_automatic_price(self):
        for source in ([], [item(start='839,99'), item(start='799,99')]):
            result, entry, client, formatter = self.run_publisher(source)
            self.assertEqual(result['failed'], 1)
            client.return_value.publish_products.assert_not_called()
            formatter.assert_not_called()
            self.assertEqual(self.jv.price, '1399.99')
            self.db.delete(entry)
            self.db.commit()

    def test_publisher_rejects_price_from_incomplete_catalog(self):
        result, entry, client, formatter = self.run_publisher([item()], partial=True)
        self.assertEqual(result['failed'], 1)
        client.return_value.publish_products.assert_not_called()
        formatter.assert_not_called()

    def test_publisher_uses_fresh_aftercool_price(self):
        result, entry, client, formatter = self.run_publisher([item()])
        self.assertEqual(result['processing'], 1)
        self.assertEqual(formatter.call_args.args[2]['price'], '2149.99')
        client.return_value.publish_products.assert_called_once()

    def test_publisher_preserves_manual_price_even_with_conflicting_source_prices(self):
        self.jv.has_manual_price = True
        self.db.commit()
        result, entry, client, formatter = self.run_publisher([item(start='839,99'), item(start='799,99')])
        self.assertEqual(result['processing'], 1)
        self.assertEqual(formatter.call_args.args[2]['price'], '1399.99')

    def test_duplicate_catalog_prices_never_overwrite_saved_price(self):
        with patch.object(ac, 'SessionLocal', self.session):
            result = ac.sync_products_to_db('DEMO-F', 'JV', [item(start='839,99'), item(start='799,99')])
        self.db.expire_all()
        self.assertEqual(self.jv.price, '1399.99')
        self.assertEqual(result['price_conflicts'], ['DEMO-PRICE'])

    def test_availability_conflict_does_not_send_otto_update_or_change_price(self):
        with patch('tasks_availability_sync.fetch_aftercool_products_for_factory', AsyncMock(return_value={'DEMO-PRICE': {'price_conflict': True}})), \
             patch('tasks_availability_sync.OttoMarketClient', autospec=True) as client:
            result = run_availability_sync(db=self.db, batch_delay_sec=0)
        client.return_value.update_quantities.assert_not_called()
        self.assertEqual(self.jv.price, '1399.99')
        self.assertEqual(result['skipped'], 1)
        self.assertIn('Conflicting', result['errors'][0])

    def test_catalog_refresh_preserves_ai_title_and_manual_price(self):
        self.jv.has_ai_title = True
        self.jv.product_name = 'AI title'
        self.jv.has_manual_price = True
        self.db.commit()
        with patch.object(ac, 'SessionLocal', self.session):
            ac.sync_products_to_db('DEMO-F', 'JV', [item()])
        self.db.expire_all()
        self.assertEqual(self.jv.product_name, 'AI title')
        self.assertEqual(self.jv.price, '1399.99')
        self.assertEqual(self.jv.price_af, '2149.99')


if __name__ == '__main__':
    unittest.main()
