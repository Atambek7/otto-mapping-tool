from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import text

from services.ean_pool_service import EanPoolError, import_pool_entries, reserve_operation_ean
from services.operation_service import create_operation
from tests.test_factory_service import FactoryServicePostgresTests


class CatalogReservationPostgresTests(FactoryServicePostgresTests):
    def test_selected_assignment_is_account_and_version_scoped_and_reuses_one_ean(self):
        source_id, candidate_id, _, _ = self.stage_candidate(account="XL")
        with self.engine.begin() as conn:
            operation = create_operation(conn, account="XL", candidate_episode_id=candidate_id, actor="offline-operator")
            import_pool_entries(conn, account="XL", eans=["4991231231231", "4991231231232"], actor="offline", data_scope="PRODUCTION")
            result = reserve_operation_ean(conn, account="XL", operation_id=operation["id"], expected_version=1, actor="offline")
            self.assertEqual(result["status"], "RESERVED")
            self.assertEqual(result["version"], 2)
            repeat = reserve_operation_ean(conn, account="XL", operation_id=operation["id"], expected_version=2, actor="offline")
            self.assertTrue(repeat["reused"])
            self.assertEqual(repeat["ean"], result["ean"])
            with self.assertRaises(EanPoolError) as conflict:
                reserve_operation_ean(conn, account="XL", operation_id=operation["id"], expected_version=1, actor="offline")
            self.assertEqual(conflict.exception.code, "VERSION_CONFLICT")
            with self.assertRaises(EanPoolError) as scoped:
                reserve_operation_ean(conn, account="JV", operation_id=operation["id"], expected_version=2, actor="offline")
            self.assertEqual(scoped.exception.code, "OPERATION_NOT_FOUND")
            self.assertEqual(conn.execute(text("SELECT count(*) FROM ean_pool WHERE status='RESERVED'")).scalar_one(), 1)
            self.assertEqual(conn.execute(text("SELECT original_title FROM source_products WHERE id=:id"), {"id": source_id}).scalar_one(), "Offline fixture")

    def test_concurrent_selected_products_cannot_share_one_free_ean(self):
        candidates = [self.stage_candidate(account="XL")[1] for _ in range(2)]
        with self.engine.begin() as conn:
            operations = [create_operation(conn, account="XL", candidate_episode_id=candidate, actor="offline") for candidate in candidates]
            import_pool_entries(conn, account="XL", eans=["4991231231233"], actor="offline", data_scope="PRODUCTION")
        barrier = Barrier(2)
        def assign(operation):
            barrier.wait(timeout=10)
            try:
                with self.engine.begin() as conn:
                    return reserve_operation_ean(conn, account="XL", operation_id=operation["id"], expected_version=1, actor="offline")["status"]
            except EanPoolError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(assign, operations))
        self.assertEqual(results.count("RESERVED"), 1)
        self.assertEqual(results.count("POOL_EXHAUSTED"), 1)
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT count(*) FROM restart_operations WHERE new_ean='4991231231233'")).scalar_one(), 1)
