import os
import sys
import sqlite3
import asyncio
from typing import List, Dict, Any

# Ensure backend root is on python path
BACKEND_ROOT = "/app"
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from database import SessionLocal
import models

from services.roma_db import build_roma_sqlite_db
from services.otto_client import OttoMarketClient
from services.otto_formatter import format_otto_product
from services.brand_guard import sanitize_brand_attributes


# ============================================================================
# 1. OTTO AUTH CHECK
# ============================================================================

def check_otto_auth() -> Dict[str, bool]:
    print("\n=======================================================")
    print(" 1. ПРОВЕРКА АВТОРИЗАЦИИ OTTO API")
    print("=======================================================")

    results: Dict[str, bool] = {}
    db = SessionLocal()

    try:
        for acc in ("JV", "XL"):
            try:
                # Read credentials from DB
                settings = (
                    db.query(models.AccountSettings)
                    .filter(models.AccountSettings.account_name == acc)
                    .first()
                )

                if not settings:
                    print(f"  ✗ [{acc}] AccountSettings не найдены")
                    results[acc] = False
                    continue

                client_id = getattr(settings, "otto_client_id", None)
                client_secret = getattr(settings, "otto_client_secret", None)

                if not client_id or not client_secret:
                    print(f"  ✗ [{acc}] OTTO client_id/client_secret не настроены")
                    results[acc] = False
                    continue

                # Current OttoMarketClient signature:
                # OttoMarketClient(client_id, client_secret, timeout=30.0)
                client = OttoMarketClient(
                    client_id=client_id,
                    client_secret=client_secret,
                )

                token = client.get_access_token()

                if token and len(token) > 20:
                    print(f"  ✓ [{acc}] OAuth token успешно получен")
                    results[acc] = True
                else:
                    print(f"  ✗ [{acc}] OTTO вернул пустой/некорректный token")
                    results[acc] = False

            except Exception as e:
                print(f"  ✗ [{acc}] Ошибка авторизации: {e}")
                results[acc] = False

        return results

    finally:
        db.close()


# ============================================================================
# 2. SQLITE EXPORT CHECK
# ============================================================================

async def check_sqlite_export(
    factory_id: str = "504201",
    account: str = "JV",
) -> bool:
    print("\n=======================================================")
    print(f" 2. ПРОВЕРКА ВЫГРУЗКИ В SQLITE DB")
    print(f"    Фабрика: {factory_id} | Аккаунт: {account}")
    print("=======================================================")

    db = SessionLocal()
    db_path = None

    try:
        mappings = (
            db.query(models.ProductMapping)
            .filter(
                models.ProductMapping.factory_id == str(factory_id),
                models.ProductMapping.account_name == account,
            )
            .all()
        )

        print(f"  Найдено товаров: {len(mappings)}")

        if not mappings:
            print(f"  ✗ Нет товаров для фабрики {factory_id}")
            return False

        # Test sample of 15 items
        sample_mappings = mappings[:15]

        items: List[Dict[str, Any]] = []

        for m in sample_mappings:
            items.append(
                {
                    "ean": m.product_ean,
                    "product": m.product_name,
                    "title": m.product_name,
                    "price_otto": m.price,
                    "price_af": m.price_af,
                    "price": m.price or m.price_af,
                    "collection": str(factory_id),
                    "category_id": m.category_id,
                    "pic_main": m.image_url,
                }
            )

        print(
            f"  Запуск build_roma_sqlite_db "
            f"для {len(items)} товаров..."
        )

        db_path = await build_roma_sqlite_db(
            items,
            account=account,
            factory_name=str(factory_id),
        )

        print(f"  ✓ SQLite файл создан: {db_path}")

        if not db_path or not os.path.exists(db_path):
            print("  ✗ SQLite файл отсутствует после генерации")
            return False

        # ------------------------------------------------------------------
        # Inspect SQLite
        # ------------------------------------------------------------------

        conn = sqlite3.connect(db_path)

        try:
            cur = conn.cursor()

            cur.execute("PRAGMA table_info(products)")
            cols = [col[1] for col in cur.fetchall()]

            expected_cols = [
                "pic_main",
                "ean",
                "product",
                "price",
                "category",
                "collection",
            ]

            print(f"  Колонки таблицы products: {cols}")

            if cols != expected_cols:
                print(f"  ✗ Неверные колонки. Ожидалось: {expected_cols}")
                return False

            cur.execute(
                """
                SELECT
                    ean,
                    product,
                    price,
                    category,
                    collection,
                    length(pic_main)
                FROM products
                """
            )

            rows = cur.fetchall()

        finally:
            conn.close()

        print(f"  Извлечено строк из SQLite: {len(rows)}")

        if len(rows) != len(items):
            print(
                f"  ✗ Количество строк не совпадает: "
                f"{len(rows)} != {len(items)}"
            )
            return False

        # ------------------------------------------------------------------
        # Validate data
        # ------------------------------------------------------------------

        zero_prices = []
        no_titles = []

        for row in rows:
            ean, product, price, category, collection, img_len = row

            if price is None:
                zero_prices.append((ean, price))
            else:
                try:
                    if float(price) <= 0:
                        zero_prices.append((ean, price))
                except (TypeError, ValueError):
                    zero_prices.append((ean, price))

            if not product or str(product).strip() in (
                "",
                "Без названия",
                "None",
            ):
                no_titles.append(ean)

        if zero_prices:
            print(f"  ✗ Нулевые/некорректные цены: {zero_prices}")
            return False

        if no_titles:
            print(f"  ✗ Пустые названия: {no_titles}")
            return False

        print("  ✓ Все цены положительные")
        print("  ✓ Все названия заполнены")

        for row in rows[:3]:
            ean, product, price, category, collection, img_len = row
            print(
                f"    - EAN {ean}: "
                f"{str(product)[:35]}... | "
                f"Цена: {price} € | "
                f"Категория: {category} | "
                f"Фото: {img_len or 0} байт"
            )

        print("  ✓ SQLite export работает корректно")
        return True

    except Exception as e:
        print(f"  ✗ Ошибка SQLite export: {e}")
        return False

    finally:
        # Remove generated temporary file
        if db_path and os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception:
                pass

        db.close()


# ============================================================================
# 3. OTTO PAYLOAD CHECK
# ============================================================================

def check_otto_product_payload(
    factory_id: str = "504201",
    account: str = "JV",
) -> bool:
    print("\n=======================================================")
    print(" 3. ПРОВЕРКА ФОРМИРОВАНИЯ PAYLOAD ДЛЯ OTTO")
    print(f"    Фабрика: {factory_id} | Аккаунт: {account}")
    print("=======================================================")

    db = SessionLocal()

    try:
        from services.categories_repo import get_category_by_id

        mappings = (
            db.query(models.ProductMapping)
            .filter(
                models.ProductMapping.factory_id == str(factory_id),
                models.ProductMapping.account_name == account,
                models.ProductMapping.category_id.isnot(None),
            )
            .limit(10)
            .all()
        )

        print(f"  Проверка реальных товаров: {len(mappings)}")

        if not mappings:
            print("  ✗ Нет товаров для проверки payload")
            return False

        all_ok = True

        for idx, m in enumerate(mappings, 1):
            try:
                ean = str(m.product_ean).strip()

                # ----------------------------------------------------------
                # Product attributes
                # ----------------------------------------------------------

                attrs = (
                    db.query(models.ProductAttribute)
                    .filter(
                        models.ProductAttribute.product_ean == ean,
                        models.ProductAttribute.account_name == account,
                    )
                    .all()
                )

                attr_dict = {
                    a.attribute_name: a.attribute_value
                    for a in attrs
                }

                # ----------------------------------------------------------
                # Resolve price
                # ----------------------------------------------------------

                clean_price = None

                for p_val in (m.price, m.price_af):
                    if not p_val:
                        continue

                    try:
                        value = float(
                            str(p_val)
                            .replace("€", "")
                            .replace(",", ".")
                            .strip()
                        )

                        if value > 0:
                            clean_price = (
                                str(int(value))
                                if value.is_integer()
                                else f"{value:.2f}"
                            )
                            break

                    except (ValueError, TypeError):
                        continue

                if clean_price is None:
                    raise AssertionError(
                        f"EAN {ean} не имеет валидной цены"
                    )

                # ----------------------------------------------------------
                # Category
                # ----------------------------------------------------------

                cat_obj = get_category_by_id(m.category_id)

                cat_name = (
                    cat_obj.get("name", m.category_id)
                    if cat_obj
                    else m.category_id
                )

                if not cat_name:
                    raise AssertionError(
                        f"EAN {ean}: категория отсутствует"
                    )

                # ----------------------------------------------------------
                # Brand sanitization
                # ----------------------------------------------------------

                safe_attrs = sanitize_brand_attributes(
                    account,
                    attr_dict,
                )

                # ----------------------------------------------------------
                # Build formatter input
                # ----------------------------------------------------------

                aftercool_info = {
                    "name": m.product_name or f"Product {ean}",
                    "description": (
                        f"Hochwertiges Produkt "
                        f"{m.product_name or ean}. "
                        f"Perfekt für Ihr Zuhause."
                    ),
                    "price": clean_price,
                    "images": (
                        [m.image_url]
                        if m.image_url
                        else ["https://example.com/test.jpg"]
                    ),
                    "custom_item_specifics": "",
                }

                mapping_dict = {
                    "attributes": safe_attrs,
                    "category_id": m.category_id,
                    "category": cat_name,
                    "account_name": account,
                }

                # Current formatter signature:
                # format_otto_product(
                #     ean,
                #     aftercool_info,
                #     mapping,
                #     category_name,
                #     account_name="JV"
                # )
                payload = format_otto_product(
                    ean=ean,
                    aftercool_info=aftercool_info,
                    mapping=mapping_dict,
                    category_name=cat_name,
                    account_name=account,
                )

                # ==========================================================
                # PAYLOAD VALIDATION
                # ==========================================================

                # 1. Top-level identifiers
                assert (
                    payload.get("productReference") == ean
                ), (
                    f"Неверный productReference: "
                    f"{payload.get('productReference')}"
                )

                assert payload.get("sku") == ean, (
                    f"Неверный sku: {payload.get('sku')}"
                )

                assert payload.get("ean") == ean, (
                    f"Неверный ean: {payload.get('ean')}"
                )

                # 2. productDescription
                product_description = payload.get(
                    "productDescription",
                    {},
                )

                assert isinstance(
                    product_description,
                    dict,
                ), "productDescription должен быть dict"

                assert (
                    product_description.get("category") == cat_name
                ), (
                    f"Неверная category: "
                    f"{product_description.get('category')}"
                )

                brand_id = product_description.get("brandId")

                assert brand_id, (
                    f"EAN {ean}: отсутствует brandId"
                )

                assert product_description.get("productLine"), (
                    f"EAN {ean}: отсутствует productLine"
                )

                description = product_description.get(
                    "description",
                    "",
                )

                assert description, (
                    f"EAN {ean}: отсутствует description"
                )

                # 3. Pricing
                pricing = payload.get("pricing", {})

                assert isinstance(
                    pricing,
                    dict,
                ), "pricing должен быть dict"

                standard_price = pricing.get(
                    "standardPrice",
                    {},
                )

                price_amount = standard_price.get("amount")

                assert price_amount is not None, (
                    f"EAN {ean}: отсутствует standardPrice.amount"
                )

                assert float(price_amount) > 0, (
                    f"EAN {ean}: цена <= 0"
                )

                assert (
                    standard_price.get("currency") == "EUR"
                ), (
                    f"Неверная валюта: "
                    f"{standard_price.get('currency')}"
                )

                # 4. MSRP
                msrp = pricing.get("msrp", {})

                msrp_amount = msrp.get("amount")

                assert msrp_amount is not None, (
                    f"EAN {ean}: отсутствует MSRP"
                )

                assert float(msrp_amount) >= float(price_amount), (
                    f"MSRP {msrp_amount} меньше цены {price_amount}"
                )

                assert msrp.get("currency") == "EUR", (
                    f"MSRP currency != EUR: "
                    f"{msrp.get('currency')}"
                )

                # 5. Media assets
                media_assets = payload.get(
                    "mediaAssets",
                    [],
                )

                assert isinstance(
                    media_assets,
                    list,
                ), "mediaAssets должен быть list"

                assert len(media_assets) <= 10, (
                    f"Слишком много изображений: "
                    f"{len(media_assets)}"
                )

                for media in media_assets:
                    assert media.get("type") == "IMAGE", (
                        f"Неверный media type: "
                        f"{media.get('type')}"
                    )

                    location = media.get("location", "")

                    assert (
                        location.startswith("http://")
                        or location.startswith("https://")
                    ), (
                        f"Некорректный image URL: {location}"
                    )

                # 6. Logistics
                logistics = payload.get("logistics", {})

                assert isinstance(
                    logistics,
                    dict,
                ), "logistics должен быть dict"

                assert (
                    logistics.get("packingUnitCount") == 1
                ), (
                    f"Неверный packingUnitCount: "
                    f"{logistics.get('packingUnitCount')}"
                )

                packing_units = logistics.get(
                    "packingUnits",
                    [],
                )

                assert packing_units, (
                    f"EAN {ean}: отсутствуют packingUnits"
                )

                packing_unit = packing_units[0]

                assert packing_unit.get("weight", 0) > 0
                assert packing_unit.get("width", 0) > 0
                assert packing_unit.get("height", 0) > 0
                assert packing_unit.get("length", 0) > 0

                # 7. Compliance
                compliance = payload.get(
                    "compliance",
                    {},
                )

                assert isinstance(
                    compliance,
                    dict,
                ), "compliance должен быть dict"

                product_safety = compliance.get(
                    "productSafety",
                    {},
                )

                assert product_safety.get(
                    "addresses"
                ), (
                    f"EAN {ean}: отсутствуют compliance addresses"
                )

                # 8. Warranty sanitization
                payload_attrs = product_description.get(
                    "attributes",
                    [],
                )

                for attr in payload_attrs:
                    attr_name = str(
                        attr.get("name", "")
                    ).lower()

                    assert "garantie" not in attr_name, (
                        f"Запрещенный warranty-атрибут: "
                        f"{attr.get('name')}"
                    )

                # 9. Warranty text in values
                for attr in payload_attrs:
                    for value in attr.get("values", []):
                        assert (
                            "garantie" not in str(value).lower()
                        ), (
                            f"Гарантия обнаружена в атрибуте "
                            f"{attr.get('name')}: {value}"
                        )

                # 10. Bullet points
                bullets = product_description.get(
                    "bulletPoints",
                    [],
                )

                assert isinstance(
                    bullets,
                    list,
                ), "bulletPoints должен быть list"

                assert len(bullets) <= 5, (
                    f"Слишком много bulletPoints: "
                    f"{len(bullets)}"
                )

                for bullet in bullets:
                    bullet_text = str(bullet).lower()

                    assert "garantie" not in bullet_text, (
                        f"Гарантия обнаружена в bulletPoint: "
                        f"{bullet}"
                    )

                    assert len(str(bullet)) <= 180, (
                        f"Bullet Point > 180 символов: "
                        f"{bullet}"
                    )

                # 11. Description should not contain HTML
                assert "<strong>" not in description.lower()
                assert "<br" not in description.lower()

                # ----------------------------------------------------------
                # Result
                # ----------------------------------------------------------

                print(
                    f"  ✓ [{idx}/{len(mappings)}] "
                    f"EAN {ean} | "
                    f"Категория: {cat_name} | "
                    f"Цена: {price_amount} € | "
                    f"MSRP: {msrp_amount} € | "
                    f"Фото: {len(media_assets)} | "
                    f"brandId: {brand_id}"
                )

            except Exception as item_error:
                all_ok = False
                print(
                    f"  ✗ [{idx}/{len(mappings)}] "
                    f"EAN {getattr(m, 'product_ean', 'UNKNOWN')}: "
                    f"{item_error}"
                )

        if all_ok:
            print(
                "  ✓ Формирование OTTO payload "
                "соответствует текущему formatter"
            )

        return all_ok

    except Exception as e:
        print(f"  ✗ Общая ошибка payload test: {e}")
        return False

    finally:
        db.close()


# ============================================================================
# MAIN
# ============================================================================

async def main():
    print("\n")
    print("=======================================================")
    print("   OTTO PUBLISH / EXPORT DIAGNOSTIC TEST")
    print("=======================================================")

    # 1. OAuth
    auth_results = check_otto_auth()

    # 2. SQLite
    export_ok = await check_sqlite_export(
        factory_id="504201",
        account="JV",
    )

    # 3. Payload
    payload_ok = check_otto_product_payload(
        factory_id="504201",
        account="JV",
    )

    # ------------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------------

    print("\n=======================================================")
    print(" ИТОГ ПРОВЕРКИ")
    print("=======================================================")

    for account, ok in auth_results.items():
        print(
            f"  Авторизация OTTO [{account}]: "
            f"{'УСПЕШНО' if ok else 'ОШИБКА'}"
        )

    print(
        f"  SQLite export: "
        f"{'УСПЕШНО' if export_ok else 'ОШИБКА'}"
    )

    print(
        f"  OTTO payload: "
        f"{'УСПЕШНО' if payload_ok else 'ОШИБКА'}"
    )

    print("=======================================================")
    print()


if __name__ == "__main__":
    asyncio.run(main())