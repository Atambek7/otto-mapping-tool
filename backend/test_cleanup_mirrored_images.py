import os
import time
import shutil
import tempfile
import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from database import Base
import models
from scripts.cleanup_mirrored_images import (
    get_referenced_mirrored_files,
    clean_mirrored_images,
)
from main import app


class TestCleanupMirroredImages(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.temp_dir = tempfile.mkdtemp(prefix="test_mirrored_")

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_get_referenced_mirrored_files(self):
        # 1. Active ProductMapping with mirrored URL
        p1 = models.ProductMapping(
            product_ean="4071489000001",
            account_name=models.AccountType.XL,
            image_url="/static/mirrored/mirrored_XL_4071489000001_abc123.jpg",
        )
        # 2. Remote URL (should NOT be included in referenced mirrored files)
        p2 = models.ProductMapping(
            product_ean="4071489000002",
            account_name=models.AccountType.JV,
            image_url="https://img.jvmoebel.de/sofa.jpg",
        )
        self.db.add_all([p1, p2])
        self.db.commit()

        referenced = get_referenced_mirrored_files(self.db)
        self.assertIn("mirrored_XL_4071489000001_abc123.jpg", referenced)
        self.assertEqual(len(referenced), 1)

    def test_clean_mirrored_images_dry_run_and_execution(self):
        # Setup referenced file
        referenced_name = "mirrored_XL_4071489000001_ref.jpg"
        referenced_path = os.path.join(self.temp_dir, referenced_name)
        with open(referenced_path, "wb") as f:
            f.write(b"REFERENCED_IMAGE_DATA_12345")

        # Setup orphaned file OLD (e.g. 20 days old)
        old_orphan_name = "mirrored_XL_orphan_old.jpg"
        old_orphan_path = os.path.join(self.temp_dir, old_orphan_name)
        with open(old_orphan_path, "wb") as f:
            f.write(b"ORPHANED_OLD_DATA_12345")
        old_mtime = time.time() - (20 * 86400)
        os.utime(old_orphan_path, (old_mtime, old_mtime))

        # Setup orphaned file NEW (e.g. 1 day old, should be preserved with older_than_days=14)
        new_orphan_name = "mirrored_XL_orphan_new.jpg"
        new_orphan_path = os.path.join(self.temp_dir, new_orphan_name)
        with open(new_orphan_path, "wb") as f:
            f.write(b"ORPHANED_NEW_DATA_12345")
        new_mtime = time.time() - (1 * 86400)
        os.utime(new_orphan_path, (new_mtime, new_mtime))

        # Add referenced mapping to DB
        p = models.ProductMapping(
            product_ean="4071489000001",
            account_name=models.AccountType.XL,
            image_url=f"/static/mirrored/{referenced_name}",
        )
        self.db.add(p)
        self.db.commit()

        # 1. DRY-RUN test
        stats_dry = clean_mirrored_images(
            db=self.db,
            mirrored_dir=self.temp_dir,
            older_than_days=14,
            dry_run=True,
        )
        self.assertEqual(stats_dry["total_files_on_disk"], 3)
        self.assertEqual(stats_dry["referenced_files_count"], 1)
        self.assertEqual(stats_dry["orphaned_files_count"], 2)
        self.assertEqual(stats_dry["eligible_for_deletion_count"], 1)  # Only the 20-day-old one
        self.assertEqual(stats_dry["deleted_files_count"], 0)  # None deleted in dry-run
        self.assertTrue(os.path.exists(old_orphan_path))

        # 2. EXECUTE test
        stats_exec = clean_mirrored_images(
            db=self.db,
            mirrored_dir=self.temp_dir,
            older_than_days=14,
            dry_run=False,
        )
        self.assertEqual(stats_exec["deleted_files_count"], 1)
        # Old orphan deleted
        self.assertFalse(os.path.exists(old_orphan_path))
        # Referenced and new orphan still exist!
        self.assertTrue(os.path.exists(referenced_path))
        self.assertTrue(os.path.exists(new_orphan_path))

    def test_health_disk_usage_endpoint(self):
        client = TestClient(app)
        resp = client.get("/health/disk-usage")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("mirrored", data)
        self.assertIn("files_count", data["mirrored"])
        self.assertIn("total_mb", data["mirrored"])
        self.assertIn("disk_free_gb", data)


if __name__ == "__main__":
    unittest.main()
