"""Optional tests against an explicitly supplied disposable PostgreSQL DSN."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import uuid4
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
import models
from services.initial_availability import register_initial, claim_step

DSN = os.getenv('OTTOFLOW_DISPOSABLE_TEST_DSN')

@unittest.skipUnless(DSN, 'disposable PostgreSQL DSN is not configured')
class ConcurrentClaimTests(unittest.TestCase):
    def test_only_one_worker_can_claim_stock(self):
        if 'ottoflow-stock-test-pg' not in DSN:
            self.fail('Refusing a non-disposable PostgreSQL DSN')
        engine = create_engine(DSN)
        sessions = sessionmaker(bind=engine)
        session = sessions()
        ean = 'TEST-' + uuid4().hex
        plan = models.PublishPlan(factory_id='test', account_name=models.AccountType.JV,
                                  total_products=1, total_days=1)
        session.add(plan)
        session.flush()
        item = models.PublishQueueItem(plan_id=plan.id, product_ean=ean,
            account_name=models.AccountType.JV, category_id='x', scheduled_day=1,
            scheduled_date=datetime.now())
        session.add(item)
        session.commit()
        item_id = item.id
        self.assertTrue(register_initial(session, 'JV', ean, item_id,
                                         delivery_payload={'sku': ean, 'shippingProfileId': 'test', 'processingTime': '37'}))
        session.commit()
        state_id = session.query(models.InitialAvailabilityState.id).filter_by(
            account_name='JV', product_ean=ean
        ).scalar()
        def worker(_):
            local = sessions()
            try:
                return claim_step(local, state_id=state_id, operation='stock') is not None
            finally:
                local.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(worker, range(8)))
        self.assertEqual(results.count(True), 1)
        session.close()
        engine.dispose()
