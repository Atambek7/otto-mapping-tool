import sys
from services.automapper import (
    map_to_otto_grundfarbe,
    enforce_furniture_color_rules,
    match_closest_option,
    OTTO_GRUNDFARBEN
)

def run_tests():
    print("=== TEST 1: Universal Color Spectrum to Otto Grundfarbe ===")
    color_cases = [
        ("Walnuss dunkel", "braun"),
        ("Eiche Sonoma", "braun"),
        ("Wenge", "braun"),
        ("Buche natur", "braun"),
        ("Anthrazit matt", "grau"),
        ("Hellgrau", "grau"),
        ("Smaragdgrün", "grün"),
        ("Dunkelgrün", "grün"),
        ("Bordeauxrot", "rot"),
        ("Kirschrot", "rot"),
        ("Senfgelb", "gelb"),
        ("Creme", "beige"),
        ("Sandbeige", "beige"),
        ("Gold glänzend", "goldfarben"),
        ("Messing", "goldfarben"),
        ("Silber metallic", "silberfarben"),
        ("Chrom", "silberfarben"),
        ("Schwarz", "schwarz"),
        ("Tiefschwarz hochglanz", "schwarz"),
        ("Weiß", "weiß"),
        ("Polarweiß", "weiß"),
        ("Petrolblau", "blau"),
        ("Marineblau", "blau"),
        ("Altrosa", "rosa"),
        ("Lila", "lila"),
        ("Orange", "orange"),
        ("Glas transparent", "transparent"),
        ("Bunt gestreift", "bunt"),
    ]
    
    for input_color, expected_gf in color_cases:
        actual_gf = map_to_otto_grundfarbe(input_color)
        assert actual_gf == expected_gf, f"Failed for '{input_color}': expected '{expected_gf}', got '{actual_gf}'"
        assert actual_gf in OTTO_GRUNDFARBEN, f"'{actual_gf}' is not one of Otto's 17 canonical Grundfarben!"
    print(f"  [OK] All {len(color_cases)} color spectrum cases correctly mapped to Otto canonical Grundfarbe!")

    print("\n=== TEST 2: Wardrobe with Drawers and Doors ===")
    mock_schema = [
        {"name": "Farbe", "options": None},
        {"name": "Grundfarbe", "options": OTTO_GRUNDFARBEN},
        {"name": "Farbe Korpus", "options": ["Walnuss", "Weiß", "Schwarz"]},
        {"name": "Farbe Schubladen", "options": ["Walnuss", "Weiß", "Schwarz"]},
        {"name": "Farbe Türen", "options": ["Walnuss", "Weiß", "Schwarz"]},
        {"name": "Anzahl Schubladen", "options": None},
        {"name": "Anzahl Türen", "options": None},
        {"name": "Anzahl Füße", "options": None},
        {"name": "Farbe Füße", "options": ["Schwarz", "Gold", "Silber", "Chrom", "Walnuss"]},
        {"name": "Anzahl Griffe", "options": None},
        {"name": "Farbe Griffe", "options": ["Gold", "Schwarz", "Silber", "Chrom"]},
        {"name": "Design", "options": ["Modern", "Klassisch", "Landhaus"]},
        {"name": "Serie", "options": None},
        {"name": "Set-Info", "options": None}
    ]
    
    input_extracted_1 = {
        "reasoning": "Colors: 70% Walnuss dunkel, 20% Schwarz, 10% Gold. Has 2 drawers, 2 doors, 4 gold handles, 4 black legs. Classic design.",
        "has_drawers": True,
        "has_doors": True,
        "has_legs": True,
        "has_handles": True,
        "Farbe": "Walnuss",
        "Grundfarbe": None,
        "Farbe Korpus": None,
        "Farbe Schubladen": None,
        "Farbe Türen": None,
        "Anzahl Schubladen": "2",
        "Anzahl Türen": "2",
        "Anzahl Füße": "4",
        "Farbe Füße": "Schwarz",
        "Anzahl Griffe": "4",
        "Farbe Griffe": "Gold",
        "Design": "Barock Klassisch",
        "Serie": "Royal Elegance",
        "Set-Info": "1-tlg."
    }
    
    res1 = enforce_furniture_color_rules(input_extracted_1, mock_schema)
    assert res1["Farbe"] == "Walnuss"
    assert res1["Grundfarbe"] == "braun"
    assert res1["Farbe Korpus"] == "Walnuss"
    assert res1["Farbe Schubladen"] == "Walnuss"
    assert res1["Farbe Türen"] == "Walnuss"
    assert res1["Anzahl Füße"] == "4"
    assert res1["Farbe Füße"] == "Schwarz"
    assert res1["Anzahl Griffe"] == "4"
    assert res1["Farbe Griffe"] == "Gold"
    assert res1["Design"] == "Klassisch"
    assert res1["Serie"] == "Royal Elegance"
    assert res1["Set-Info"] == "1-tlg."
    for k in ["reasoning", "has_drawers", "has_doors", "has_legs", "has_handles"]:
        assert k not in res1, f"Meta key '{k}' must be stripped from result"
    print("  [OK] Wardrobe cascade and new attributes test passed successfully!")

    print("\n=== TEST 3: Chair Set Multiplier (5 chairs * 4 legs = 20 legs) ===")
    input_extracted_set = {
        "reasoning": "5x Esszimmerstühle, each chair has 4 black metal legs (total 20). No handles, no drawers, no doors. Modern style.",
        "has_drawers": False,
        "has_doors": False,
        "has_legs": True,
        "has_handles": False,
        "Farbe": "Anthrazit",
        "Grundfarbe": None,
        "Farbe Korpus": None,
        "Anzahl Füße": "20",
        "Farbe Füße": "Schwarz",
        "Anzahl Griffe": "0",
        "Farbe Griffe": None,
        "Design": "Modern minimalist",
        "Serie": "Milano",
        "Set-Info": "5er-Set"
    }
    res_set = enforce_furniture_color_rules(input_extracted_set, mock_schema)
    assert res_set["Farbe"] == "Anthrazit"
    assert res_set["Grundfarbe"] == "grau"
    assert res_set["Anzahl Füße"] == "20"
    assert res_set["Farbe Füße"] == "Schwarz"
    assert res_set["Farbe Griffe"] is None
    assert res_set["Farbe Schubladen"] is None
    assert res_set["Farbe Türen"] is None
    assert res_set["Design"] == "Modern"
    assert res_set["Serie"] == "Milano"
    assert res_set["Set-Info"] == "5er-Set"
    print("  [OK] Chair set multiplier and legs test passed successfully!")

    print("\n=== TEST 4: Handleless / Wall-mounted TV Board (0 legs, 0 handles) ===")
    input_extracted_board = {
        "reasoning": "Wall-mounted floating TV lowboard. Push-to-open, without handles, without feet. Modern clean design.",
        "has_drawers": True,
        "has_doors": False,
        "has_legs": False,
        "has_handles": False,
        "Farbe": "Weiß",
        "Anzahl Füße": "0",
        "Farbe Füße": None,
        "Anzahl Griffe": "0",
        "Farbe Griffe": None,
        "Design": "Modern",
        "Serie": "Nordic Line"
    }
    res_board = enforce_furniture_color_rules(input_extracted_board, mock_schema)
    assert res_board["Farbe"] == "Weiß"
    assert res_board["Grundfarbe"] == "weiß"
    assert res_board["Farbe Korpus"] == "Weiß"
    assert res_board["Farbe Schubladen"] == "Weiß"
    assert res_board["Farbe Türen"] is None
    assert res_board["Farbe Füße"] is None
    assert res_board["Farbe Griffe"] is None
    assert res_board["Design"] == "Modern"
    print("  [OK] Handleless and legless furniture test passed successfully!")

    print("\n=== TEST 5: Bezug (Upholstery) Cascading ===")
    mock_sofa_schema = [
        {"name": "Farbe", "options": None},
        {"name": "Bezug", "options": ["Stoff", "Samt", "Kunstleder", "Echtleder"]},
        {"name": "Bezug Korpus", "options": ["Stoff", "Samt", "Kunstleder", "Echtleder"]},
        {"name": "Bezug Sitzfläche", "options": ["Stoff", "Samt", "Kunstleder", "Echtleder"]},
        {"name": "Bezug Armlehnen", "options": ["Stoff", "Samt", "Kunstleder", "Echtleder"]},
        {"name": "Bezug Rückenlehne", "options": ["Stoff", "Samt", "Kunstleder", "Echtleder"]},
    ]
    input_bezug_1 = {
        "Farbe": "Blau",
        "Bezug": "Samt",
        "Bezug Korpus": None,
        "Bezug Sitzfläche": None,
        "Bezug Armlehnen": None,
        "Bezug Rückenlehne": None,
    }
    res_b1 = enforce_furniture_color_rules(input_bezug_1, mock_sofa_schema)
    assert res_b1["Bezug"] == "Samt"
    assert res_b1["Bezug Korpus"] == "Samt"
    assert res_b1["Bezug Sitzfläche"] == "Samt"
    assert res_b1["Bezug Armlehnen"] == "Samt"
    assert res_b1["Bezug Rückenlehne"] == "Samt"

    # Fallback inheritance: Bezug is None, but Bezug Korpus is filled by AI
    input_bezug_fallback = {
        "Farbe": "Schwarz",
        "Bezug": None,
        "Bezug Korpus": "Kunstleder",
        "Bezug Sitzfläche": None,
        "Bezug Armlehnen": None,
        "Bezug Rückenlehne": None,
    }
    res_b2 = enforce_furniture_color_rules(input_bezug_fallback, mock_sofa_schema)
    assert res_b2["Bezug"] == "Kunstleder"
    assert res_b2["Bezug Korpus"] == "Kunstleder"
    assert res_b2["Bezug Sitzfläche"] == "Kunstleder"
    assert res_b2["Bezug Armlehnen"] == "Kunstleder"
    assert res_b2["Bezug Rückenlehne"] == "Kunstleder"
    print("  [OK] Bezug cascading to Bezug Korpus, Sitzfläche, Armlehnen passed successfully!")

    print("\n=== TEST 6: Bed Color Cascade & Independent 50/50 Farbe Matratze ===")
    mock_bed_schema = [
        {"name": "Farbe", "options": None},
        {"name": "Grundfarbe", "options": OTTO_GRUNDFARBEN},
        {"name": "Farbe Bettgestell", "options": None},
        {"name": "Farbe Fußteil", "options": None},
        {"name": "Farbe Kopfteil", "options": None},
        {"name": "Farbe Matratze", "options": ["Weiß", "Anthrazit", "Grau", "Schwarz"]},
    ]
    # Case A: Same color (Boxspring bed with matching mattress)
    input_bed_same = {
        "Farbe": "Weiß",
        "Farbe Bettgestell": None,
        "Farbe Fußteil": None,
        "Farbe Kopfteil": None,
        "Farbe Matratze": "Weiß",
    }
    res_bed_same = enforce_furniture_color_rules(input_bed_same, mock_bed_schema)
    assert res_bed_same["Farbe Bettgestell"] == "Weiß"
    assert res_bed_same["Farbe Fußteil"] == "Weiß"
    assert res_bed_same["Farbe Kopfteil"] == "Weiß"
    assert res_bed_same["Farbe Matratze"] == "Weiß"

    # Case B: Contrasting color (Anthracite bed with white mattress)
    input_bed_contrast = {
        "Farbe": "Anthrazit",
        "Farbe Bettgestell": None,
        "Farbe Fußteil": None,
        "Farbe Kopfteil": None,
        "Farbe Matratze": "Weiß",
    }
    res_bed_contrast = enforce_furniture_color_rules(input_bed_contrast, mock_bed_schema)
    assert res_bed_contrast["Farbe Bettgestell"] == "Anthrazit"
    assert res_bed_contrast["Farbe Fußteil"] == "Anthrazit"
    assert res_bed_contrast["Farbe Kopfteil"] == "Anthrazit"
    assert res_bed_contrast["Farbe Matratze"] == "Weiß", "Farbe Matratze must NOT be overwritten by Farbe Anthrazit"

    # Case C: No mattress included (frame only, Farbe Matratze is None)
    input_bed_no_mat = {
        "Farbe": "Weiß",
        "Farbe Bettgestell": None,
        "Farbe Fußteil": None,
        "Farbe Kopfteil": None,
        "Farbe Matratze": None,
    }
    res_bed_no_mat = enforce_furniture_color_rules(input_bed_no_mat, mock_bed_schema)
    assert res_bed_no_mat["Farbe Bettgestell"] == "Weiß"
    assert res_bed_no_mat["Farbe Fußteil"] == "Weiß"
    assert res_bed_no_mat["Farbe Kopfteil"] == "Weiß"
    assert res_bed_no_mat.get("Farbe Matratze") is None, "Farbe Matratze must remain None when not included"
    print("  [OK] Bed color cascade and 50/50 independent Farbe Matratze passed successfully!")

    print("\n=== TEST 7: Holzart Selection & Normalization (Buche / Kiefer) ===")
    mock_wood_schema = [
        {"name": "Material", "options": None},
        {"name": "Holzart", "options": ["Buche", "Kiefer"]},
        {"name": "Holzart Bettgestell", "options": ["Buche", "Kiefer"]},
    ]
    # Input with lowercase 'buche'
    input_wood_1 = {
        "Holzart": "buche",
        "Holzart Bettgestell": "kiefer",
    }
    res_w1 = enforce_furniture_color_rules(input_wood_1, mock_wood_schema)
    assert res_w1["Holzart"] == "Buche"
    assert res_w1["Holzart Bettgestell"] == "Kiefer"

    # Input without Holzart, but mentioned in Material
    input_wood_mat = {
        "Material": "Massivholz Buche lackiert",
        "Holzart": None,
    }
    res_w2 = enforce_furniture_color_rules(input_wood_mat, mock_wood_schema)
    assert res_w2["Holzart"] == "Buche"

    input_wood_mat2 = {
        "Material": "Kiefer massiv natur",
        "Holzart": None,
    }
    # Input where text only says "Holz", no specific wood species -> defaults to Buche
    input_wood_default = {
        "Material": "Holz / Textil",
        "Holzart": None,
    }
    res_w4 = enforce_furniture_color_rules(input_wood_default, mock_wood_schema)
    assert res_w4["Holzart"] == "Buche", f"Expected 'Buche', got '{res_w4.get('Holzart')}'"
    print("  [OK] Holzart selection and Buche/Kiefer normalization passed successfully!")

    print("\n=== TEST 8: Numeric Attributes & Dimensions (Strictly No 'cm' Units) ===")
    mock_dim_schema = [
        {"name": "Breite", "type": "INTEGER", "unit": "cm", "options": None},
        {"name": "Höhe", "type": "INTEGER", "unit": "cm", "options": None},
        {"name": "Tiefe", "type": "INTEGER", "unit": "cm", "options": None},
        {"name": "Sitzhöhe", "type": "FLOAT", "unit": "cm", "options": None},
        {"name": "Liegefläche Breite", "type": "INTEGER", "unit": "cm", "options": None},
        {"name": "Belastbarkeit maximal", "type": "INTEGER", "unit": "kg", "options": None},
    ]
    input_dims = {
        "Breite": "180 cm",
        "Höhe": "85cm",
        "Tiefe": "ca. 90 cm",
        "Sitzhöhe": "45,5 cm",
        "Liegefläche Breite": "200 cm",
        "Belastbarkeit maximal": "140 kg",
    }
    res_dims = enforce_furniture_color_rules(input_dims, mock_dim_schema)
    assert res_dims["Breite"] == "180", f"Expected '180', got '{res_dims['Breite']}'"
    assert res_dims["Höhe"] == "85", f"Expected '85', got '{res_dims['Höhe']}'"
    assert res_dims["Tiefe"] == "90", f"Expected '90', got '{res_dims['Tiefe']}'"
    assert res_dims["Sitzhöhe"] == "45.5", f"Expected '45.5', got '{res_dims['Sitzhöhe']}'"
    assert res_dims["Liegefläche Breite"] == "200", f"Expected '200', got '{res_dims['Liegefläche Breite']}'"
    assert res_dims["Belastbarkeit maximal"] == "140", f"Expected '140', got '{res_dims['Belastbarkeit maximal']}'"
    print("  [OK] All numeric attributes successfully cleaned of 'cm' / 'kg' units!")

    print("\n=== TEST 9: Wohnraum Deduction ===")
    mock_wohnraum_schema = [
        {"name": "Wohnraum", "options": ["Schlafzimmer", "Wohnzimmer", "Esszimmer", "Arbeitszimmer", "Flur/Diele", "Badezimmer"]}
    ]
    # Bedroom case
    res_wr1 = enforce_furniture_color_rules({}, mock_wohnraum_schema, product_info="Title: Polsterbett mit Matratze\nDescription: Elegantes Bett für Schlafzimmer")
    assert res_wr1["Wohnraum"] == "Schlafzimmer", f"Expected 'Schlafzimmer', got '{res_wr1.get('Wohnraum')}'"

    # Living room case
    res_wr2 = enforce_furniture_color_rules({}, mock_wohnraum_schema, product_info="Title: Ecksofa L-Form Couchgarnitur")
    assert res_wr2["Wohnraum"] == "Wohnzimmer", f"Expected 'Wohnzimmer', got '{res_wr2.get('Wohnraum')}'"

    # Dining room case
    res_wr3 = enforce_furniture_color_rules({}, mock_wohnraum_schema, product_info="Title: Esstisch mit 6 Stühlen Essgruppe")
    assert res_wr3["Wohnraum"] == "Esszimmer", f"Expected 'Esszimmer', got '{res_wr3.get('Wohnraum')}'"

    # Office case
    res_wr4 = enforce_furniture_color_rules({}, mock_wohnraum_schema, product_info="Title: Schreibtisch mit Schubladen Bürotisch")
    assert res_wr4["Wohnraum"] == "Arbeitszimmer", f"Expected 'Arbeitszimmer', got '{res_wr4.get('Wohnraum')}'"
    print("  [OK] Wohnraum deduction passed successfully for all rooms!")

    print("\n=== TEST 10: Liegefläche Dimensions Parsing (180 x 200 cm) ===")
    mock_liege_schema = [
        {"name": "Breite Liegefläche", "options": None},
        {"name": "Länge Liegefläche", "options": None},
        {"name": "Liegefläche", "options": ["140x200", "160x200", "180x200", "200x200"]}
    ]
    res_lf = enforce_furniture_color_rules(
        {},
        mock_liege_schema,
        product_info="Title: Designer Bettrahmen\nDescription: Liegefläche: 180 x 200 cm, Maße ca. 180x200"
    )
    assert res_lf["Breite Liegefläche"] == "180", f"Expected '180', got '{res_lf.get('Breite Liegefläche')}'"
    assert res_lf["Länge Liegefläche"] == "200", f"Expected '200', got '{res_lf.get('Länge Liegefläche')}'"
    assert res_lf["Liegefläche"] == "180x200", f"Expected '180x200', got '{res_lf.get('Liegefläche')}'"
    print("  [OK] Liegefläche dimensions parsing passed successfully!")

    print("\n=== TEST 11: Anzahl Teile & Set Parsing ===")
    mock_teile_schema = [
        {"name": "Anzahl Teile", "options": None},
    ]
    # Case A: Bedroom set (Bett + 2 Nachttische)
    res_t1 = enforce_furniture_color_rules(
        {},
        mock_teile_schema,
        product_info="Title: Schlafzimmer-Set Bett mit 2x Nachttisch",
        cat_name="Schlafzimmer-Set"
    )
    assert res_t1["Anzahl Teile"] == "3", f"Expected '3', got '{res_t1.get('Anzahl Teile')}'"

    # Case B: Dining group (1 Tisch + 6 Stühle)
    res_t2 = enforce_furniture_color_rules(
        {},
        mock_teile_schema,
        product_info="Title: Essgruppe 1 Tisch und 6 Stühle",
        cat_name="Essgruppe"
    )
    assert res_t2["Anzahl Teile"] == "7", f"Expected '7', got '{res_t2.get('Anzahl Teile')}'"

    # Case C: 4er Set Stühle
    res_t3 = enforce_furniture_color_rules(
        {},
        mock_teile_schema,
        product_info="Title: 4er Set Esszimmerstühle Polsterstuhl",
        cat_name="Stuhl-Set"
    )
    assert res_t3["Anzahl Teile"] == "4", f"Expected '4', got '{res_t3.get('Anzahl Teile')}'"

    # Case D: Single piece (1 Bett)
    res_t4 = enforce_furniture_color_rules(
        {},
        mock_teile_schema,
        product_info="Title: Polsterbett Doppelbett 180x200",
        cat_name="Bett"
    )
    assert res_t4["Anzahl Teile"] == "1", f"Expected '1', got '{res_t4.get('Anzahl Teile')}'"
    print("  [OK] Anzahl Teile set parsing passed successfully!")

    print("\n=== TEST 12: Material Synthesis (Korpus + Bezug) ===")
    mock_mat_schema = [
        {"name": "Material", "options": ["Holz", "Textil", "Holz / Textil", "Metall", "Leder"]},
        {"name": "Material Korpus", "options": ["Holz", "Metall"]},
        {"name": "Bezug", "options": ["Textil", "Samt", "Leder"]},
    ]
    res_mat = enforce_furniture_color_rules(
        {"Material Korpus": "Holz", "Bezug": "Textil", "Material": None},
        mock_mat_schema
    )
    assert res_mat["Material"] == "Holz / Textil", f"Expected 'Holz / Textil', got '{res_mat.get('Material')}'"
    print("  [OK] Material synthesis passed successfully!")

    print("\n=== TEST 13: Standard Attributes (Zielgruppe, Optik, Lieferzustand) ===")
    mock_std_schema = [
        {"name": "Zielgruppe", "options": ["Erwachsene ; Jugendliche", "Kinder"]},
        {"name": "Optik", "options": ["uni", "Holzoptik"]},
        {"name": "Lieferzustand", "options": ["teilmontiert", "zerlegt"]},
        {"name": "Pflegehinweise", "options": None},
    ]
    res_std = enforce_furniture_color_rules({}, mock_std_schema)
    assert res_std["Zielgruppe"] == "Erwachsene ; Jugendliche"
    assert res_std["Optik"] == "uni"
    assert res_std["Lieferzustand"] == "teilmontiert"
    assert res_std["Pflegehinweise"] == "keine aggressiven Reinigungsmittel verwenden"
    print("  [OK] Standard attributes filling passed successfully!")

    print("\n=== ALL TESTS PASSED SUCCESSFULLY! ===")

if __name__ == "__main__":
    run_tests()
