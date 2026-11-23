"""Opt-in migration/policy checks against a disposable PostgreSQL 15 only.

The pre-0007 fixture uses current legacy ORM tables without the three new
tables, then stamps 0006 on this empty test DB. This does not test the older
0001-0006 migration chain, whose first revision expects pre-existing tables.
"""
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from services import factory_exclusions as policy
from tests import test_factory_exclusions as base_tests


ENABLED = os.environ.get("FACTORY_EXCLUSION_DISPOSABLE") == "1"
TABLES = {"factory_product_exclusions", "factory_product_exclusion_events", "factory_product_reassignments"}
PREVIOUS = "0006_initial_availability"
HEAD = "0007_factory_product_exclusions"


def prepare(test, upgrade=True):
    dsn = os.environ["FACTORY_EXCLUSION_TEST_DSN"]
    url = make_url(dsn)
    if (url.get_backend_name() != "postgresql" or url.host != "127.0.0.1"
            or url.database != "factory_exclusion_test" or os.environ.get("DATABASE_URL") != dsn):
        raise RuntimeError("Only the explicitly isolated PostgreSQL test DSN is allowed")
    test.engine = create_engine(dsn)
    with test.engine.begin() as conn:
        version = conn.execute(text("SHOW server_version_num")).scalar_one()
        assert 150000 <= int(version) < 160000, version
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        # Legacy tables expect these types to have been installed by 0001.
        for name, cls in (("accounttype", models.AccountType), ("planstatus", models.PlanStatus),
                          ("queueitemstatus", models.QueueItemStatus), ("executortype", models.ExecutorType)):
            ENUM(*(item.value for item in cls), name=name).create(conn, checkfirst=True)
    Base.metadata.create_all(test.engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in TABLES])
    test.config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.stamp(test.config, PREVIOUS)
    if upgrade:
        command.upgrade(test.config, "head")
    test.db = sessionmaker(bind=test.engine)()


@unittest.skipUnless(ENABLED, "Requires a disposable PostgreSQL test container")
class FactoryExclusionPostgresTests(base_tests.FactoryExclusionTests):
    def setUp(self):
        prepare(self)

    def test_already_canonical_xl_rows_are_preserved_without_reassignment(self):
        self.test_xl_apply_and_rollback_only_change_factory_ownership()
        manifest = self.load_manifest("XL")
        manifest["batch_id"] += "-canonical"
        self.db.query(models.ProductMapping).filter_by(account_name=models.AccountType.XL).update(
            {models.ProductMapping.factory_id: "483554"}, synchronize_session=False)
        self.db.commit()
        before = {m.id: (m.factory_id, m.updated_at, m.price, m.status)
                  for m in self.db.query(models.ProductMapping).all()}
        applied = policy.apply_batch(self.db, manifest, manifest["sha256"], "pg-test")
        self.assertEqual(applied["reassigned_count"], 0)
        self.assertTrue(policy.apply_batch(self.db, manifest, manifest["sha256"], "pg-test")["already_applied"])
        self.db.expire_all()
        self.assertEqual(before, {m.id: (m.factory_id, m.updated_at, m.price, m.status)
                                  for m in self.db.query(models.ProductMapping).all()})
        self.assertEqual(policy.rollback_batch(self.db, manifest["batch_id"], "pg-test", manifest["sha256"])["restored_mappings"], 0)
        self.assertEqual(self.db.query(models.ProductMapping).filter_by(factory_id="483554").count(), 1058)

    def test_xl_rollback_conflict_preserves_whole_applied_batch(self):
        self.test_xl_apply_and_rollback_only_change_factory_ownership()
        manifest = self.load_manifest("XL")
        # Reapply with a new batch so the append-only event history remains intact.
        manifest["batch_id"] += "-second"
        policy.apply_batch(self.db, manifest, manifest["sha256"], "pg-test")
        changed = self.db.query(models.ProductMapping).filter_by(
            product_ean=manifest["eans"][-1], account_name=models.AccountType.XL).one()
        changed.price = "999.99"
        changed.updated_at = datetime.now(timezone.utc) + timedelta(seconds=1)
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "changed after apply"):
            policy.rollback_batch(self.db, manifest["batch_id"], "pg-test", manifest["sha256"])
        self.assertEqual(self.db.query(models.FactoryProductExclusion).filter_by(is_active=True).count(), 1058)
        self.assertEqual(self.db.query(models.FactoryProductReassignment).filter_by(
            batch_id=manifest["batch_id"], status="APPLIED").count(), 1058)
        self.assertEqual(self.db.query(models.ProductMapping).filter_by(factory_id="483554").count(), 1058)
        self.assertEqual(self.db.query(models.PublishQueueItem).filter_by(plan_id=92).count(), 1058)
        self.assertEqual(self.db.query(models.ProductMapping).filter_by(id=changed.id).one().price, "999.99")


@unittest.skipUnless(ENABLED, "Requires a disposable PostgreSQL test container")
class FactoryExclusionMigrationTests(unittest.TestCase):
    def setUp(self):
        prepare(self, upgrade=False)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def revision(self):
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    def test_upgrade_repeat_downgrade_and_reupgrade_preserve_legacy_rows(self):
        for account in (models.AccountType.JV, models.AccountType.XL):
            self.db.add(models.ProductMapping(product_ean="4000000000001", account_name=account,
                factory_id="existing", status="Published", price="123.45", category_id="saved",
                product_name="Preserve product", ai_attributes={"Color": "Blue"}))
            self.db.add(models.ProductAttribute(product_ean="4000000000001", account_name=account,
                attribute_name="Color", attribute_value="Blue"))
        self.db.commit()
        def snapshot():
            with self.engine.connect() as conn:
                return {t: [tuple(row) for row in conn.execute(text(f"SELECT * FROM {t} ORDER BY id"))]
                        for t in ("product_mappings", "product_attributes")}
        before = snapshot()
        self.assertFalse(TABLES.intersection(inspect(self.engine).get_table_names()))
        command.upgrade(self.config, "head")
        self.assertEqual(self.revision(), HEAD)
        self.assertTrue(TABLES.issubset(inspect(self.engine).get_table_names()))
        command.upgrade(self.config, "head")
        self.assertEqual(snapshot(), before)
        command.downgrade(self.config, PREVIOUS)
        self.assertEqual(self.revision(), PREVIOUS)
        self.assertFalse(TABLES.intersection(inspect(self.engine).get_table_names()))
        self.assertEqual(snapshot(), before)
        command.upgrade(self.config, "head")
        self.assertEqual(self.revision(), HEAD)
        self.assertEqual(snapshot(), before)

    def test_downgrade_refuses_nonempty_audit_and_preserves_revision(self):
        command.upgrade(self.config, "head")
        self.db.add(models.FactoryProductExclusion(account_name="JV", excluded_factory_id="504487",
            source_factory_id="504466", product_ean="4000000000001", batch_id="pg-test",
            source_manifest_hash="a" * 64, source_verified_at=datetime.now(timezone.utc),
            reason="offline test", actor="pg-test"))
        self.db.commit()
        with self.assertRaisesRegex(RuntimeError, "Refusing to drop non-empty"):
            command.downgrade(self.config, PREVIOUS)
        self.assertEqual(self.revision(), HEAD)
        self.assertTrue(TABLES.issubset(inspect(self.engine).get_table_names()))
        self.assertEqual(self.db.query(models.FactoryProductExclusion).count(), 1)


if __name__ == "__main__":
    unittest.main()
