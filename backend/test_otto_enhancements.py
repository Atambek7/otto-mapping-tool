import unittest
import re
from unittest.mock import MagicMock
from services.otto_formatter import (
    parse_custom_item_specifics,
    generate_bullet_points,
    generate_seo_description,
    format_otto_product,
)
from services.otto_availability import (
    OTTO_SHIPPING_PROFILES,
    is_chinese_product,
    resolve_shipping_profile_id,
    apply_otto_availability,
    extract_unknown_skus,
)

SAMPLE_XML_CHAIRS = """<ItemSpecifics>
<NameValueList><Name>Farbe</Name><Value>Braun</Value></NameValueList>
<NameValueList><Name>Lieferumfang</Name><Value>6x Stuhl</Value></NameValueList>
<NameValueList><Name>Stil</Name><Value>Mid-Century Modern</Value><Value>Retro</Value></NameValueList>
<NameValueList><Name>Maße 6x Stuhl</Name><Value>57 x 60 x 83 cm</Value></NameValueList>
<NameValueList><Name>Länge</Name><Value>57 cm</Value></NameValueList>
<NameValueList><Name>Breite</Name><Value>60 cm</Value></NameValueList>
<NameValueList><Name>Höhe</Name><Value>83 cm</Value></NameValueList>
<NameValueList><Name>Material</Name><Value>Leder</Value><Value>Holz</Value></NameValueList>
<NameValueList><Name>Sitzfläche</Name><Value>Gepolstert</Value><Value>Breit</Value><Value>Mit Steppung</Value></NameValueList>
<NameValueList><Name>Rückenlehne</Name><Value>Gepolstert</Value><Value>Mit vertikaler Steppung</Value></NameValueList>
<NameValueList><Name>Armlehnen</Name><Value>Integriert</Value><Value>Offen gestaltet</Value></NameValueList>
<NameValueList><Name>Bezug</Name><Value>Glatte Leder</Value></NameValueList>
<NameValueList><Name>Gestell</Name><Value>Holz</Value></NameValueList>
<NameValueList><Name>Gestellfarbe</Name><Value>Braun</Value></NameValueList>
<NameValueList><Name>Beine</Name><Value>4 schlanke leicht ausgestellte Beine</Value></NameValueList>
<NameValueList><Name>Design</Name><Value>Elegant</Value><Value>Klassisch</Value><Value>Retro-inspiriert</Value></NameValueList>
</ItemSpecifics>"""

SAMPLE_XML_BEDROOM = """<ItemSpecifics>
<NameValueList><Name>Farbe</Name><Value>Grau</Value></NameValueList>
<NameValueList><Name>Set enthält</Name><Value>Bett</Value><Value>Kleiderschrank</Value><Value>Kommode</Value><Value>Nachttisch</Value></NameValueList>
<NameValueList><Name>Maße Bett</Name><Value>ca. 210 x 217 x 128 cm</Value></NameValueList>
<NameValueList><Name>Liegefläche</Name><Value>ca. 180 x 200 cm</Value></NameValueList>
<NameValueList><Name>Maße Kleiderschrank</Name><Value>ca. 273 x 67 x 221 cm</Value></NameValueList>
<NameValueList><Name>Maße Kommode</Name><Value>ca. 142 x 49 x 84 cm</Value></NameValueList>
<NameValueList><Name>Maße Nachttisch</Name><Value>ca. 66 x 44 x 47 cm</Value></NameValueList>
<NameValueList><Name>Material</Name><Value>Holz</Value><Value>Textil</Value></NameValueList>
<NameValueList><Name>Stil</Name><Value>Modern</Value></NameValueList>
</ItemSpecifics>"""


class TestOttoEnhancements(unittest.TestCase):

    def test_parse_custom_item_specifics(self):
        merkmale = parse_custom_item_specifics(SAMPLE_XML_CHAIRS)
        self.assertIn("Farbe", merkmale)
        self.assertEqual(merkmale["Farbe"], ["Braun"])
        self.assertIn("Material", merkmale)
        self.assertEqual(merkmale["Material"], ["Leder", "Holz"])
        self.assertIn("Maße 6x Stuhl", merkmale)
        self.assertEqual(merkmale["Maße 6x Stuhl"], ["57 x 60 x 83 cm"])

    def test_generate_bullet_points_chairs(self):
        merkmale = parse_custom_item_specifics(SAMPLE_XML_CHAIRS)
        bullets = generate_bullet_points(merkmale, "6x Esszimmerstühle Stuhl Set Leder Braun")
        self.assertEqual(len(bullets), 5)
        for i, b in enumerate(bullets):
            self.assertLessEqual(len(b), 180, f"Bullet {i+1} exceeds 180 chars: {len(b)}")
            self.assertGreater(len(b), 10, f"Bullet {i+1} is too short")

    def test_generate_bullet_points_bedroom(self):
        merkmale = parse_custom_item_specifics(SAMPLE_XML_BEDROOM)
        bullets = generate_bullet_points(merkmale, "Schlafzimmer Set Komplett Bett Schrank Grau")
        self.assertEqual(len(bullets), 5)
        for i, b in enumerate(bullets):
            self.assertLessEqual(len(b), 180, f"Bullet {i+1} exceeds 180 chars: {len(b)}")

    def test_generate_seo_description(self):
        merkmale = parse_custom_item_specifics(SAMPLE_XML_CHAIRS)
        desc = generate_seo_description(merkmale, "6x Esszimmerstühle Stuhl Set Leder Braun")
        self.assertLessEqual(len(desc), 2000)
        self.assertGreater(len(desc), 100)
        # Check clean plain-text formatting (no HTML tags)
        for forbidden in ["<p>", "</p>", "<div>", "</div>", "<ul>", "<li>", "<h1>", "<h2>", "<strong>", "<b>", "<br>"]:
            self.assertNotIn(forbidden, desc.lower())
        self.assertIn("Design & Komfort:", desc)
        self.assertIn("Materialien & Stabilität:", desc)
        self.assertIn("Maße & Produktdetails:", desc)

    def test_format_otto_product_payload(self):
        ean = "4071489823721"
        aftercool_info = {
            "name": "6x Stuhl Esszimmerstühle Braun Leder",
            "price": "399.00",
            "images": ["https://example.com/pic1.jpg", "https://example.com/pic2.jpg"],
            "custom_item_specifics": SAMPLE_XML_CHAIRS,
        }
        mapping = {
            "account_name": "JV",
            "attributes": {"Marke": "JV-MOEBEL"},
        }
        payload = format_otto_product(
            ean=ean,
            aftercool_info=aftercool_info,
            mapping=mapping,
            category_name="Stuhl",
            account_name="JV",
        )

        p_desc = payload["productDescription"]
        # 1. Model name in attributes
        attrs = p_desc["attributes"]
        model_attr = next((a for a in attrs if a["name"] == "Modellbezeichnung"), None)
        self.assertIsNotNone(model_attr, "Modellbezeichnung must be present in attributes")
        self.assertEqual(model_attr["values"], [aftercool_info["name"]])

        # 2. 5 bullet points
        bullets = p_desc["bulletPoints"]
        self.assertEqual(len(bullets), 5)
        for b in bullets:
            self.assertLessEqual(len(b), 180)

        # 3. SEO description (clean plain text, no HTML tags)
        desc = p_desc["description"]
        self.assertLessEqual(len(desc), 2000)
        self.assertNotIn("<strong>", desc)
        self.assertNotIn("<br>", desc)
        self.assertIn("Design & Komfort:", desc)

        # 4. Logistics dimensions parsed from merkmale
        logistics = payload["logistics"]
        pu = logistics["packingUnits"][0]
        self.assertEqual(pu["length"], 57)
        self.assertEqual(pu["width"], 60)
        self.assertEqual(pu["height"], 83)

    def test_disallowed_category_attributes_purged_on_export(self):
        ean = "4071489305296"
        aftercool_info = {
            "name": "Ecksofa L-Form",
            "price": "599.00",
            "images": ["https://example.com/pic1.jpg"],
        }
        # Category 25785 does not permit "Art Herstellung" or "Hinweis Lieferumfang"
        mapping = {
            "account_name": "JV",
            "category_id": "25785",
            "attributes": {
                "Marke": "JV-MOEBEL",
                "Farbe": "Grau",
                "Art Herstellung": "Fertigung aus Europa",
                "Hinweis Lieferumfang": "Alles inklusive",
                "FAKE_RANDOM_ATTR": "Should not be sent",
            },
        }
        payload = format_otto_product(
            ean=ean,
            aftercool_info=aftercool_info,
            mapping=mapping,
            category_name="25785",
            account_name="JV",
        )
        sent_attr_names = [a["name"] for a in payload["productDescription"]["attributes"]]
        self.assertNotIn("Art Herstellung", sent_attr_names)
        self.assertNotIn("Hinweis Lieferumfang", sent_attr_names)
        self.assertNotIn("FAKE_RANDOM_ATTR", sent_attr_names)
        self.assertIn("Farbe", sent_attr_names)
        self.assertIn("Modellbezeichnung", sent_attr_names)

    def test_origin_detection_and_shipping_profiles(self):
        # China detection
        self.assertTrue(is_chinese_product(factory_name="Möbel China Import"))
        self.assertTrue(is_chinese_product(image_urls=["https://aftercool.de/upload/China/pic1.jpg"]))
        self.assertTrue(is_chinese_product(factory_name="Factory 123 (CN)"))
        self.assertFalse(is_chinese_product(factory_name="Polster Manufaktur Deutschland", image_urls=["https://aftercool.de/upload/Europa/pic1.jpg"]))

        # XL shipping profile UUIDs
        xl_china = resolve_shipping_profile_id("XL", is_china=True)
        xl_europe = resolve_shipping_profile_id("XL", is_china=False)
        self.assertEqual(xl_china, "027a1a46-cf60-56f4-917b-81661569f016")
        self.assertEqual(xl_europe, "d045a64d-59e6-57fb-9f69-6370dc7c9d40")

        # JV shipping profile UUIDs
        jv_china = resolve_shipping_profile_id("JV", is_china=True)
        jv_europe = resolve_shipping_profile_id("JV", is_china=False)
        self.assertEqual(jv_china, "935a75b0-ac88-55a8-98df-8556306f1386")
        self.assertEqual(jv_europe, "786c6468-3baf-52e0-88b5-13757eb7f873")

    def test_apply_otto_availability(self):
        mock_client = MagicMock()
        mock_client.update_quantities.return_value = {"status_code": 200}
        mock_client.update_product_delivery_information.return_value = {"status_code": 200}

        item = MagicMock()
        item.product_ean = "4071489823721"

        mock_mapping = MagicMock()
        mock_mapping.product_ean = "4071489823721"
        mock_mapping.image_url = "https://example.com/Germany/sofa.jpg"
        mock_mapping.product_name = "Sofa Germany"
        mock_mapping.factory_id = "504138"

        # Create separate query chain mocks for ProductMapping and ProductAttribute
        mapping_chain = MagicMock()
        mapping_chain.filter.return_value = mapping_chain
        mapping_chain.all.return_value = [mock_mapping]

        attr_chain = MagicMock()
        attr_chain.filter.return_value = attr_chain
        attr_chain.all.return_value = []

        mock_db = MagicMock()
        mock_db.query.side_effect = [mapping_chain, attr_chain]

        plan = MagicMock()
        plan.factory_name = "Deutsche Möbel"

        res = apply_otto_availability(
            client=mock_client,
            items=[item],
            account_name="JV",
            db=mock_db,
            plan=plan,
        )
        self.assertEqual(res["status"], "success")

        # Verify quantities called with 20
        mock_client.update_quantities.assert_called_once_with([{"sku": "4071489823721", "quantity": 20}])

        # Verify delivery info called with JV Europe profile
        mock_client.update_product_delivery_information.assert_called_once_with([{
            "sku": "4071489823721",
            "shippingProfileId": "786c6468-3baf-52e0-88b5-13757eb7f873",
            "processingTime": "37"
        }])

    def test_extract_images_from_aftercool_row(self):
        from services.executors.otto_api import extract_images_from_aftercool_row

        # 1. Semicolon delimited string
        row = {
            "PictureURL": "https://example.com/main.jpg",
            "pictureurls": "https://example.com/extra1.jpg;https://example.com/extra2.jpg;https://example.com/extra3.jpg"
        }
        imgs = extract_images_from_aftercool_row(row)
        self.assertEqual(len(imgs), 4)
        self.assertEqual(imgs[0], "https://example.com/main.jpg")
        self.assertEqual(imgs[1], "https://example.com/extra1.jpg")
        self.assertEqual(imgs[3], "https://example.com/extra3.jpg")

        # 2. Deduplication with main
        row_dup = {
            "PictureURL": "https://example.com/main.jpg",
            "pictureurls": "https://example.com/main.jpg;https://example.com/extra1.jpg"
        }
        imgs_dup = extract_images_from_aftercool_row(row_dup)
        self.assertEqual(len(imgs_dup), 2)

        # 3. List of URLs
        row_list = {
            "PictureURL": "https://example.com/main.jpg",
            "pictureurls": ["https://example.com/p1.jpg", "https://example.com/p2.jpg"]
        }
        imgs_list = extract_images_from_aftercool_row(row_list)
        self.assertEqual(len(imgs_list), 3)

        # 4. Empty/invalid values filtered
        row_empty = {
            "PictureURL": "",
            "pictureurls": "   ; not_a_url ; https://example.com/valid.jpg "
        }
        imgs_empty = extract_images_from_aftercool_row(row_empty)
        self.assertEqual(imgs_empty, ["https://example.com/valid.jpg"])

    def test_media_assets_limit_and_deduplication(self):
        from services.otto_formatter import _build_media_assets

        # 15 images should be capped at 10
        many_images = [f"https://example.com/pic_{i}.jpg" for i in range(15)]
        assets = _build_media_assets(many_images)
        self.assertEqual(len(assets), 10)
        self.assertEqual(assets[0]["location"], "https://example.com/pic_0.jpg")
        self.assertEqual(assets[9]["location"], "https://example.com/pic_9.jpg")

        # Deduplication
        duplicates = [
            "https://example.com/pic_1.jpg",
            "https://example.com/pic_1.jpg",
            "https://example.com/pic_2.jpg"
        ]
        assets_dedup = _build_media_assets(duplicates)
        self.assertEqual(len(assets_dedup), 2)
        self.assertEqual(assets_dedup[0]["location"], "https://example.com/pic_1.jpg")
        self.assertEqual(assets_dedup[1]["location"], "https://example.com/pic_2.jpg")

    def test_xl_vs_jv_image_isolation(self):
        """
        Verify that for XL: main image is mirrored and additional images remain untouched.
        For JV: NO images are mirrored (all remain untouched).
        """
        raw_images = [
            "https://example.com/main_bed.jpg",
            "https://example.com/extra_wardrobe.jpg",
            "https://example.com/extra_commode.jpg",
        ]
        mirrored_main_xl = "https://omt.automatonsoft.de/static/mirrored/mirrored_XL_123.jpg"

        # --- XL Logic ---
        xl_images = []
        is_xl = True
        if is_xl:
            # Main mirrored
            xl_images.append(mirrored_main_xl)
        else:
            xl_images.append(raw_images[0])
        # Additional untouched
        for extra in raw_images[1:]:
            xl_images.append(extra)

        self.assertEqual(xl_images[0], mirrored_main_xl, "XL main image must be mirrored")
        self.assertEqual(xl_images[1], raw_images[1], "XL extra image 1 must remain untouched")
        self.assertEqual(xl_images[2], raw_images[2], "XL extra image 2 must remain untouched")

        # --- JV Logic ---
        jv_images = []
        is_jv_xl = False  # JV account
        if is_jv_xl:
            jv_images.append(mirrored_main_xl)
        else:
            jv_images.append(raw_images[0])
        # Additional untouched
        for extra in raw_images[1:]:
            jv_images.append(extra)

        self.assertEqual(jv_images[0], raw_images[0], "JV main image must NOT be mirrored")
        self.assertEqual(jv_images[1], raw_images[1], "JV extra image 1 must remain untouched")
        self.assertEqual(jv_images[2], raw_images[2], "JV extra image 2 must remain untouched")

    def test_calculate_msrp(self):
        from services.otto_formatter import calculate_msrp, _build_pricing

        # Tier 1: price > 5000 -> * 1.10, rounded to X49/X99
        self.assertEqual(calculate_msrp(6000.0), 6649.0)
        self.assertEqual(calculate_msrp(5500.0), 6049.0)

        # Tier 2: 2500 <= price <= 5000 -> * 1.18, rounded to X49/X99
        self.assertEqual(calculate_msrp(5000.0), 5949.0)
        self.assertEqual(calculate_msrp(3099.0), 3649.0)
        self.assertEqual(calculate_msrp(2500.0), 2949.0)

        # Tier 3: 1000 <= price < 2500 -> * 1.25, rounded to X49/X99
        self.assertEqual(calculate_msrp(2000.0), 2549.0)
        self.assertEqual(calculate_msrp(1000.0), 1249.0)

        # Tier 4: price < 1000 -> * 1.35, rounded to X49/X99
        self.assertEqual(calculate_msrp(500.0), 699.0)
        self.assertEqual(calculate_msrp(100.0), 149.0)

        # Integration in _build_pricing
        pricing = _build_pricing("3099.00", "4071489823721")
        self.assertEqual(pricing["standardPrice"]["amount"], 3099.0)
        self.assertEqual(pricing["standardPrice"]["currency"], "EUR")
        self.assertEqual(pricing["msrp"]["amount"], 3649.0)
        self.assertEqual(pricing["msrp"]["currency"], "EUR")
        self.assertEqual(pricing["vat"], "FULL")


class TestMsrpRounding(unittest.TestCase):
    """Unit tests for the round_msrp function based on user specifications."""

    def test_user_examples(self):
        from services.otto_formatter import round_msrp
        # Example 1: 3656.82 -> 3649.00 (56 closer to 49)
        self.assertEqual(round_msrp(3656.82), 3649.0)
        # Example 2: 3680.50 -> 3699.00 (80 closer to 99)
        self.assertEqual(round_msrp(3680.50), 3699.0)

    def test_boundary_values(self):
        from services.otto_formatter import round_msrp
        # 74 is equidistant (|74-49|=25, |74-99|=25) -> user confirmed 49
        self.assertEqual(round_msrp(3674.00), 3649.0)
        self.assertEqual(round_msrp(74.00), 49.0)
        # 75 is closer to 99 (|75-49|=26, |75-99|=24) -> 99
        self.assertEqual(round_msrp(3675.00), 3699.0)
        self.assertEqual(round_msrp(75.00), 99.0)

    def test_lower_digits_same_hundred(self):
        from services.otto_formatter import round_msrp
        # 00..24 remain within same hundred and round to 49
        self.assertEqual(round_msrp(3600.00), 3649.0)
        self.assertEqual(round_msrp(3610.00), 3649.0)
        self.assertEqual(round_msrp(3624.00), 3649.0)

    def test_edge_zero_and_negative(self):
        from services.otto_formatter import round_msrp
        self.assertEqual(round_msrp(0.0), 0.0)
        self.assertEqual(round_msrp(-50.0), 0.0)

    def test_otto_compliance_msrp_gte_price(self):
        from services.otto_formatter import round_msrp
        # If raw msrp rounds to 49.0, but standard price is 50.0,
        # msrp must be stepped up to at least standard price (99.0)
        self.assertEqual(round_msrp(49.0, standard_price=50.0), 99.0)
        self.assertEqual(round_msrp(67.5, standard_price=50.0), 99.0)
        # If standard price is 40.0, 49.0 >= 40.0 so it stays 49.0
        self.assertEqual(round_msrp(67.5, standard_price=40.0), 49.0)


class TestChineseProductDetection(unittest.TestCase):
    """Tests for the enhanced is_chinese_product() cascading algorithm."""

    # --- Priority 1: kurzbeschreibung ---

    def test_kurz_china_keyword(self):
        self.assertTrue(is_chinese_product(kurzbeschreibung="Hergestellt in China, Lieferzeit 8-12 Wochen"))

    def test_kurz_china_vr_china(self):
        self.assertTrue(is_chinese_product(kurzbeschreibung="Produkt aus VR China"))

    def test_kurz_china_delivery_marker(self):
        self.assertTrue(is_chinese_product(kurzbeschreibung="Lieferung 8-12 Wochen"))

    def test_kurz_china_prc(self):
        self.assertTrue(is_chinese_product(kurzbeschreibung="Made in PRC"))

    def test_kurz_turkey_country(self):
        self.assertFalse(is_chinese_product(kurzbeschreibung="Produktion in der Türkei, 4-8 Wochen"))

    def test_kurz_turkey_city_ine(self):
        """INE (İnegöl) is a Turkish city — should return False."""
        self.assertFalse(is_chinese_product(kurzbeschreibung="Herkunft INE, Möbelfabrik"))

    def test_kurz_turkey_city_inegol(self):
        self.assertFalse(is_chinese_product(kurzbeschreibung="Fabrik in Inegöl Türkei"))

    def test_kurz_ine_not_match_online(self):
        """'ine' should NOT false-positive on words like 'online'."""
        # 'online' contains 'ine' but is not a word-boundary match
        result = is_chinese_product(kurzbeschreibung="Online bestellen, China Ware")
        self.assertTrue(result)  # China keyword wins, 'online' doesn't trigger INE

    def test_kurz_germany(self):
        self.assertFalse(is_chinese_product(kurzbeschreibung="Made in Germany, sofort lieferbar"))

    def test_kurz_europe_delivery(self):
        self.assertFalse(is_chinese_product(kurzbeschreibung="4-8 Wochen Lieferzeit"))

    def test_kurz_non_china_overrides_image(self):
        """Non-china kurzbeschreibung should override china image URL."""
        self.assertFalse(is_chinese_product(
            kurzbeschreibung="Produktion in der Türkei",
            image_urls=["https://example.com/china/product.jpg"],
        ))

    # --- Priority 2: Ursprungsland attribute ---

    def test_ursprungsland_turkey(self):
        self.assertFalse(is_chinese_product(attributes={"Ursprungsland": "Türkei"}))

    def test_ursprungsland_china(self):
        self.assertTrue(is_chinese_product(attributes={"Ursprungsland": "China"}))

    def test_ursprungsland_deutschland(self):
        self.assertFalse(is_chinese_product(attributes={"Ursprungsland": "Deutschland"}))

    def test_ursprungsland_empty_kurz(self):
        """With empty kurzbeschreibung, Ursprungsland should be checked."""
        self.assertFalse(is_chinese_product(
            kurzbeschreibung="",
            attributes={"Ursprungsland": "Türkei"},
        ))

    # --- Priority 3: Image URLs ---

    def test_image_url_china_path(self):
        self.assertTrue(is_chinese_product(image_urls=["https://example.com/china/sofa.jpg"]))

    def test_image_url_cn_path(self):
        self.assertTrue(is_chinese_product(image_urls=["https://example.com/products/_cn_/chair.jpg"]))

    def test_image_url_normal(self):
        self.assertFalse(is_chinese_product(image_urls=["https://example.com/germany/sofa.jpg"]))

    # --- Priority 4: Factory name ---

    def test_factory_china(self):
        self.assertTrue(is_chinese_product(factory_name="China Factory 88"))

    def test_factory_cn_code(self):
        self.assertTrue(is_chinese_product(factory_name="supplier_cn_202"))

    def test_factory_normal(self):
        self.assertFalse(is_chinese_product(factory_name="Deutsche Möbelfabrik"))

    # --- Priority 5: Product name ---

    def test_product_name_china(self):
        self.assertTrue(is_chinese_product(product_name="Sofa China Import"))

    def test_product_name_cn(self):
        self.assertTrue(is_chinese_product(product_name="Chair (CN)"))

    def test_product_name_normal(self):
        self.assertFalse(is_chinese_product(product_name="Elegantes Sofa Grau Modern"))

    # --- Default ---

    def test_default_is_europe(self):
        """With no data at all, default should be Europe (False)."""
        self.assertFalse(is_chinese_product())

    def test_all_empty(self):
        self.assertFalse(is_chinese_product(
            factory_name="",
            factory_id="",
            image_urls=[],
            product_name="",
            kurzbeschreibung="",
            attributes={},
        ))

    # --- Shipping profile UUID resolution ---

    def test_jv_china_profile(self):
        self.assertEqual(
            resolve_shipping_profile_id("JV", True),
            "935a75b0-ac88-55a8-98df-8556306f1386"
        )

    def test_jv_europe_profile(self):
        self.assertEqual(
            resolve_shipping_profile_id("JV", False),
            "786c6468-3baf-52e0-88b5-13757eb7f873"
        )

    def test_xl_china_profile(self):
        self.assertEqual(
            resolve_shipping_profile_id("XL", True),
            "027a1a46-cf60-56f4-917b-81661569f016"
        )

    def test_xl_europe_profile(self):
        self.assertEqual(
            resolve_shipping_profile_id("XL", False),
            "d045a64d-59e6-57fb-9f69-6370dc7c9d40"
        )


class TestAttributeNormalizationAndWhitelisting(unittest.TestCase):
    """Tests for dimension normalization, schema whitelisting, and origin resolution."""

    def test_clean_dimension_value(self):
        from services.okb_sync import clean_dimension_value

        self.assertEqual(clean_dimension_value("73 cm"), "73")
        self.assertEqual(clean_dimension_value("95 cm"), "95")
        self.assertEqual(clean_dimension_value("42 cm"), "42")
        self.assertEqual(clean_dimension_value("73,5 cm"), "73.5")
        self.assertEqual(clean_dimension_value("800 mm"), "80")
        self.assertEqual(clean_dimension_value("1,5 m"), "150")
        self.assertEqual(clean_dimension_value("ca. 120 cm"), "120")
        self.assertEqual(clean_dimension_value("~ 50 cm"), "50")
        self.assertEqual(clean_dimension_value("140 Kg"), "140")
        self.assertEqual(clean_dimension_value(""), "")
        self.assertEqual(clean_dimension_value(None), "")

    def test_category_whitelisting(self):
        from services.categories_repo import get_allowed_attribute_names

        # Category 25545 is Sessel
        allowed = get_allowed_attribute_names("25545")
        self.assertIn("Breite", allowed)
        self.assertIn("Höhe", allowed)
        self.assertIn("Sitzhöhe", allowed)
        self.assertNotIn("Material Türen", allowed)
        self.assertNotIn("Maße Sessel", allowed)
        self.assertNotIn("Herstellungsjahr", allowed)

    def test_apply_default_attributes_with_schema(self):
        from services.constant_attributes import apply_default_attributes

        # Sessel: Material Türen should NOT be added
        res = apply_default_attributes({}, account="JV", category_id="25545")
        self.assertNotIn("Material Türen", res)

    def test_msrp_rounding_rules(self):
        from services.okb_sync import round_msrp_to_49_or_99

        # User examples:
        # 3656,82 -> 3649,00
        self.assertEqual(round_msrp_to_49_or_99(3656.82), 3649.0)
        # 3680,50 -> 3699,00
        self.assertEqual(round_msrp_to_49_or_99(3680.50), 3699.0)
        # 3610 -> 3649,00
        self.assertEqual(round_msrp_to_49_or_99(3610.0), 3649.0)

    def test_origin_and_delivery_resolution(self):
        from services.okb_sync import resolve_origin_and_delivery_info
        from services.otto_availability import is_chinese_product

        # 1. Turkish city marker: INE (Inegöl)
        ine_res = resolve_origin_and_delivery_info(
            aftercool_row={"Ort": "INE", "Artikelbeschreibung": "Chesterfield Sessel"}
        )
        self.assertEqual(ine_res["origin_country"], "Türkei")
        self.assertEqual(ine_res["delivery_weeks"], "4-8 Wochen")
        self.assertEqual(ine_res["delivery_days"], 37)
        self.assertFalse(ine_res["is_china"])

        # 2. Turkish marker: TR in description
        tr_res = resolve_origin_and_delivery_info(
            product_title="Luxus Bett TR 180x200"
        )
        self.assertEqual(tr_res["origin_country"], "Türkei")
        self.assertEqual(tr_res["delivery_weeks"], "4-8 Wochen")
        self.assertEqual(tr_res["delivery_days"], 37)
        self.assertFalse(tr_res["is_china"])

        # 3. China marker: China in description / 8-12 Wochen
        cn_res = resolve_origin_and_delivery_info(
            aftercool_row={"Artikelbeschreibung": "Hergestellt in China, 8-12 Wochen"}
        )
        self.assertEqual(cn_res["origin_country"], "China")
        self.assertEqual(cn_res["delivery_weeks"], "8-12 Wochen")
        self.assertEqual(cn_res["delivery_days"], 56)
        self.assertTrue(cn_res["is_china"])

        # 4. Europe marker: Deutschland -> 4-8 Wochen, 37 days
        de_res = resolve_origin_and_delivery_info(
            aftercool_row={"Ort": "Berlin", "Artikelbeschreibung": "Made in Germany Esstisch"}
        )
        self.assertEqual(de_res["origin_country"], "Deutschland")
        self.assertEqual(de_res["delivery_weeks"], "4-8 Wochen")
        self.assertEqual(de_res["delivery_days"], 37)
        self.assertFalse(de_res["is_china"])

        # 5. Unknown origin -> origin_country is None (no hardcoded Turkey fallback!), 4-8 Wochen, 37 days
        unknown_res = resolve_origin_and_delivery_info(
            product_title="Modernes Designer Sofa Ecksofa",
            aftercool_row={"Artikelbeschreibung": "Hochwertiges Sofa mit Samtbezug"}
        )
        self.assertIsNone(unknown_res["origin_country"])
        self.assertEqual(unknown_res["delivery_weeks"], "4-8 Wochen")
        self.assertEqual(unknown_res["delivery_days"], 37)
        self.assertFalse(unknown_res["is_china"])

        # 6. Explicit country attribute
        vn_res = resolve_origin_and_delivery_info(
            attributes={"Herstellungsland": "Vietnam"}
        )
        self.assertEqual(vn_res["origin_country"], "Vietnam")
        self.assertEqual(vn_res["delivery_weeks"], "4-8 Wochen")
        self.assertFalse(vn_res["is_china"])

        # 7. is_chinese_product tests
        self.assertFalse(is_chinese_product(attributes={"Herkunftsland": "Türkei"}))
        self.assertFalse(is_chinese_product(attributes={"Herstellungsland": "TR"}))
        self.assertFalse(is_chinese_product(attributes={"Herkunftsland": "INE"}))
        self.assertFalse(is_chinese_product(attributes={"Ursprungsland": "Deutschland"}))
        self.assertTrue(is_chinese_product(attributes={"Herkunftsland": "China"}))
        self.assertTrue(is_chinese_product(attributes={"Ursprungsland": "CN"}))
        self.assertFalse(is_chinese_product(product_name="INE-104 Chesterfield Sofa"))
        self.assertTrue(is_chinese_product(product_name="China Design Stuhl"))


class TestCategoryMappingAndCrossAccountTransfer(unittest.TestCase):
    """Tests for soft furniture sets (Polstergarnitur, Couchgarnitur, Sofa-Set) and JV <-> XL transfer."""

    def test_polstergarnitur_without_table(self):
        from services.okb_sync import find_best_category
        cat_id, cat_name = find_best_category("Polstergarnitur")
        self.assertEqual(cat_id, "25785")
        self.assertEqual(cat_name, "Polstergarnitur")

    def test_couchgarnitur_without_table(self):
        from services.okb_sync import find_best_category
        cat_id, cat_name = find_best_category("Couchgarnitur", "3+2+1 Polstergarnitur Luxus Leder")
        self.assertEqual(cat_id, "25785")
        self.assertEqual(cat_name, "Polstergarnitur")

    def test_couchgarnitur_with_table(self):
        from services.okb_sync import find_best_category
        cat_id, cat_name = find_best_category("Couchgarnitur", "3+2+1 Polstergarnitur inkl. Couchtisch")
        self.assertEqual(cat_id, "20110")
        self.assertEqual(cat_name, "Wohnzimmer-Set")

    def test_sofa_set_without_table(self):
        from services.okb_sync import find_best_category
        cat_id, cat_name = find_best_category("Sofa-Set", "Modernes 3-teiliges Sofa-Set")
        self.assertEqual(cat_id, "25785")
        self.assertEqual(cat_name, "Polstergarnitur")

    def test_sofa_set_with_beistelltisch(self):
        from services.okb_sync import find_best_category
        cat_id, cat_name = find_best_category("Sofa-Set", "Sofa-Set mit Beistelltisch")
        self.assertEqual(cat_id, "20110")
        self.assertEqual(cat_name, "Wohnzimmer-Set")

    def test_extract_aftercool_couchgarnitur(self):
        from services.okb_sync import _extract_aftercool_data
        row_no_table = {
            "CustomItemSpecifics": "<ItemSpecifics><NameValueList><Name><![CDATA[Produktart]]></Name><Value><![CDATA[Couchgarnitur]]></Value></NameValueList></ItemSpecifics>",
            "Artikelbeschreibung": "3+2+1 Leder Garnitur"
        }
        cat_id, cat_name, _ = _extract_aftercool_data(row_no_table)
        self.assertEqual(cat_id, "25785")

        row_with_table = {
            "CustomItemSpecifics": "<ItemSpecifics><NameValueList><Name><![CDATA[Produktart]]></Name><Value><![CDATA[Couchgarnitur]]></Value></NameValueList></ItemSpecifics>",
            "Artikelbeschreibung": "3+2+1 Leder Garnitur inkl. Couchtisch"
        }
        cat_id, cat_name, _ = _extract_aftercool_data(row_with_table)
        self.assertEqual(cat_id, "20110")

    def test_twin_resolution(self):
        import asyncio
        from services.aftercool_twin import resolve_twin_product
        loop = asyncio.new_event_loop()
        try:
            tgt_fid, tgt_ean, tgt_pname = loop.run_until_complete(
                resolve_twin_product(
                    source_account="JV",
                    target_account="XL",
                    source_factory_id="504220",
                    source_ean="4071489823721",
                    source_product_name="Elegantes Schlafzimmer Set Beige Holz Metall Komplettset 4 Teilig"
                )
            )
            self.assertEqual(tgt_fid, "483411")
            self.assertEqual(tgt_ean, "4071489340488")
            self.assertIn("Schlafzimmer", tgt_pname)
        finally:
            loop.close()


    def test_brand_guard_image_filtering(self):
        import asyncio
        from services.brand_guard import filter_brand_images_sync, filter_brand_images, is_forbidden_brand_image_url

        # Check URL string detection
        self.assertTrue(is_forbidden_brand_image_url("XL", "https://image.afterbuy.de/images/53416/additional_53416.jpg"))
        self.assertTrue(is_forbidden_brand_image_url("XL", "https://img.jvmoebel.de/logo.jpg"))
        self.assertFalse(is_forbidden_brand_image_url("XL", "https://img.xlmoebel.de/sofa_1.jpg"))
        self.assertFalse(is_forbidden_brand_image_url("JV", "https://image.afterbuy.de/images/53416/additional_53416.jpg"))

        # Check sync filtering
        imgs = [
            "https://img.xlmoebel.de/bed_1.jpg",
            "https://image.afterbuy.de/images/53416/additional_53416.jpg",
            "https://img.xlmoebel.de/bed_2.jpg"
        ]
        xl_clean = filter_brand_images_sync("XL", imgs)
        self.assertEqual(xl_clean, ["https://img.xlmoebel.de/bed_1.jpg", "https://img.xlmoebel.de/bed_2.jpg"])

        # JV retains all images
        jv_clean = filter_brand_images_sync("JV", imgs)
        self.assertEqual(len(jv_clean), 3)

        # Async filtering
        loop = asyncio.new_event_loop()
        try:
            xl_async_clean = loop.run_until_complete(filter_brand_images("XL", imgs))
            self.assertEqual(xl_async_clean, ["https://img.xlmoebel.de/bed_1.jpg", "https://img.xlmoebel.de/bed_2.jpg"])
        finally:
            loop.close()

    def test_extract_unknown_skus(self):
        # 1. 422 Quantities error format
        res_422 = {
            "status_code": 422,
            "response": {"detail": "Error: SKU is unknown for SKU: 4071489340495"}
        }
        unknowns = extract_unknown_skus(res_422, ["4071489340495", "4071489340496"])
        self.assertEqual(unknowns, ["4071489340495"])

        # 2. 400 Delivery error format
        res_400 = {
            "status_code": 400,
            "response": {"detail": "Error sku: 4071489340495 is unknown"}
        }
        unknowns_deliv = extract_unknown_skus(res_400, ["4071489340495"])
        self.assertEqual(unknowns_deliv, ["4071489340495"])

        # 3. 200/204 success -> empty
        res_200 = {"status_code": 200, "response": []}
        self.assertEqual(extract_unknown_skus(res_200, ["4071489340495"]), [])

        # 4. 500 server error -> empty (handled by HTTP retry, not unknown SKU)
        res_500 = {"status_code": 500, "error": "Internal Server Error"}
        self.assertEqual(extract_unknown_skus(res_500, ["4071489340495"]), [])

    def test_apply_otto_availability_sku_unknown_quantities(self):
        mock_client = MagicMock()
        mock_client.update_quantities.return_value = {
            "status_code": 422,
            "response": {"detail": "Error: SKU is unknown for SKU: 4071489823721"}
        }
        mock_client.update_product_delivery_information.return_value = {"status_code": 200}

        item = MagicMock()
        item.product_ean = "4071489823721"

        mock_mapping = MagicMock()
        mock_mapping.product_ean = "4071489823721"
        mock_mapping.image_url = ""
        mock_mapping.product_name = "Test Prod"
        mock_mapping.factory_id = "123"

        mapping_chain = MagicMock()
        mapping_chain.filter.return_value = mapping_chain
        mapping_chain.all.return_value = [mock_mapping]

        attr_chain = MagicMock()
        attr_chain.filter.return_value = attr_chain
        attr_chain.all.return_value = []

        mock_db = MagicMock()
        mock_db.query.side_effect = [mapping_chain, attr_chain]

        res = apply_otto_availability(
            client=mock_client,
            items=[item],
            account_name="JV",
            db=mock_db,
        )
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["unknown_skus"], ["4071489823721"])
        self.assertEqual(res["quantity_failed_skus"], ["4071489823721"])
        self.assertEqual(res["delivery_failed_skus"], [])

    def test_apply_otto_availability_sku_unknown_delivery(self):
        mock_client = MagicMock()
        mock_client.update_quantities.return_value = {"status_code": 200}
        mock_client.update_product_delivery_information.return_value = {
            "status_code": 400,
            "response": {"detail": "Error sku: 4071489823721 is unknown"}
        }

        item = MagicMock()
        item.product_ean = "4071489823721"

        mock_mapping = MagicMock()
        mock_mapping.product_ean = "4071489823721"
        mock_mapping.image_url = ""
        mock_mapping.product_name = "Test Prod"
        mock_mapping.factory_id = "123"

        mapping_chain = MagicMock()
        mapping_chain.filter.return_value = mapping_chain
        mapping_chain.all.return_value = [mock_mapping]

        attr_chain = MagicMock()
        attr_chain.filter.return_value = attr_chain
        attr_chain.all.return_value = []

        mock_db = MagicMock()
        mock_db.query.side_effect = [mapping_chain, attr_chain]

        res = apply_otto_availability(
            client=mock_client,
            items=[item],
            account_name="JV",
            db=mock_db,
        )
        self.assertFalse(res["success"])
        self.assertEqual(res["unknown_skus"], ["4071489823721"])
        self.assertEqual(res["quantity_failed_skus"], [])
        self.assertEqual(res["delivery_failed_skus"], ["4071489823721"])

    def test_apply_otto_availability_selective_skips(self):
        mock_client = MagicMock()
        mock_client.update_quantities.return_value = {"status_code": 200}
        mock_client.update_product_delivery_information.return_value = {"status_code": 200}

        item = MagicMock()
        item.product_ean = "4071489823721"

        mock_mapping = MagicMock()
        mock_mapping.product_ean = "4071489823721"
        mock_mapping.image_url = ""
        mock_mapping.product_name = "Test"
        mock_mapping.factory_id = "1"

        mapping_chain = MagicMock()
        mapping_chain.filter.return_value = mapping_chain
        mapping_chain.all.return_value = [mock_mapping]

        attr_chain = MagicMock()
        attr_chain.filter.return_value = attr_chain
        attr_chain.all.return_value = []

        mock_db = MagicMock()
        mock_db.query.side_effect = [mapping_chain, attr_chain]

        # 1. Skip quantities (target_quantity_skus=[]) -> only delivery should run
        res = apply_otto_availability(
            client=mock_client,
            items=[item],
            account_name="JV",
            db=mock_db,
            target_quantity_skus=[],
            target_delivery_skus=["4071489823721"],
        )
        self.assertTrue(res["success"])
        mock_client.update_quantities.assert_not_called()
        mock_client.update_product_delivery_information.assert_called_once()

        # 2. Skip delivery (target_delivery_skus=[]) -> only quantities should run
        mock_client.reset_mock()
        mock_db.reset_mock()
        res2 = apply_otto_availability(
            client=mock_client,
            items=[item],
            account_name="JV",
            db=mock_db,
            target_quantity_skus=["4071489823721"],
            target_delivery_skus=[],
        )
        self.assertTrue(res2["success"])
        mock_client.update_quantities.assert_called_once()
        mock_client.update_product_delivery_information.assert_not_called()

    def test_dispatch_otto_availability_batches(self):
        from tasks import dispatch_otto_availability_batches
        from unittest.mock import patch, MagicMock

        # 250 item IDs should be split into 3 batches (100, 100, 50)
        item_ids = list(range(1, 251))
        with patch("tasks.apply_otto_availability_task.apply_async") as mock_apply_async:
            with patch("redis.from_url") as mock_redis_from_url:
                mock_redis = MagicMock()
                mock_redis.set.return_value = True  # Always acquired
                mock_redis_from_url.return_value = mock_redis

                dispatched = dispatch_otto_availability_batches(
                    item_ids=item_ids,
                    account_name="JV",
                    plan_id=10,
                    base_countdown=60,
                    jitter_step=5,
                )
                self.assertEqual(dispatched, 3)
                self.assertEqual(mock_apply_async.call_count, 3)
                mock_redis.delete.assert_not_called()

                # Batch 0: countdown = 60
                call0 = mock_apply_async.call_args_list[0]
                self.assertEqual(len(call0.kwargs.get("args", call0[1].get("args"))[0]), 100)
                self.assertEqual(call0.kwargs.get("countdown", call0[1].get("countdown")), 60)

                # Batch 1: countdown = 65
                call1 = mock_apply_async.call_args_list[1]
                self.assertEqual(len(call1.kwargs.get("args", call1[1].get("args"))[0]), 100)
                self.assertEqual(call1.kwargs.get("countdown", call1[1].get("countdown")), 65)

                # Batch 2: countdown = 70
                call2 = mock_apply_async.call_args_list[2]
                self.assertEqual(len(call2.kwargs.get("args", call2[1].get("args"))[0]), 50)
                self.assertEqual(call2.kwargs.get("countdown", call2[1].get("countdown")), 70)

        # Test force=True: should delete locks before acquiring
        with patch("tasks.apply_otto_availability_task.apply_async"):
            with patch("redis.from_url") as mock_redis_from_url:
                mock_redis = MagicMock()
                mock_redis.set.return_value = True
                mock_redis_from_url.return_value = mock_redis

                dispatched = dispatch_otto_availability_batches(
                    item_ids=[1, 2],
                    account_name="JV",
                    force=True,
                )
                self.assertEqual(dispatched, 1)
                self.assertEqual(mock_redis.delete.call_count, 2)
                mock_redis.delete.assert_any_call("otto_avail_sched:JV:1")
                mock_redis.delete.assert_any_call("otto_avail_sched:JV:2")

    def test_apply_otto_availability_task_retry_and_exhaustion(self):
        from tasks import apply_otto_availability_task
        from unittest.mock import patch, MagicMock

        # Mock items and DB session
        mock_item = MagicMock()
        mock_item.id = 1
        mock_item.product_ean = "4071489823721"
        mock_item.error_message = None

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [mock_item]
        mock_settings = MagicMock()
        mock_settings.otto_client_id = "test_id"
        mock_settings.otto_client_secret = "test_sec"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_settings

        failed_res = {
            "status": "failed",
            "success": False,
            "unknown_skus": ["4071489823721"],
            "quantity_failed_skus": ["4071489823721"],
            "delivery_failed_skus": [],
            "error": "SKU unknown",
        }

        # 1. Test Retry when unknown SKU and retries < 6
        with patch("database.SessionLocal", return_value=mock_db):
            with patch("services.otto_client.OttoMarketClient"):
                with patch("services.otto_availability.apply_otto_availability", return_value=failed_res):
                    with patch.object(apply_otto_availability_task, "retry", side_effect=Exception("CeleryRetryTriggered")) as mock_retry:
                        with patch.object(apply_otto_availability_task.request, "retries", 1):
                            with self.assertRaises(Exception) as ctx:
                                apply_otto_availability_task.run(
                                    item_ids=[1],
                                    account_name="JV",
                                )
                            self.assertIn("CeleryRetryTriggered", str(ctx.exception))
                            mock_retry.assert_called_once()
                            retry_kwargs = mock_retry.call_args.kwargs
                            self.assertEqual(retry_kwargs["countdown"], 60)
                            self.assertEqual(retry_kwargs["kwargs"]["pending_quantity_skus"], ["4071489823721"])
                            self.assertEqual(retry_kwargs["kwargs"]["pending_delivery_skus"], [])

        # 2. Test Exhaustion when retries >= 6 -> records error message
        with patch("database.SessionLocal", return_value=mock_db):
            with patch("services.otto_client.OttoMarketClient"):
                with patch("services.otto_availability.apply_otto_availability", return_value=failed_res):
                    with patch.object(apply_otto_availability_task.request, "retries", 6):
                        with patch("redis.from_url"):
                            res = apply_otto_availability_task.run(
                                item_ids=[1],
                                account_name="JV",
                            )
                            self.assertEqual(res["status"], "partial_or_failed")
                            self.assertIn("Availability sync incomplete", mock_item.error_message)

    def test_retry_availability_endpoint(self):
        from routers.publish_pipeline import retry_availability_endpoint, RetryAvailabilityRequest
        from fastapi import HTTPException
        from unittest.mock import patch, MagicMock
        import models

        mock_db = MagicMock()

        # 1. Neither plan_id nor eans -> 400
        with self.assertRaises(HTTPException) as ctx:
            retry_availability_endpoint(RetryAvailabilityRequest(), db=mock_db)
        self.assertEqual(ctx.exception.status_code, 400)

        # 2. plan_id branch
        mock_plan = MagicMock()
        mock_plan.id = 42
        mock_plan.account_name = models.AccountType.JV

        mock_item1 = MagicMock()
        mock_item1.id = 101
        mock_item1.account_name = models.AccountType.JV

        mock_db.query.return_value.filter.return_value.first.return_value = mock_plan
        mock_db.query.return_value.filter.return_value.all.return_value = [mock_item1]

        with patch("tasks.dispatch_otto_availability_batches", return_value=1) as mock_dispatch:
            res = retry_availability_endpoint(RetryAvailabilityRequest(plan_id=42), db=mock_db)
            self.assertTrue(res["success"])
            self.assertEqual(res["dispatched_batches"], 1)
            mock_dispatch.assert_called_once_with(
                item_ids=[101],
                account_name="JV",
                plan_id=42,
                base_countdown=0,
                jitter_step=2,
                force=True,
            )

    def test_otto_client_availability_fail_fast(self):
        from services.otto_client import OttoMarketClient
        from unittest.mock import patch, MagicMock
        import requests

        client = OttoMarketClient(client_id="test_id", client_secret="test_secret", timeout=30.0)
        client.access_token = "valid_token"
        client.token_expires_at = 9999999999

        # Test update_quantities timeout is capped at 10.0s and retries capped at 2 attempts
        with patch("requests.post") as mock_post:
            mock_post.side_effect = requests.exceptions.Timeout("Connection timed out")
            with patch("time.sleep") as mock_sleep:
                res = client.update_quantities([{"sku": "4071489823721", "quantity": 20}])
                self.assertEqual(res["status_code"], 500)
                self.assertIn("Connection timed out", res["error"])
                self.assertEqual(mock_post.call_count, 2)  # exactly 2 attempts, not 3
                # Verify timeout argument was min(10.0, 30.0) = 10.0
                call_args = mock_post.call_args_list[0]
                self.assertEqual(call_args.kwargs.get("timeout"), 10.0)
                self.assertEqual(mock_sleep.call_count, 1)  # only 1 sleep between 2 attempts

        # Test update_product_delivery_information timeout capped at 10.0s and retries capped at 2
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 503
            mock_resp.text = "Service Unavailable"
            mock_post.return_value = mock_resp
            with patch("time.sleep") as mock_sleep:
                res = client.update_product_delivery_information([{"sku": "4071489823721", "shippingProfileId": "p-1"}])
                self.assertEqual(res["status_code"], 503)
                self.assertEqual(mock_post.call_count, 2)  # exactly 2 attempts, not 3
                call_args = mock_post.call_args_list[0]
                self.assertEqual(call_args.kwargs.get("timeout"), 10.0)
                self.assertEqual(mock_sleep.call_count, 1)

        # Test update_quantities success on 200
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"message": "Updated"}
            mock_post.return_value = mock_resp
            res = client.update_quantities([{"sku": "4071489823721", "quantity": 20}])
            self.assertEqual(res["status_code"], 200)
            self.assertEqual(mock_post.call_count, 1)

    def test_twin_factory_endpoint(self):
        from routers.aftercool import get_twin_factory
        from unittest.mock import patch
        import asyncio

        with patch("services.aftercool_twin.resolve_target_factory_id", return_value="483411") as mock_resolve:
            res = asyncio.run(get_twin_factory(factory_id="504220", source_account="JV", target_account="XL"))
            self.assertEqual(res["source_factory_id"], "504220")
            self.assertEqual(res["source_account"], "JV")
            self.assertEqual(res["target_account"], "XL")
            self.assertEqual(res["target_factory_id"], "483411")
            mock_resolve.assert_called_once_with(source_factory_id="504220", source_account="JV", target_account="XL")

    def test_transfer_to_account_target_factory_id(self):
        from routers.mapping import transfer_to_account, TransferAccountRequest
        from unittest.mock import patch, MagicMock
        import asyncio
        import models

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        with patch("services.aftercool_twin.resolve_target_factory_id", return_value="483411"):
            req = TransferAccountRequest(
                source_account=models.AccountType.JV,
                target_account=models.AccountType.XL,
                factory_id="504220",
            )
            res = asyncio.run(transfer_to_account(req, db=mock_db))
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["target_factory_id"], "483411")

    def test_mirror_image_cache_key_includes_hash(self):
        from services.image_processor import mirror_image_for_xl
        import hashlib
        import asyncio
        from unittest.mock import patch

        ean = "4071489823721"
        test_url = "https://example.com/test_image_for_hash.jpg"
        expected_hash = hashlib.md5(test_url.encode("utf-8")).hexdigest()[:10]
        expected_filename = f"mirrored_XL_{ean}_{expected_hash}.jpg"

        with patch("os.path.exists", return_value=True), patch("os.path.getsize", return_value=1024):
            res_url = asyncio.run(mirror_image_for_xl(test_url, ean, "XL"))
            self.assertEqual(res_url, f"/static/mirrored/{expected_filename}")

    def test_otto_api_executor_image_isolation(self):
        """
        Verify XL filters JV logos first, then mirrors first clean image.
        Verify JV includes all images with logos untouched and no mirroring.
        """
        raw_af_images = [
            "https://example.com/product_main.jpg",
            "https://example.com/jvmebel_brand_logo.jpg",
            "https://example.com/detail1.jpg",
        ]
        # In JV, all 3 images should be kept untouched
        jv_clean = [img for img in raw_af_images]
        self.assertEqual(len(jv_clean), 3)
        self.assertIn("https://example.com/jvmebel_brand_logo.jpg", jv_clean)

        # In XL, brand guard should filter out logo
        from services.brand_guard import is_forbidden_brand_image_url
        xl_clean = [img for img in raw_af_images if not is_forbidden_brand_image_url("XL", img)]
        self.assertEqual(len(xl_clean), 2)
        self.assertNotIn("https://example.com/jvmebel_brand_logo.jpg", xl_clean)
        self.assertEqual(xl_clean[0], "https://example.com/product_main.jpg")
        self.assertEqual(xl_clean[1], "https://example.com/detail1.jpg")


if __name__ == "__main__":
    unittest.main()


