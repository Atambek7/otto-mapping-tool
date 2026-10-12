import asyncio
from services.automapper import (
    get_top_categories,
    heuristic_category_prediction,
    predict_category,
    build_category_index,
    WORD_TO_CAT
)
from services.categories_repo import get_category_by_id

def test_heuristic_category_prediction():
    print("=== TEST 1: Heuristic Category Prediction ===")
    test_cases = [
        # Chairs only -> 25778 Sitzgruppe
        ("4er-Set Esszimmerstühle Samt Grau mit Metallbeinen", "25778", "Sitzgruppe"),
        ("2er Set Schwingstühle Freischwinger Kunstleder Schwarz", "25778", "Sitzgruppe"),
        ("6x Esszimmerstuhl Küchenstuhl Polsterstuhl", "25778", "Sitzgruppe"),
        ("Stuhl-Set 4 Stück Stoffbezug Beige", "25778", "Sitzgruppe"),
        ("Bistrostühle 2er-Set wetterfest", "25778", "Sitzgruppe"),
        ("Armlehnstühle 4er Set Esszimmer", "25778", "Sitzgruppe"),

        # Table + Chairs -> 25784 Essgruppe
        ("Esstisch mit 4 Stühlen Essgruppe Sonoma Eiche", "25784", "Essgruppe"),
        ("Esstisch Holz massiv inklusive 6 Stühle Esszimmergarnitur", "25784", "Essgruppe"),
        ("Essgruppe 5-tlg. Küchentisch mit 4 Stühlen", "25784", "Essgruppe"),
        ("Küchentisch mit 2 Stühlen Essgruppe klein", "25784", "Essgruppe"),
        ("Esstischgruppe 7-teilig Tisch und 6 Stühle", "25784", "Essgruppe"),

        # Outdoor / Garden
        ("Balkonset 3-tlg. Klapptisch mit 2 Klappstühlen", "25776", "Balkonset"),
        ("Garten-Essgruppe 5-tlg. Gartentisch mit 4 Gartensesseln", "25782", "Garten-Essgruppe"),
        ("Gartenlounge-Set Polyrattan Ecksofa mit Tisch", "25786", "Gartenlounge-Set"),

        # Furniture Sets (Set / Garnitur)
        ("Badmöbel-Set 3-tlg. Hochschrank Waschbeckenunterschrank Spiegel", "20101", "Badmöbel-Set"),
        ("Garderoben-Set 4-tlg. Schuhschrank Spiegel Wandpaneel", "20112", "Garderoben-Set"),
        ("Schlafzimmer-Set 3-teilig Bett Nachtkommoden", "24447", "Schlafzimmer-Set"),
        ("Wohnzimmer-Set 4-tlg. Wohnwand Vitrine TV-Lowboard", "20110", "Wohnzimmer-Set"),
        ("Polstergarnitur 3-teilig Sofa 3-Sitzer 2-Sitzer Sessel", "25785", "Polstergarnitur"),
        ("Bartisch mit 2 Barhockern Bar-Set", "20105", "Bar-Set"),
    ]

    for title, expected_id, expected_name in test_cases:
        predicted_id = heuristic_category_prediction(title)
        cat = get_category_by_id(predicted_id) if predicted_id else {}
        actual_name = cat.get("name") if cat else None
        assert predicted_id == expected_id, f"FAIL for '{title}': expected {expected_id} ({expected_name}), got {predicted_id} ({actual_name})"
        print(f"  [OK] '{title[:42]}...' -> {predicted_id} ({actual_name})")

    print(f"All {len(test_cases)} heuristic category tests passed successfully!\n")

def test_top_categories_ranking():
    print("=== TEST 2: Candidate Ranking & Injection in get_top_categories ===")
    
    # 1. Chairs only: Sitzgruppe (25778) MUST be in top 5
    chairs_top = get_top_categories("4er-Set Esszimmerstühle Samt", n=10)
    top_ids = [str(c.get("categoryId")) for c in chairs_top]
    assert "25778" in top_ids[:5], f"25778 (Sitzgruppe) not in top 5 candidates for chair set! Top 5: {top_ids[:5]}"
    print(f"  [OK] '4er-Set Esszimmerstühle Samt' includes 25778 (Sitzgruppe) at rank #{top_ids.index('25778')+1}")

    # 2. Table + Chairs: Essgruppe (25784) MUST be in top 5
    dining_top = get_top_categories("Esstisch mit 4 Stühlen", n=10)
    top_ids_dining = [str(c.get("categoryId")) for c in dining_top]
    assert "25784" in top_ids_dining[:5], f"25784 (Essgruppe) not in top 5 candidates for dining set! Top 5: {top_ids_dining[:5]}"
    print(f"  [OK] 'Esstisch mit 4 Stühlen' includes 25784 (Essgruppe) at rank #{top_ids_dining.index('25784')+1}")

    # 3. Badmöbel-Set: 20101 MUST be in top 5
    bath_top = get_top_categories("Badmöbel-Set 3-tlg.", n=10)
    top_ids_bath = [str(c.get("categoryId")) for c in bath_top]
    assert "20101" in top_ids_bath[:5], f"20101 (Badmöbel-Set) not in top 5 for bathroom set! Top 5: {top_ids_bath[:5]}"
    print(f"  [OK] 'Badmöbel-Set 3-tlg.' includes 20101 (Badmöbel-Set) at rank #{top_ids_bath.index('20101')+1}")

    # 4. Garderoben-Set: 20112 MUST be in top 5
    hall_top = get_top_categories("Garderoben-Set 4-tlg.", n=10)
    top_ids_hall = [str(c.get("categoryId")) for c in hall_top]
    assert "20112" in top_ids_hall[:5], f"20112 (Garderoben-Set) not in top 5 for hallway set! Top 5: {top_ids_hall[:5]}"
    print(f"  [OK] 'Garderoben-Set 4-tlg.' includes 20112 (Garderoben-Set) at rank #{top_ids_hall.index('20112')+1}")

    # 5. Polstergarnitur: 25785 MUST be in top 5
    sofa_top = get_top_categories("Polstergarnitur Sofa Couch Set", n=10)
    top_ids_sofa = [str(c.get("categoryId")) for c in sofa_top]
    assert "25785" in top_ids_sofa[:5], f"25785 (Polstergarnitur) not in top 5 for sofa set! Top 5: {top_ids_sofa[:5]}"
    print(f"  [OK] 'Polstergarnitur Sofa Couch Set' includes 25785 (Polstergarnitur) at rank #{top_ids_sofa.index('25785')+1}")

    print("All candidate ranking tests passed successfully!\n")

async def test_predict_category_fast_path():
    print("=== TEST 3: predict_category Fast Path & Resolution ===")
    
    # predict_category should immediately return without needing an API key if heuristic matches
    res1 = await predict_category("Title: 4er-Set Esszimmerstühle Samt", "4er-Set Esszimmerstühle Samt", custom_api_key=None)
    assert res1 == "25778", f"Expected 25778, got {res1}"
    print("  [OK] predict_category chairs set -> 25778 (Sitzgruppe)")

    res2 = await predict_category("Title: Esstisch mit 4 Stühlen", "Esstisch mit 4 Stühlen", custom_api_key=None)
    assert res2 == "25784", f"Expected 25784, got {res2}"
    print("  [OK] predict_category table + chairs -> 25784 (Essgruppe)")

    res3 = await predict_category("Title: Badmöbel-Set 3-tlg.", "Badmöbel-Set 3-tlg.", custom_api_key=None)
    assert res3 == "20101", f"Expected 20101, got {res3}"
    print("  [OK] predict_category bathroom set -> 20101 (Badmöbel-Set)")

    print("All predict_category fast-path tests passed successfully!\n")

def run_all():
    test_heuristic_category_prediction()
    test_top_categories_ranking()
    asyncio.run(test_predict_category_fast_path())
    print("🎉 ALL CATEGORY RULES TESTS PASSED WITH 100% SUCCESS!")

if __name__ == "__main__":
    run_all()
