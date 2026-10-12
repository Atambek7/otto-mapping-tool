import unittest
from services.ai_title_generator import (
    is_valid_source_title,
    validate_title_type_consistency,
    detect_furniture_types
)

class TestAiTitleValidationAndGuards(unittest.TestCase):
    def test_is_valid_source_title(self):
        # Invalid / Empty / Placeholder titles
        self.assertFalse(is_valid_source_title(""))
        self.assertFalse(is_valid_source_title(None))
        self.assertFalse(is_valid_source_title("   "))
        self.assertFalse(is_valid_source_title("null"))
        self.assertFalse(is_valid_source_title("Без названия"))
        self.assertFalse(is_valid_source_title("Product 123"))
        self.assertFalse(is_valid_source_title("n/a"))
        self.assertFalse(is_valid_source_title("-"))
        self.assertFalse(is_valid_source_title("abc"))

        # Valid source titles
        self.assertTrue(is_valid_source_title("Elektrischer Relaxsessel Fernsehsessel beige Stoffbezug Verstellbar"))
        self.assertTrue(is_valid_source_title("Leder Ecksofa Wohnlandschaft braun"))
        self.assertTrue(is_valid_source_title("Massivholz Esstisch Eiche geölt"))

    def test_detect_furniture_types(self):
        sessel_types = detect_furniture_types("Elektrischer Relaxsessel Fernsehsessel beige Stoffbezug Verstellbar")
        self.assertIn("sessel", sessel_types)

        tisch_types = detect_furniture_types("Esstisch aus Holz mit ausziehbarer Funktion und 4 Stühlen")
        self.assertIn("esstisch", tisch_types)
        self.assertIn("stuhl", tisch_types)

        sofa_types = detect_furniture_types("Leder Ecksofa mit Schlaffunktion")
        self.assertIn("sofa", sofa_types)

    def test_furniture_type_consistency_rejection(self):
        # The exact reported bug: Sessel transformed into Esstisch
        orig = "Elektrischer Relaxsessel Fernsehsessel beige Stoffbezug Verstellbar"
        hallucinated = "Esstisch aus Holz mit ausziehbarer Funktion und 4 Stühlen"
        self.assertFalse(validate_title_type_consistency(orig, hallucinated))

        # Sessel transformed into Bett
        self.assertFalse(validate_title_type_consistency(orig, "Modernes Boxspringbett 180x200"))

        # Sofa transformed into Esstisch
        sofa_orig = "Leder Ecksofa Wohnlandschaft braun"
        self.assertFalse(validate_title_type_consistency(sofa_orig, "Esstisch aus Holz"))

    def test_furniture_type_consistency_acceptance(self):
        orig = "Elektrischer Relaxsessel Fernsehsessel beige Stoffbezug Verstellbar"
        valid_gen = "Beiger elektrischer Relaxsessel mit Stoffbezug"
        self.assertTrue(validate_title_type_consistency(orig, valid_gen))

        sofa_orig = "Leder Ecksofa Wohnlandschaft braun"
        valid_sofa = "Braunes Ecksofa aus Leder"
        self.assertTrue(validate_title_type_consistency(sofa_orig, valid_sofa))

if __name__ == "__main__":
    unittest.main()
