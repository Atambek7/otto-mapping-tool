from __future__ import annotations

import unittest

from services.preparation_service import _attribute_values, _target_images


class PreparationPayloadContractTests(unittest.TestCase):
    def test_ottoflow_logistics_rules_preserve_source_and_apply_requested_defaults(self):
        from services.logistics_formatter import build_logistics
        data,evidence=build_logistics({}, {}, {'Breite':'205','Höhe':'78','Tiefe':'90'})
        self.assertEqual(data['packingUnits'][0],{'width':205,'height':78,'length':90,'weight':1})
        self.assertEqual(evidence['strategy'],'OTTOFLOW_FORMATTER')
        source={'packingUnits':[{'weight':25}],'packingUnitCount':1}
        self.assertEqual(build_logistics({'logistics':source},{},{})[0],source)
        self.assertEqual(build_logistics({}, {}, {})[0]['packingUnits'][0],{'width':10,'height':10,'length':10,'weight':1})

    def test_attribute_normalization_is_schema_bound_and_idempotent(self):
        from services.logistics_formatter import normalize_schema_attribute
        spec={'multiValue':True,'allowedValues':['Erwachsene','Jugendliche']}
        normalized=normalize_schema_attribute('Erwachsene, Jugendliche',spec)
        self.assertEqual(normalized,'Erwachsene; Jugendliche')
        self.assertEqual(normalize_schema_attribute(normalized,spec),normalized)
        self.assertEqual(normalize_schema_attribute('Erwachsene, Kinder',spec),'Erwachsene, Kinder')
        self.assertEqual(normalize_schema_attribute('Erwachsene, Jugendliche',{**spec,'multiValue':False}),'Erwachsene, Jugendliche')
        self.assertEqual(normalize_schema_attribute('205 cm',{'type':'INTEGER'}),'205')

    def test_ai_attribute_type_validation_rejects_unbekannt_and_enforces_numeric_types(self):
        from services.logistics_formatter import validate_and_normalize_ai_attribute

        float_spec = {'name': 'Höhe Füße', 'type': 'FLOAT', 'unit': 'cm', 'multiValue': True}
        int_spec = {'name': 'Anzahl Sitzflächen', 'type': 'INTEGER', 'multiValue': False}
        enum_spec = {'name': 'Farbe', 'type': 'STRING', 'allowedValues': ['Grau', 'Schwarz'], 'multiValue': False}

        for placeholder in ('unbekannt', 'Unbekannt', 'nicht bekannt', 'k.A.', 'keine Angabe', 'none', 'n/a', '-'):
            self.assertIsNone(validate_and_normalize_ai_attribute(placeholder, float_spec))
            self.assertIsNone(validate_and_normalize_ai_attribute(placeholder, enum_spec))

        self.assertEqual(validate_and_normalize_ai_attribute('120', float_spec), '120')
        self.assertEqual(validate_and_normalize_ai_attribute('73,5 cm', float_spec), '73.5')
        self.assertEqual(validate_and_normalize_ai_attribute('12; 15.5 cm', float_spec), '12; 15.5')
        self.assertEqual(validate_and_normalize_ai_attribute('4,00', int_spec), '4')
        self.assertIsNone(validate_and_normalize_ai_attribute('4.5', int_spec))
        self.assertIsNone(validate_and_normalize_ai_attribute('4; 5', int_spec))
        self.assertEqual(validate_and_normalize_ai_attribute('Grau', enum_spec), 'Grau')
        self.assertIsNone(validate_and_normalize_ai_attribute('Lila', enum_spec))

    def test_preparation_drops_unbekannt_placeholders_and_invalid_numeric_attributes(self):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock, patch
        from services.preparation_service import _build_target_content

        conn = MagicMock()
        conn.execute.return_value.mappings.return_value.first.return_value = {
            'title': 'Chesterfield Sofagarnitur mit Designer Couchtisch',
            'category': 'Chesterfield-Sofa',
            'attributes_json': {
                'Höhe Füße': 'unbekannt',
                'Höhe Armlehnen': 'unbekannt',
                'Breite': '260 cm',
                'Sitzhöhe': '42,50',
                'Farbe': 'Grau',
            },
            'version': 3,
            'updated_by': 'operator',
            'updated_at': datetime(2026, 11, 21, 8, 0, tzinfo=timezone.utc),
        }
        schema_attrs = [
            {'name': 'Höhe Füße', 'type': 'FLOAT', 'unit': 'cm', 'required': False, 'legal': False},
            {'name': 'Höhe Armlehnen', 'type': 'FLOAT', 'unit': 'cm', 'required': False, 'legal': False},
            {'name': 'Breite', 'type': 'FLOAT', 'unit': 'cm', 'required': False, 'legal': False},
            {'name': 'Sitzhöhe', 'type': 'FLOAT', 'unit': 'cm', 'required': False, 'legal': False},
            {'name': 'Farbe', 'type': 'STRING', 'required': False, 'legal': False},
        ]
        with patch('services.categories_repo.get_category', return_value={'name': 'Chesterfield-Sofa', 'categoryId': '123'}), \
             patch('services.categories_repo.get_category_schema_attributes', return_value=schema_attrs):
            content, package, _gaps, _source = _build_target_content(
                conn,
                context={'original_title': 'Sofa', 'source_type': 'OTTO_DIRECT'},
                account='XL',
                otto_product={'productDescription': {'category': 'Chesterfield-Sofa', 'brandId': '6HMOZBOU'}},
                target_sku='4072102621489',
                price={'targetPriceCents': 768999, 'uvpPriceCents': 845900},
                source_ref='OTTO:/v5/products/source',
                candidate_id='322e2867-d770-4e7d-b418-f5fc2acce044',
                aftercool=None,
            )
        by_name = {item['name']: item['value'] for item in content['attributes']}
        self.assertNotIn('Höhe Füße', by_name)
        self.assertNotIn('Höhe Armlehnen', by_name)
        self.assertEqual(by_name['Breite'], '260')
        self.assertEqual(by_name['Sitzhöhe'], '42.5')
        self.assertEqual(by_name['Farbe'], 'Grau')
        pkg_names = {item['name'] for item in package['productDescription']['attributes']}
        self.assertNotIn('Höhe Füße', pkg_names)
        self.assertNotIn('Höhe Armlehnen', pkg_names)

    def test_formatter_resolves_category_id_to_otto_name(self):
        from unittest.mock import MagicMock, patch
        from services.preparation_service import _build_target_content
        conn = MagicMock()
        conn.execute.return_value.mappings.return_value.first.return_value = None
        with patch("services.categories_repo.get_category", return_value={"name": "Sofa", "categoryId": "25927"}), \
             patch("services.categories_repo.get_category_schema_attributes", return_value=[]):
            content, package, _gaps, source = _build_target_content(
                conn, context={"original_title": "Sofa", "source_type": "OTTO_DIRECT"}, account="JV",
                otto_product={"productDescription": {"category": "25927", "brandId": "brand"}},
                target_sku="4072102511612", price={"targetPriceCents": 10000, "uvpPriceCents": 13500},
                source_ref="OTTO:/v5/products/source", candidate_id="candidate", aftercool=None,
            )
        self.assertEqual(content["category"], "Sofa")
        self.assertEqual(package["productDescription"]["category"], "Sofa")
        self.assertEqual(source["categorySchema"]["categoryId"], "25927")

    def test_database_exception_is_not_exposed_as_sqlalchemy_documentation_code(self):
        from sqlalchemy.exc import IntegrityError
        from jobs.preparation_handler import _preparation_error_code
        self.assertEqual(_preparation_error_code(IntegrityError("sql", {}, Exception("details"))), "PREPARATION_DATABASE_ERROR")
        self.assertEqual(_preparation_error_code(RuntimeError("private details")), "PREPARATION_FAILED")

    def test_catalog_image_is_the_first_asset_and_original_ean_gallery_keeps_its_order(self):
        gallery={"verified":True,"image_urls":[f"https://images.example/{i}.jpg" for i in range(11)]}
        catalog_hero="https://catalog.example/hero.jpg"
        for source_type in ("AFTERCOOL", "OTTO_DIRECT"):
            with self.subTest(source_type=source_type):
                images, assets = _target_images({"mediaAssets":[{"location":"https://otto.example/old.jpg"}]},
                    source_type=source_type,aftercool=None,otto_source_ref="OTTO:/source",
                    aftercool_gallery=gallery,catalog_image_url=catalog_hero)
                self.assertEqual([row["location"] for row in assets],[catalog_hero,*gallery["image_urls"][:9]])
                self.assertEqual(images[0]["sourceRefs"],["OttoRestart:catalog-image"])

    def test_catalog_image_deduplicates_when_it_is_already_in_aftercool_gallery(self):
        hero="https://images.example/hero.jpg"
        images,assets=_target_images({},source_type="AFTERCOOL",aftercool=None,
            otto_source_ref="OTTO:/source",
            aftercool_gallery={"verified":True,"image_urls":[hero,"https://images.example/second.jpg"]},
            catalog_image_url=hero)
        self.assertEqual([row["location"] for row in assets],[hero,"https://images.example/second.jpg"])
        self.assertEqual(images[0]["sourceRefs"],["OttoRestart:catalog-image"])

    def test_invalid_catalog_image_is_ignored_and_gallery_remains_first(self):
        gallery_hero="https://images.example/gallery-hero.jpg"
        images,assets=_target_images({},source_type="AFTERCOOL",aftercool=None,
            otto_source_ref="OTTO:/source",
            aftercool_gallery={"verified":True,"image_urls":[gallery_hero]},
            catalog_image_url="http://catalog.example/not-https.jpg")
        self.assertEqual([row["location"] for row in assets],[gallery_hero])
        self.assertEqual(images[0]["sourceRefs"],["Aftercool:/api/products/by-ean"])

    def test_source_attributes_are_normalized_without_losing_multiple_values(self):
        values = _attribute_values({
            "attributes": [
                {"name": "Material", "values": ["Holz"]},
                {"name": "Farben", "values": ["Weiß", "Grau"]},
                {"name": "Ignored empty", "values": []},
            ]
        })
        self.assertEqual(values, {"Material": "Holz", "Farben": "Weiß; Grau"})

    def test_media_assets_match_ottoflow_contract_and_keep_exact_provenance(self):
        images, assets = _target_images(
            {"mediaAssets": [
                {"type": "IMAGE", "location": "https://otto.example/image.jpg"},
                {"type": "IMAGE", "filename": "filename-only.jpg"},
            ]},
            source_type="OTTO_DIRECT",
            aftercool=None,
            otto_source_ref="OTTO:/v5/products/123",
        )
        self.assertEqual(images, [{
            "url": "https://otto.example/image.jpg",
            "sourceRefs": ["OTTO:/v5/products/123"],
        }])
        self.assertEqual(assets, [{
            "type": "IMAGE", "location": "https://otto.example/image.jpg",
        }])

    def test_verified_aftercool_gallery_precedes_otto_and_rejects_non_https(self):
        images, assets = _target_images(
            {"mediaAssets": [{"type": "IMAGE", "location": "https://otto.example/fallback.jpg"}]},
            source_type="AFTERCOOL",
            aftercool={
                "status": "FOUND", "verified": True,
                "image_urls": ["https://aftercool.example/main.jpg", "http://unsafe.example/image.jpg"],
            },
            otto_source_ref="OTTO:/v5/products/123",
        )
        self.assertEqual([item["url"] for item in images], [
            "https://aftercool.example/main.jpg", "https://otto.example/fallback.jpg",
        ])
        self.assertEqual(images[0]["sourceRefs"], ["Aftercool:/api/products"])
        self.assertEqual(assets[0], {"type": "IMAGE", "location": "https://aftercool.example/main.jpg"})

    def test_prepare_operation_revision_registers_catalog_image_reference_for_qc(self):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock, patch
        from services.preparation_service import prepare_operation_revision
        from services.qc_service import source_reference_ids

        conn = MagicMock()
        conn.execute.return_value.mappings.return_value.first.return_value = None
        captured = {}

        def capture_revision(_conn, **kwargs):
            captured.update(kwargs)
            return {"id": "rev-1", "revisionNumber": 1, "payloadHash": "a" * 64}

        context = {
            "operation_id": "11111111-1111-1111-1111-111111111111",
            "source_product_id": "22222222-2222-2222-2222-222222222222",
            "candidate_episode_id": "33333333-3333-3333-3333-333333333333",
            "source_type": "AFTERCOOL",
            "aftercool_product_id": "67093835",
            "aftercool_sku": "4260606057426",
            "aftercool_ean": "4260606057426",
            "factory_id": "501351",
            "factory_name": "Test Factory",
            "original_title": "Chesterfield Sofa",
            "category": "Chesterfield-Sofa",
            "catalog_image_url": "https://varvara.de/A-GS3/3Sitzer/156/b1.jpg",
            "source_updated_at": datetime(2026, 11, 18, tzinfo=timezone.utc),
            "observed_sku": "4260606057426",
            "confirmed_ean": "4260606057426",
            "period": "3M",
            "classification": "EXACT_CODE",
            "episode_state": "OPEN",
            "version": 1,
            "operation_status": "PREPARING",
        }
        eligibility = MagicMock(eligible=True, evidence_ids=())
        with patch("services.preparation_service._load_context", return_value=context), \
             patch("services.preparation_service.reserve_ean_for_operation", return_value={"status": "RESERVED", "ean": "4072102511629", "reused": True}), \
             patch("services.preparation_service.validate_gtin_eligibility", return_value=eligibility), \
             patch("services.preparation_service.append_operation_revision", side_effect=capture_revision):
            prepare_operation_revision(
                conn, account="JV", operation_id=context["operation_id"], expected_version=1,
                preflight_context=context,
                otto_product={"sku": "4260606057426", "productDescription": {"category": "Chesterfield-Sofa", "brandId": "brand-1", "productLine": "Chesterfield Sofa", "description": "Desc", "bulletPoints": ["B1"]}},
                otto_prices={"sku": "4260606057426", "standardPrice": {"amount": "2299.99", "currency": "EUR"}},
                fulfillment={"sku": "4260606057426"},
                aftercool={"account": "JV", "status": "FOUND", "found": True, "verified": True, "data_scope": "PRODUCTION", "source_type": "AFTERCOOL", "product_id": "67093835", "ean": "4260606057426", "factory_id": "501351", "price_cents": 229999},
                aftercool_gallery={"verified": True, "data_scope": "PRODUCTION", "account": "JV", "ean": "4260606057426", "image_urls": ["https://varvara.de/A-GS3/3Sitzer/156/b2.jpg"]},
                captured_at=datetime(2026, 11, 18, tzinfo=timezone.utc),
                actor="operator",
                approved_evidence_types=(),
            )
        refs = source_reference_ids(captured["source_snapshot"]["references"])
        self.assertIn("OttoRestart:catalog-image", refs)
        self.assertIn("Aftercool:/api/products/by-ean", refs)

    def test_normalized_upscaled_images_preserve_valid_urls_and_pass_qc(self):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock, patch
        from services.preparation_service import prepare_operation_revision
        from services.qc_service import source_reference_ids

        conn = MagicMock()
        conn.execute.return_value.mappings.return_value.first.return_value = None
        captured = {}

        def capture_revision(_conn, **kwargs):
            captured.update(kwargs)
            return {"id": "rev-2", "revisionNumber": 2, "payloadHash": "b" * 64}

        context = {
            "operation_id": "11111111-1111-1111-1111-111111111111",
            "source_product_id": "22222222-2222-2222-2222-222222222222",
            "candidate_episode_id": "33333333-3333-3333-3333-333333333333",
            "source_type": "AFTERCOOL",
            "aftercool_product_id": "67093835",
            "aftercool_sku": "4260606057426",
            "aftercool_ean": "4260606057426",
            "factory_id": "501351",
            "factory_name": "Test Factory",
            "original_title": "Chesterfield Sofa",
            "category": "Chesterfield-Sofa",
            "catalog_image_url": "https://varvara.de/A-GS3/3Sitzer/156/b1.jpg",
            "source_updated_at": datetime(2026, 11, 18, tzinfo=timezone.utc),
            "observed_sku": "4260606057426",
            "confirmed_ean": "4260606057426",
            "period": "3M",
            "classification": "EXACT_CODE",
            "episode_state": "OPEN",
            "version": 1,
            "operation_status": "PREPARING",
        }
        eligibility = MagicMock(eligible=True, evidence_ids=())
        upscaled_url = "https://omt.automatonsoft.de/restart/media/upscaled_JV_0123456789ab_fedcba987654.jpg"
        normalized = {
            "https://varvara.de/A-GS3/3Sitzer/156/b1.jpg": {
                "sourceUrl": "https://varvara.de/A-GS3/3Sitzer/156/b1.jpg",
                "normalizedUrl": "https://varvara.de/A-GS3/3Sitzer/156/b1.jpg",
                "upscaled": False,
                "originalSize": [1280, 851],
                "finalSize": [1280, 851],
            },
            "https://www.handelshop24.com/FarbmusterST.jpg": {
                "sourceUrl": "https://www.handelshop24.com/FarbmusterST.jpg",
                "normalizedUrl": upscaled_url,
                "upscaled": True,
                "originalSize": [640, 236],
                "finalSize": [1356, 500],
                "sha256": "f" * 64,
                "filename": "upscaled_JV_0123456789ab_fedcba987654.jpg",
            },
        }
        with patch("services.preparation_service._load_context", return_value=context), \
             patch("services.preparation_service.reserve_ean_for_operation", return_value={"status": "RESERVED", "ean": "4072102511629", "reused": True}), \
             patch("services.preparation_service.validate_gtin_eligibility", return_value=eligibility), \
             patch("services.preparation_service.append_operation_revision", side_effect=capture_revision):
            prepare_operation_revision(
                conn, account="JV", operation_id=context["operation_id"], expected_version=1,
                preflight_context=context,
                otto_product={"sku": "4260606057426", "productDescription": {"category": "Chesterfield-Sofa", "brandId": "brand-1", "productLine": "Chesterfield Sofa", "description": "Desc", "bulletPoints": ["B1"]}},
                otto_prices={"sku": "4260606057426", "standardPrice": {"amount": "2299.99", "currency": "EUR"}},
                fulfillment={"sku": "4260606057426"},
                aftercool={"account": "JV", "status": "FOUND", "found": True, "verified": True, "data_scope": "PRODUCTION", "source_type": "AFTERCOOL", "product_id": "67093835", "ean": "4260606057426", "factory_id": "501351", "price_cents": 229999},
                aftercool_gallery={"verified": True, "data_scope": "PRODUCTION", "account": "JV", "ean": "4260606057426", "image_urls": ["https://www.handelshop24.com/FarbmusterST.jpg"]},
                normalized_images=normalized,
                captured_at=datetime(2026, 11, 18, tzinfo=timezone.utc),
                actor="operator",
                approved_evidence_types=(),
            )
        content_images = captured["content"]["images"]
        self.assertEqual([img["url"] for img in content_images], [
            "https://varvara.de/A-GS3/3Sitzer/156/b1.jpg",
            upscaled_url,
        ])
        self.assertEqual(content_images[0]["sourceRefs"], ["OttoRestart:catalog-image"])
        self.assertEqual(content_images[1]["sourceRefs"], ["Aftercool:/api/products/by-ean", "OttoRestart:upscaled-image"])
        refs = source_reference_ids(captured["source_snapshot"]["references"])
        self.assertIn("OttoRestart:upscaled-image", refs)
        self.assertEqual(len(captured["source_snapshot"]["imageNormalization"]), 1)


if __name__ == "__main__":
    unittest.main()
