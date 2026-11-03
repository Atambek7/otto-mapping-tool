"""Factory history is account-scoped and does not count plans alone."""
import unittest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
import models
from routers.publish_pipeline import list_drip_factories

class FactoryHistoryTests(unittest.TestCase):
    def test_distinct_evidence_and_account(self):
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        def add(factory, account, href=None, executor=models.ExecutorType.OTTO_API):
            plan = models.PublishPlan(factory_id=factory, account_name=account,
                                      total_products=1, total_days=1, executor_type=executor)
            db.add(plan)
            db.flush()
            db.add(models.PublishQueueItem(plan_id=plan.id, product_ean=factory,
                account_name=account, category_id='test', scheduled_day=1,
                scheduled_date=datetime.now(), otto_task_href=href))
        add('known', models.AccountType.JV, 'task')
        add('known', models.AccountType.JV, 'another-task')
        add('pending', models.AccountType.JV)
        add('rejected', models.AccountType.JV)
        db.query(models.PublishQueueItem).filter_by(product_ean='rejected').one().otto_submission_attempted_at = datetime.now()
        add('batch', models.AccountType.JV, 'task', models.ExecutorType.BATCH_FILE)
        add('xl-only', models.AccountType.XL, 'task')
        db.commit()
        result = list_drip_factories(models.AccountType.JV, db)
        self.assertEqual(result['count'], 2)
        self.assertEqual({f['id'] for f in result['factories']}, {'known', 'rejected'})
        self.assertFalse(result['history_complete'])
        self.assertEqual(list_drip_factories(models.AccountType.XL, db)['factories'][0]['id'], 'xl-only')
        db.close()
        engine.dispose()
