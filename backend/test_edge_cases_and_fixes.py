import os
import sys
import unittest
import tempfile
import sqlite3
import asyncio
from unittest.mock import patch, AsyncMock
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.dirname(__file__))

import models
from database import get_db, SessionLocal
from routers.mapping import (
    assign_category,
    CategoryMappingBase,
    get_product_mapping,
    save_attributes,
    AttributeMappingBase,
    delete_mapping_bulk,
    BulkDeleteRequest,
    bulk_export,
    BulkExportRequest,
    auto_map_bulk,
    AutoMapBulkRequest,
    AutoMapProductModel
)
from services.roma_db import import_roma_sqlite_db
from routers.export import export_otto_json

class TestEdgeCasesAndFixes(unittest.TestCase):
    def setUp(self):
        self.db: Session = SessionLocal()
        self.test_eans = [
            "990000000001",
            "990000000002",
            "990000000003",
            "990000000004",
            "990000000005",
            "990000000006",
            "990000000007",
            "990000000008"
        ]
        # Clean test artifacts
        self.db.query(models.ProductAttribute).filter(
            models.ProductAttribute.product_ean.in_(self.test_eans)
        ).delete(synchronize_session=False)
        self.db.query(models.ProductMapping).filter(
            models.ProductMapping.product_ean.in_(self.test_eans)
        ).delete(synchronize_session=False)
        self.db.commit()

    def tearDown(self):
        self.db.query(models.ProductAttribute).filter(
            models.ProductAttribute.product_ean.in_(self.test_eans)
        ).delete(synchronize_session=False)
        self.db.query(models.ProductMapping).filter(
            models.ProductMapping.product_ean.in_(self.test_eans)
        ).delete(synchronize_session=False)
        self.db.commit()
        self.db.close()

    def test_1_roma_db_import_account_isolation(self):
        """Fix 1: Roma DB import into XL does NOT hijack or conflict with JV."""
        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE products (
                pic_main BLOB,
                ean TEXT,
                product TEXT,
                price REAL,
                category TEXT,
                collection TEXT
            )
        """)
        cur.execute("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?)", (
            b"", "990000000001", "Test Bed", 199.99, "Betten", "504000"
        ))
        conn.commit()
        conn.close()

        try:
            # 1. Import into JV
            res_jv = import_roma_sqlite_db(path, factory_id="504000", account="JV", db=self.db)
            self.db.commit()
            m_jv = self.db.query(models.ProductMapping).filter(
                models.ProductMapping.product_ean == "990000000001",
                models.ProductMapping.account_name == "JV"
            ).first()
            self.assertIsNotNone(m_jv)
            self.assertEqual(m_jv.account_name, "JV")

            # 2. Import same into XL -> MUST NOT crash with IntegrityError and MUST NOT overwrite JV!
            res_xl = import_roma_sqlite_db(path, factory_id="504000", account="XL", db=self.db)
            self.db.commit()
            m_xl = self.db.query(models.ProductMapping).filter(
                models.ProductMapping.product_ean == "990000000001",
                models.ProductMapping.account_name == "XL"
            ).first()
            self.assertIsNotNone(m_xl)
            self.assertEqual(m_xl.account_name, "XL")

            # Verify JV is STILL intact!
            m_jv_check = self.db.query(models.ProductMapping).filter(
                models.ProductMapping.product_ean == "990000000001",
                models.ProductMapping.account_name == "JV"
            ).first()
            self.assertIsNotNone(m_jv_check)
            self.assertEqual(m_jv_check.account_name, "JV")
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_2_bulk_delete_account_scoping(self):
        """Fix 2: Bulk delete with account='XL' does NOT touch JV."""
        ean = "990000000002"
        # Create in JV
        self.db.add(models.ProductMapping(product_ean=ean, account_name="JV", factory_id="504000", category_id="sofas"))
        self.db.add(models.ProductAttribute(product_ean=ean, account_name="JV", attribute_name="Farbe", attribute_value="Blau"))
        # Create in XL
        self.db.add(models.ProductMapping(product_ean=ean, account_name="XL", factory_id="504000", category_id="sofas"))
        self.db.add(models.ProductAttribute(product_ean=ean, account_name="XL", attribute_name="Farbe", attribute_value="Rot"))
        self.db.commit()

        # Execute bulk delete for XL only
        req = BulkDeleteRequest(product_eans=[ean], account="XL")
        res = asyncio.run(delete_mapping_bulk(req, db=self.db))
        self.assertEqual(res["status"], "success")

        # XL must be deleted
        m_xl = self.db.query(models.ProductMapping).filter(
            models.ProductMapping.product_ean == ean,
            models.ProductMapping.account_name == "XL"
        ).first()
        self.assertIsNone(m_xl)

        # JV must STILL EXIST with attributes!
        m_jv = self.db.query(models.ProductMapping).filter(
            models.ProductMapping.product_ean == ean,
            models.ProductMapping.account_name == "JV"
        ).first()
        self.assertIsNotNone(m_jv)
        a_jv = self.db.query(models.ProductAttribute).filter(
            models.ProductAttribute.product_ean == ean,
            models.ProductAttribute.account_name == "JV"
        ).first()
        self.assertIsNotNone(a_jv)
        self.assertEqual(a_jv.attribute_value, "Blau")

    def test_3_get_product_mapping_strict_isolation(self):
        """Fix 3: get_product_mapping with account='XL' returns Unmapped for unmapped product, NO JV bleed."""
        ean = "990000000003"
        # Only in JV
        self.db.add(models.ProductMapping(product_ean=ean, account_name="JV", factory_id="504000", category_id="sofas"))
        self.db.add(models.ProductAttribute(product_ean=ean, account_name="JV", attribute_name="Marke", attribute_value="Meise Möbel"))
        self.db.commit()

        # Query for XL
        res_xl = asyncio.run(get_product_mapping(ean, account="XL", db=self.db))
        self.assertIsNone(res_xl["category_id"])
        self.assertEqual(res_xl["status"], "Unmapped")
        self.assertEqual(res_xl["attributes"], {})

        # Query for JV
        res_jv = asyncio.run(get_product_mapping(ean, account="JV", db=self.db))
        self.assertEqual(res_jv["category_id"], "sofas")
        self.assertEqual(res_jv["attributes"].get("Marke"), "Meise Möbel")

    def test_4_bulk_export_account_filter(self):
        """Fix 5: bulk_export only exports products belonging to the requested account."""
        fid = "504999"
        ean_jv = "990000000004"
        ean_xl = "990000000005"
        self.db.add(models.ProductMapping(product_ean=ean_jv, account_name="JV", factory_id=fid, category_id="sofas"))
        self.db.add(models.ProductMapping(product_ean=ean_xl, account_name="XL", factory_id=fid, category_id="sofas"))
        self.db.commit()

        req = BulkExportRequest(factory_ids=[fid], account="XL")
        async def read_stream():
            resp = await bulk_export("csv", req, db=self.db)
            chunks = []
            async for chunk in resp.body_iterator:
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8")
        content = asyncio.run(read_stream())

        self.assertIn(ean_xl, content)
        self.assertNotIn(ean_jv, content)

    def test_5_auto_map_bulk_applies_default_constants(self):
        """Fix 6: auto_map_bulk ensures standard account defaults are created and valid status is set."""
        ean = "990000000006"
        self.db.add(models.ProductMapping(product_ean=ean, account_name="JV", factory_id="504000", status="Not Mapped"))
        self.db.commit()

        mock_user = models.User(id=1, username="test", openai_api_key="sk-fake")
        req = AutoMapBulkRequest(
            factory_id="504000",
            account="JV",
            products=[AutoMapProductModel(
                ean=ean,
                title="Ecksofa Schlafsofa Grau",
                specifics="Farbe: Grau",
                gallery_url="http://example.com/img.jpg",
                picture_urls=""
            )]
        )

        fake_result = {
            "status": "success",
            "ean": ean,
            "category_id": "sofas",
            "category_name": "Sofas",
            "attributes": {"Farbe": "Grau"},
            "errors": [],
            "db_updates": {
                "category_id": "sofas",
                "mapping_status": "Need Attributes",
                "attributes_to_save": {"Farbe": "Grau"}
            }
        }

        with patch("routers.mapping.auto_map_product", new=AsyncMock(return_value=fake_result)):
            res = asyncio.run(auto_map_bulk(req, current_user=mock_user, db=self.db))

        self.assertEqual(res["status"], "success")

        # Check default constants were applied!
        attrs = self.db.query(models.ProductAttribute).filter(
            models.ProductAttribute.product_ean == ean,
            models.ProductAttribute.account_name == "JV"
        ).all()
        attr_dict = {a.attribute_name: a.attribute_value for a in attrs}
        self.assertIn("Farbe", attr_dict)
        self.assertIn("Marke", attr_dict)
        self.assertEqual(attr_dict["Marke"], "JVMOEBEL")

    def test_6_otto_json_export_no_jv_fallback(self):
        """Fix 7: export_otto_json for XL does NOT fall back to JV attributes if XL has none."""
        ean = "990000000007"
        fid = "504888"
        # JV has attributes
        self.db.add(models.ProductMapping(product_ean=ean, account_name="JV", factory_id=fid, category_id="sofas", status="Ready"))
        self.db.add(models.ProductAttribute(product_ean=ean, account_name="JV", attribute_name="Marke", attribute_value="Meise Möbel"))
        # XL has mapping Ready, but no attributes
        self.db.add(models.ProductMapping(product_ean=ean, account_name="XL", factory_id=fid, category_id="sofas", status="Ready"))
        self.db.commit()

        res = asyncio.run(export_otto_json(factory_id=fid, account="XL", db=self.db))
        import json
        data = json.loads(res.body.decode("utf-8")) if hasattr(res, "body") else json.loads(res)
        xl_product = next((p for p in data if p.get("sku") == ean or p.get("productReference") == ean), None)
        if xl_product:
            # Attributes must NOT have JV's Meise Möbel
            self.assertNotIn("Meise Möbel", str(xl_product.get("attributes", {})))

    def test_7_ean_whitespace_sanitization(self):
        """Fix 8: EAN with leading/trailing spaces is stripped cleanly."""
        ean = "990000000008"
        padded_ean = f"  {ean} \n"
        
        # assign_category
        cat_req = CategoryMappingBase(product_ean=padded_ean, factory_id="504000", account="JV", category_id="sofas")
        res1 = asyncio.run(assign_category(cat_req, db=self.db))
        self.assertEqual(res1["status"], "success")

        # Verify DB has stripped EAN
        m = self.db.query(models.ProductMapping).filter(models.ProductMapping.product_ean == ean).first()
        self.assertIsNotNone(m)
        self.assertEqual(m.product_ean, ean)

        # save_attributes
        attr_req = AttributeMappingBase(product_ean=padded_ean, account="JV", attributes={"Farbe": "Schwarz"})
        res2 = asyncio.run(save_attributes(attr_req, db=self.db))
        self.assertEqual(res2["status"], "success")

        a = self.db.query(models.ProductAttribute).filter(models.ProductAttribute.product_ean == ean).first()
        self.assertIsNotNone(a)
        self.assertEqual(a.product_ean, ean)

if __name__ == "__main__":
    unittest.main()
