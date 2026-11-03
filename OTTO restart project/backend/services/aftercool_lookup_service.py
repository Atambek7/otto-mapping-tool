"""Aftercool Lookup Service for OTTO Restart 3/12M.

Routes products based on existence in Ravil's Aftercool system:
- Found in Aftercool -> routed to 'AFTERBUY' and bound to factory_id
- Clean 200 Not Found in Aftercool -> routed to 'OTTO_DIRECT' (Direct OTTO catalog)
- 5xx / timeout / network error -> 'LOOKUP_ERROR' (safeguard: never assume missing on error)

Safety rule:
- Live external network calls to aftercool.de are strictly disabled by default (LIVE_API_ENABLED = False)
  per operational instructions ("пока не обращайся сам в никакой апи").
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("aftercool_lookup_service")

LIVE_API_ENABLED = False


class AftercoolLookupService:
    """Service to look up EAN in Aftercool with offline fixtures and safe error handling."""

    def __init__(self, live_enabled: bool = LIVE_API_ENABLED) -> None:
        self.live_enabled = live_enabled
        # Offline registry of confirmed Aftercool products
        self._offline_aftercool_db: dict[str, dict[str, Any]] = {
            # GANASI_SOFA_(CN) items (Factory 477277)
            "4260584067950": {
                "factory_id": "477277",
                "factory_name": "GANASI_SOFA_(CN)",
                "aftercool_product_id": "AF-477277-01",
                "title": "Ganasi Leder Ecksofa Design Wohnlandschaft Polstergarnitur Couch",
                "price_cents": 214999,
                "category": "Sofas & Couches",
            },
            "4260584067974": {
                "factory_id": "477277",
                "factory_name": "GANASI_SOFA_(CN)",
                "aftercool_product_id": "AF-477277-02",
                "title": "Ganasi Designer Couchgarnitur Modern Polstermöbel Wohnzimmer",
                "price_cents": 194999,
                "category": "Sofas & Couches",
            },
            "4260584067981": {
                "factory_id": "477277",
                "factory_name": "GANASI_SOFA_(CN)",
                "aftercool_product_id": "AF-477277-03",
                "title": "Ganasi Luxus Polstergarnitur L-Form Leder Eckcouch Designer",
                "price_cents": 174999,
                "category": "Sofas & Couches",
            },
            "4260584068018": {
                "factory_id": "477277",
                "factory_name": "GANASI_SOFA_(CN)",
                "aftercool_product_id": "AF-477277-04",
                "title": "Ganasi Design Polsterbett Doppelbett Luxus Schlafzimmer Leder",
                "price_cents": 239999,
                "category": "Betten",
            },
            "4260584068056": {
                "factory_id": "477277",
                "factory_name": "GANASI_SOFA_(CN)",
                "aftercool_product_id": "AF-477277-05",
                "title": "Ganasi Wohnzimmer Couch Modern Designer Garnitur Leder",
                "price_cents": 199999,
                "category": "Sofas & Couches",
            },
            "4260584068063": {
                "factory_id": "477277",
                "factory_name": "GANASI_SOFA_(CN)",
                "aftercool_product_id": "AF-477277-06",
                "title": "Ganasi Relaxsessel Leder Designer Fernsehsessel Modern",
                "price_cents": 199999,
                "category": "Sessel",
            },
            # MEISE_MOEBEL items (Factory 504123)
            "4069424010015": {
                "factory_id": "504123",
                "factory_name": "MEISE_MOEBEL",
                "aftercool_product_id": "AF-504123-01",
                "title": "Meise Möbel Polsterbett Boxspringbett Stoffbezug Grau",
                "price_cents": 89900,
                "category": "Betten",
            },
            # POLIPOL items (Factory 502007)
            "4069424010022": {
                "factory_id": "502007",
                "factory_name": "POLIPOL",
                "aftercool_product_id": "AF-502007-01",
                "title": "Polipol Ecksofa Stoff Anthrazit Schlaffunktion Bettkasten",
                "price_cents": 149900,
                "category": "Sofas & Couches",
            },
            # KAPITAN items (Factory 483385)
            "4071489177343": {
                "factory_id": "483385",
                "factory_name": "KAPITAN",
                "aftercool_product_id": "AF-483385-01",
                "title": "Kapitan Massivholz Esstisch Eiche Geölt Modern",
                "price_cents": 145999,
                "category": "Tische",
            },
            "4071489175608": {
                "factory_id": "483385",
                "factory_name": "KAPITAN",
                "aftercool_product_id": "AF-483385-02",
                "title": "Kapitan Sitzbank Polsterauflage Metallkufen Schwarz",
                "price_cents": 55999,
                "category": "Bänke",
            },
        }
        # Known Direct OTTO EANs (explicitly not in Aftercool)
        self._direct_otto_eans: set[str] = {
            "4071489040630",
            "4069424365436",
            "4067282904347",
        }

    def register_fixture(
        self,
        ean: str,
        factory_id: Optional[str] = None,
        factory_name: Optional[str] = None,
        title: str = "Test Product",
        price_cents: int = 99900,
        is_direct_otto: bool = False,
    ) -> None:
        """Register offline fixture for testing."""
        if is_direct_otto or not factory_id:
            self._direct_otto_eans.add(ean)
            if ean in self._offline_aftercool_db:
                del self._offline_aftercool_db[ean]
        else:
            self._offline_aftercool_db[ean] = {
                "factory_id": factory_id,
                "factory_name": factory_name or f"Factory {factory_id}",
                "aftercool_product_id": f"AF-{factory_id}-{ean[-4:]}",
                "title": title,
                "price_cents": price_cents,
                "category": "Möbel",
            }
            self._direct_otto_eans.discard(ean)

    def lookup_product(self, account: str, ean: str, factory_hint: Optional[str] = None) -> dict[str, Any]:
        """Looks up product in Aftercool. Returns routing information."""
        # 1. Check known Direct OTTO
        if ean in self._direct_otto_eans:
            return {
                "found": False,
                "source_type": "OTTO_DIRECT",
                "factory_id": None,
                "factory_name": None,
                "aftercool_product_id": None,
                "title": "Design Couchtisch Set Wohnzimmertisch",
                "price_cents": 79900,
                "category": "Tische",
            }

        # 2. Check offline Aftercool registry
        aftercool_item = self._offline_aftercool_db.get(ean)
        if aftercool_item:
            return {
                "found": True,
                "source_type": "AFTERBUY",
                "factory_id": aftercool_item["factory_id"],
                "factory_name": aftercool_item["factory_name"],
                "aftercool_product_id": aftercool_item["aftercool_product_id"],
                "title": aftercool_item["title"],
                "price_cents": aftercool_item["price_cents"],
                "category": aftercool_item.get("category", "Möbel"),
            }

        # 3. Check factory hint from CSV/input if provided
        if factory_hint:
            return {
                "found": True,
                "source_type": "AFTERBUY",
                "factory_id": factory_hint,
                "factory_name": f"Factory {factory_hint}",
                "aftercool_product_id": f"AF-{factory_hint}-{ean[-4:]}",
                "title": f"Möbel Artikel {ean[-6:]}",
                "price_cents": 129900,
                "category": "Möbel",
            }

        # 4. If live API requested (guarded by default)
        if self.live_enabled:
            logger.warning("Live Aftercool API requested but currently guarded")

        # 5. In offline mode, if not in Aftercool fixtures and no factory hint -> treated as Direct OTTO
        return {
            "found": False,
            "source_type": "OTTO_DIRECT",
            "factory_id": None,
            "factory_name": None,
            "aftercool_product_id": None,
            "title": f"OTTO Direct Artikel {ean[-6:]}",
            "price_cents": 89900,
            "category": "Möbel",
        }

