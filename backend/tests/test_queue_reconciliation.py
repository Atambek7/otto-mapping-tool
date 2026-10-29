import json
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from services.aftercool_service import apply_aftercool_prices, parse_aftercool_product
from services import queue_reconciliation as reconciliation


class PricePriorityTests(unittest.TestCase):
    def test_aftercool_fills_missing_price_and_parses_start_price(self):
        product = parse_aftercool_product({'ean': 'DEMO-PRICE', 'row': {
            'SofortkaufenPreis': '0,0000', 'Startpreis': '1.459,99',
        }})
        mapping = SimpleNamespace(price=None, price_af=None, has_manual_price=False)
        apply_aftercool_prices(mapping, product.price, product.price_af)
        self.assertEqual(mapping.price, '1459.99')
        self.assertEqual(mapping.price_af, '1459.99')

    def test_manual_price_wins_and_missing_aftercool_does_not_erase(self):
        mapping = SimpleNamespace(price='1234.56', price_af=None, has_manual_price=True)
        apply_aftercool_prices(mapping, 999, 888)
        self.assertEqual(mapping.price, '1234.56')
        self.assertEqual(mapping.price_af, '888')
        apply_aftercool_prices(mapping, None, None)
        self.assertEqual(mapping.price, '1234.56')
        self.assertEqual(mapping.price_af, '888')


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        # Only the three relevant tables, in an isolated in-memory database.
        models.PublishPlan.__table__.create(self.engine)
        models.ProductMapping.__table__.create(self.engine)
        models.PublishQueueItem.__table__.create(self.engine)
        models.CategoryCache.__table__.create(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.cache = Mock()
        self.cache.mget.return_value = [None]
        self.cache_patch = patch.object(reconciliation, 'redis_client', self.cache)
        self.cache_patch.start()
        self.plan = models.PublishPlan(id=1, factory_id='DEMO-FACTORY', account_name='XL', total_products=1, total_days=1)
        self.mapping = models.ProductMapping(product_ean='DEMO-PRICE', factory_id='DEMO-FACTORY', account_name='XL', price=None, price_af=None, status='Error')
        self.item = models.PublishQueueItem(id=1, plan_id=1, product_ean='DEMO-PRICE', account_name='XL', category_id='demo', scheduled_day=1, scheduled_date=datetime.now(timezone.utc), status='failed', attempts=5, error_message='missing price')
        self.db.add_all([self.plan, self.mapping, self.item])
        self.db.commit()
        self.report = {
            'version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
            'plan_id': 1, 'account': 'XL', 'factory_id': 'DEMO-FACTORY',
            'items': [{'id': 1, 'ean': 'DEMO-PRICE',
                       'fingerprint': reconciliation.attempt_fingerprint(self.item),
                       'aftercool': {'price': '999.99', 'price_af': '888.88'},
                       'otto': {'exists': True, 'checked_at': datetime.now(timezone.utc).isoformat()}}],
        }

    def tearDown(self):
        self.cache_patch.stop()
        self.db.close()
        self.engine.dispose()

    def test_repair_changes_only_local_prices_not_attempt_or_publish_state(self):
        with patch.object(reconciliation.requests, 'get', side_effect=AssertionError('No network allowed')):
            result = reconciliation.apply_report(self.db, self.report)
        self.assertEqual(result['prices_recovered'], 1)
        self.assertEqual(self.mapping.price, '999.99')
        self.assertEqual(self.item.status, models.QueueItemStatus.FAILED)
        self.assertEqual(self.item.attempts, 5)
        self.assertEqual(self.item.error_message, 'missing price')
        self.assertIsNone(self.item.published_at)
        self.assertEqual(self.mapping.status, 'Error')
        self.assertIsNone(self.mapping.published_at)
        stored = self.cache.mset.call_args.args[0]
        self.cache.mget.return_value = list(stored.values())
        observations = reconciliation.load_observations('XL', [self.item])
        self.assertIn(1, observations)
        self.assertTrue(observations[1]['exists'])

    def test_queue_api_exposes_remote_fact_and_retry_skips_existing_card(self):
        from routers.publish_pipeline import get_plan_items, retry_failed_items
        reconciliation.apply_report(self.db, self.report)
        stored = self.cache.mset.call_args.args[0]
        self.cache.mget.return_value = list(stored.values())
        response = get_plan_items(1, status=None, db=self.db)
        self.assertTrue(response['items'][0]['otto_product_exists'])
        self.assertEqual(response['items'][0]['status'], 'failed')
        self.assertEqual(response['items'][0]['price'], '999.99')
        self.assertEqual(retry_failed_items(1, db=self.db)['retried_count'], 0)
        self.assertEqual(self.item.status, models.QueueItemStatus.FAILED)

    def test_failed_guard_on_later_item_rolls_back_entire_repair(self):
        bad_row = dict(self.report['items'][0], id=999)
        self.report['items'].append(bad_row)
        with self.assertRaises(Exception):
            reconciliation.apply_report(self.db, self.report)
        self.assertIsNone(self.mapping.price)
        self.cache.mset.assert_not_called()

    def test_manual_price_edited_after_inspection_survives_repair(self):
        self.mapping.has_manual_price = True
        self.mapping.price = '1234.56'
        self.db.commit()
        reconciliation.apply_report(self.db, self.report)
        self.assertEqual(self.mapping.price, '1234.56')
        self.assertTrue(self.mapping.has_manual_price)

    def test_automatic_price_already_present_is_not_overwritten_by_recovery(self):
        self.mapping.price = '1500'
        self.mapping.price_af = '1400'
        self.db.commit()
        result = reconciliation.apply_report(self.db, self.report)
        self.assertEqual(result['prices_recovered'], 0)
        self.assertEqual(self.mapping.price, '1500')
        self.assertEqual(self.mapping.price_af, '1400')

    def test_other_account_remains_untouched(self):
        other = models.ProductMapping(product_ean='DEMO-PRICE', factory_id='DEMO-FACTORY', account_name='JV', price='1.00')
        self.db.add(other)
        self.db.commit()
        reconciliation.apply_report(self.db, self.report)
        self.assertEqual(other.price, '1.00')

    def test_changed_attempt_aborts_before_repair(self):
        self.item.attempts += 1
        self.db.commit()
        with self.assertRaises(ValueError):
            reconciliation.apply_report(self.db, self.report)
        self.assertIsNone(self.mapping.price)
        self.cache.mset.assert_not_called()

    def test_old_report_cannot_apply(self):
        self.report['created_at'] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with self.assertRaises(ValueError):
            reconciliation.apply_report(self.db, self.report)
        self.assertIsNone(self.mapping.price)

    def test_observation_cannot_cross_accounts_or_attempts(self):
        reconciliation.apply_report(self.db, self.report)
        stored = self.cache.mset.call_args.args[0]
        self.cache.mget.return_value = list(stored.values())
        self.assertEqual(reconciliation.load_observations('JV', [self.item]), {})
        self.item.attempts += 1
        self.assertEqual(reconciliation.load_observations('XL', [self.item]), {})

    def test_remote_auth_error_or_wrong_ean_is_unknown_not_absence(self):
        client = SimpleNamespace(base_url='https://mock.invalid')
        with patch.object(reconciliation.requests, 'get', return_value=Mock(status_code=403)):
            self.assertIsNone(reconciliation.check_product(client, 'mock-token', 'DEMO-PRICE')['exists'])
        response = Mock(status_code=200)
        response.json.return_value = {'sku': 'DEMO-PRICE', 'ean': 'OTHER'}
        with patch.object(reconciliation.requests, 'get', return_value=response):
            self.assertIsNone(reconciliation.check_product(client, 'mock-token', 'DEMO-PRICE')['exists'])
        response.json.return_value = {'sku': 'DEMO-PRICE', 'ean': 'DEMO-PRICE'}
        with patch.object(reconciliation.requests, 'get', return_value=response):
            self.assertTrue(reconciliation.check_product(client, 'mock-token', 'DEMO-PRICE')['exists'])

    def test_storage_failure_blocks_retry_and_missing_observation_is_not_confirmation(self):
        self.cache.mget.side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            reconciliation.load_observations('XL', [self.item], strict=True)
        self.assertEqual(reconciliation.load_observations('XL', [self.item]), {})


if __name__ == '__main__':
    unittest.main()
