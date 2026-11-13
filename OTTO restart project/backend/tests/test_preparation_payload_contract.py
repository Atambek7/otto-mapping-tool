from __future__ import annotations

import unittest

from services.preparation_service import _attribute_values, _target_images


class PreparationPayloadContractTests(unittest.TestCase):
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
        self.assertEqual(images[0]["sourceRefs"], ["AFTERCOOL:/api/products"])
        self.assertEqual(assets[0], {"type": "IMAGE", "location": "https://aftercool.example/main.jpg"})


if __name__ == "__main__":
    unittest.main()
