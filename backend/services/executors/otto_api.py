import json
import re
from datetime import datetime, timezone
from typing import List, Dict, Any
from sqlalchemy import func
from sqlalchemy.orm import Session
import models
from .base import BasePublishExecutor
from ..otto_client import OttoMarketClient
from ..aftercool_service import parse_aftercool_product, parse_price, apply_aftercool_prices
from ..otto_formatter import format_otto_product

def parse_otto_error_payload(resp_data: Any) -> tuple[str, list[str]]:
    """
    Parses OTTO API error payloads into a readable string and returns a list of attributes to skip.
    Returns: (readable_error_string, list_of_attributes_to_remove)
    """
    if not resp_data:
        return "Unknown error", []

    if isinstance(resp_data, str):
        try:
            resp_data = json.loads(resp_data)
        except Exception:
            m = re.search(r"für das Attribut ['\"]([^'\"]+)['\"]", resp_data, re.IGNORECASE)
            if not m:
                m = re.search(r"Attribute ['\"]([^'\"]+)['\"]", resp_data, re.IGNORECASE)
            if m:
                attr_name = m.group(1).strip()
                return resp_data[:500], [attr_name]
            return resp_data[:500], []

    error_msgs = []
    attrs_to_remove = []

    if isinstance(resp_data, dict):
        err_list = resp_data.get("errors", [])
        if not err_list and "title" in resp_data:
            err_list = [resp_data]

        for err in err_list:
            if not isinstance(err, dict):
                continue
            title = err.get("title") or err.get("code") or ""
            detail = err.get("detail") or err.get("message") or ""
            field = err.get("field") or ""

            full_text = f"{title} {detail}".strip()
            full_lower = full_text.lower()

            # Extract attribute name if possible
            attr_name = ""
            if field.startswith("attributes.") or field.startswith("product.attributes."):
                attr_name = field.split("attributes.")[-1]
            elif "für das Attribut" in full_text:
                m = re.search(r"für das Attribut ['\"]([^'\"]+)['\"]", full_text, re.IGNORECASE)
                if m:
                    attr_name = m.group(1).strip()
            elif "für das Merkmal" in full_text:
                m = re.search(r"für das Merkmal ['\"]([^'\"]+)['\"]", full_text, re.IGNORECASE)
                if m:
                    attr_name = m.group(1).strip()
            elif "Attribute '" in full_text:
                parts = full_text.split("Attribute '")
                if len(parts) > 1:
                    attr_name = parts[1].split("'")[0]
            elif "Attribute \"" in full_text:
                parts = full_text.split("Attribute \"")
                if len(parts) > 1:
                    attr_name = parts[1].split("\"")[0]

            display_msg = detail if detail else title
            if title == "ATTRIBUTE_NOT_ALLOWED" or "not permitted" in full_lower or "not allowed" in full_lower or "unknown attribute" in full_lower:
                msg = f"Лишний/недопустимый атрибут"
                if attr_name:
                    msg += f" '{attr_name}'"
                    attrs_to_remove.append(attr_name)
                msg += f": {display_msg}"
                error_msgs.append(msg)
            elif title == "MANDATORY_ATTRIBUTE_MISSING" or "mandatory" in full_lower:
                msg = f"Не заполнен обязательный атрибут"
                if attr_name:
                    msg += f" '{attr_name}'"
                msg += f": {display_msg}"
                error_msgs.append(msg)
            elif title == "INVALID_ATTRIBUTE_VALUE" or "invalid" in full_lower or "kein gültiger vorgabewert" in full_lower:
                msg = f"Некорректное значение поля"
                if attr_name:
                    msg += f" '{attr_name}'"
                    attrs_to_remove.append(attr_name)
                msg += f": {display_msg}"
                error_msgs.append(msg)
            else:
                msg = f"{title}: {detail}" if (title and detail) else (detail or title)
                if field:
                    msg += f" (Поле: {field})"
                if attr_name and ("vorgabewert" in full_lower or "not allowed" in full_lower):
                    attrs_to_remove.append(attr_name)
                error_msgs.append(msg)

    if not error_msgs:
        return str(resp_data)[:500], []

    return " ; ".join(error_msgs), list(dict.fromkeys(attrs_to_remove))


def extract_images_from_aftercool_row(
    row: dict[str, Any], p: dict[str, Any] = None, account: str = None
) -> list[str]:
    """
    Extracts all images from Aftercool product data (main picture and additional pictures).
    Deduplicates URLs while preserving original gallery order.
    Supports formats:
    - PictureURL, GalleryURL, image_url (main image)
    - pictureurls, PictureURLs, additional_images (string with ;, ,, |, or newline delimiter, or list)
    """
    import re
    pic_urls = []
    p = p or {}
    row = row or {}

    # 1. Main image
    main_pic = (
        row.get("PictureURL")
        or row.get("GalleryURL")
        or row.get("pictureurl")
        or row.get("galleryurl")
        or p.get("image_url")
        or p.get("PictureURL")
        or p.get("GalleryURL")
        or ""
    )
    if not main_pic:
        for k, v in row.items():
            if str(k).strip().lower() in ("pictureurl", "galleryurl") and v:
                main_pic = v
                break

    if main_pic and str(main_pic).strip():
        pic_urls.append(str(main_pic).strip())

    # 2. Additional images
    extra_pics = (
        row.get("pictureurls")
        or row.get("PictureURLs")
        or row.get("pictureUrls")
        or row.get("PictureUrls")
        or row.get("picture_urls")
        or row.get("additional_images")
        or p.get("pictureurls")
        or p.get("PictureURLs")
        or p.get("pictureUrls")
        or p.get("PictureUrls")
        or p.get("picture_urls")
        or p.get("additional_images")
        or ""
    )
    if not extra_pics:
        for k, v in row.items():
            if str(k).strip().lower() in ("pictureurls", "picture_urls", "additional_images", "additionalimages") and v:
                extra_pics = v
                break

    if isinstance(extra_pics, (list, tuple)):
        for u in extra_pics:
            u_str = str(u).strip()
            if u_str and u_str not in pic_urls:
                pic_urls.append(u_str)
    elif isinstance(extra_pics, str) and extra_pics.strip():
        parts = [x.strip() for x in re.split(r"[;\n|,]+", extra_pics.strip()) if x.strip()]
        for part in parts:
            if part and part not in pic_urls:
                pic_urls.append(part)

    # Filter out empty or non-URL values
    valid_urls = []
    for u in pic_urls:
        u_clean = u.strip()
        if u_clean and (u_clean.startswith("http://") or u_clean.startswith("https://") or u_clean.startswith("/")):
            if u_clean not in valid_urls:
                valid_urls.append(u_clean)

    if account and str(account).upper() == "XL":
        from ..brand_guard import filter_brand_images_sync
        valid_urls = filter_brand_images_sync("XL", valid_urls)

    return valid_urls


class OttoApiExecutor(BasePublishExecutor):
    """
    Executes product publication directly through Otto Market v5 REST API.
    """

    def publish_batch(
        self,
        plan: models.PublishPlan,
        items: List[models.PublishQueueItem],
        db: Session
    ) -> Dict[str, Any]:
        account_name_str = plan.account_name.value if hasattr(plan.account_name, "value") else str(plan.account_name)
        
        # 1. Fetch account credentials
        settings = (
            db.query(models.AccountSettings)
            .filter(models.AccountSettings.account_name == account_name_str)
            .first()
        )

        published_count = 0
        processing_count = 0
        failed_count = 0
        skipped_count = 0

        if not settings or not getattr(settings, "otto_client_id", None) or not getattr(settings, "otto_client_secret", None):
            error_msg = f"Otto API credentials (client_id / client_secret) are not configured for account {account_name_str}."
            for item in items:
                item.attempts += 1
                item.error_message = error_msg
                item.status = models.QueueItemStatus.FAILED
            db.commit()
            return {
                "success": False,
                "error": error_msg,
                "total": len(items),
                "published": 0,
                "processing": 0,
                "failed": len(items),
                "skipped": 0,
            }

        client = OttoMarketClient(
            client_id=settings.otto_client_id,
            client_secret=settings.otto_client_secret
        )

        # 2. Collect product mappings and attributes
        eans = [it.product_ean for it in items]
        mappings = (
            db.query(models.ProductMapping)
            .filter(
                models.ProductMapping.product_ean.in_(eans),
                models.ProductMapping.account_name == plan.account_name
            )
            .all()
        )
        mapping_by_ean = {m.product_ean: m for m in mappings}

        category_ids = {m.category_id for m in mappings if m.category_id}
        from ..categories_service import resolve_category_names
        cat_name_by_id = resolve_category_names(category_ids, db)

        attributes = (
            db.query(models.ProductAttribute)
            .filter(
                models.ProductAttribute.product_ean.in_(eans),
                models.ProductAttribute.account_name == plan.account_name
            )
            .all()
        )
        attrs_by_ean = {}
        for attr in attributes:
            attrs_by_ean.setdefault(attr.product_ean, {})[attr.attribute_name] = attr.attribute_value

        # Release database transaction locks before starting Aftercool network operations
        db.commit()

        # Fetch extra product details (all images, description) from Aftercool
        aftercool_product_map = {}
        try:
            from routers.aftercool import get_aftercool_session, AFTERCOOL_BASE_URL
            from ..aftercool_service import resolve_aftercool_account
            import httpx
            import asyncio

            aftercool_acc = resolve_aftercool_account(str(plan.factory_id).strip(), account_name_str, db=db)

            async def fetch_af_data():
                session_cookie = await get_aftercool_session()
                async with httpx.AsyncClient(cookies={"session": session_cookie}, timeout=35.0) as af_client:
                    async def safe_get(url: str, params: dict):
                        nonlocal session_cookie
                        try:
                            res = await af_client.get(url, params=params)
                            if res.status_code in (401, 403):
                                session_cookie = await get_aftercool_session(force_refresh=True)
                                af_client.cookies.set("session", session_cookie)
                                res = await af_client.get(url, params=params, cookies={"session": session_cookie})
                            return res
                        except Exception as ex:
                            import logging
                            logging.warning(f"Error fetching from Aftercool URL {url}: {ex}")
                            return None

                    offset = 0
                    limit = 500
                    while True:
                        params = {
                            "factory_id": str(plan.factory_id).strip(),
                            "account": aftercool_acc,
                            "dataset": "lister",
                            "include_row": 1,
                            "limit": limit,
                            "offset": offset,
                        }
                        res = await safe_get(f"{AFTERCOOL_BASE_URL}/api/products", params=params)
                        if not res or res.status_code != 200:
                            break
                        data = res.json()
                        items_list = data.get("items", data) if isinstance(data, dict) else data
                        if not isinstance(items_list, list) or not items_list:
                            break
                        for p in items_list:
                            row = p.get("row", {})
                            ean = str(p.get("ean") or row.get("EAN") or "").strip()
                            if not ean:
                                continue

                            pic_urls = extract_images_from_aftercool_row(row, p, account=account_name_str)
                            desc = (
                                row.get("Artikelbeschreibung")
                                or row.get("Beschreibung")
                                or row.get("Description")
                                or p.get("description")
                                or ""
                            )
                            cis = (
                                row.get("CustomItemSpecifics")
                                or p.get("CustomItemSpecifics")
                                or row.get("custom_item_specifics")
                                or ""
                            )
                            kurz = (
                                row.get("Kurzbeschreibung")
                                or row.get("kurzbeschreibung")
                                or row.get("ShortDescription")
                                or row.get("short_description")
                                or p.get("kurzbeschreibung")
                                or ""
                            )
                            parsed = parse_aftercool_product(p, default_factory_id=str(plan.factory_id))
                            aftercool_product_map[ean] = {
                                "price": str(parsed.price) if parsed and parsed.price is not None else None,
                                "price_af": str(parsed.price_af) if parsed and parsed.price_af is not None else None,
                                "images": pic_urls,
                                "description": str(desc).strip(),
                                "custom_item_specifics": str(cis).strip(),
                                "factory_id": str(p.get("factory_id") or row.get("CatalogID") or "").strip(),
                                "kurzbeschreibung": str(kurz).strip(),
                            }

                        offset += len(items_list)
                        total = data.get("total") if isinstance(data, dict) else None
                        if total is not None and offset >= total:
                            break
                        if len(items_list) < limit:
                            break

                    # Fallback for any EANs in batch not found in the factory batch
                    missing_eans = [e for e in eans if e not in aftercool_product_map]
                    for m_ean in missing_eans:
                        m_rec = mapping_by_ean.get(m_ean)
                        m_fid = m_rec.factory_id if m_rec else str(plan.factory_id).strip()
                        m_acc = (
                            m_rec.source_account
                            if (m_rec and m_rec.source_account)
                            else resolve_aftercool_account(m_fid, account_name_str, db=db)
                        )
                        try:
                            s_items = []
                            res_s = await safe_get(
                                f"{AFTERCOOL_BASE_URL}/api/products/by-ean",
                                params={"account": m_acc, "ean": m_ean, "dataset": "lister", "limit": 1}
                            )
                            if res_s and res_s.status_code == 200:
                                s_data = res_s.json()
                                s_items = s_data.get("items", []) if isinstance(s_data, dict) else s_data

                            # Secondary fallback to search query if by-ean didn't return items
                            if not s_items:
                                res_q = await safe_get(
                                    f"{AFTERCOOL_BASE_URL}/api/products",
                                    params={"q": m_ean, "account": m_acc, "dataset": "lister", "include_row": 1, "limit": 5}
                                )
                                if res_q and res_q.status_code == 200:
                                    q_data = res_q.json()
                                    q_items = q_data.get("items", []) if isinstance(q_data, dict) else q_data
                                    for qi in q_items:
                                        qi_row = qi.get("row", {})
                                        qi_ean = str(qi.get("ean") or qi_row.get("EAN") or "").strip()
                                        if qi_ean == m_ean:
                                            s_items = [qi]
                                            break

                            if s_items:
                                p_s = s_items[0]
                                row_s = p_s.get("row", {})
                                cis_s = (
                                    row_s.get("CustomItemSpecifics")
                                    or p_s.get("CustomItemSpecifics")
                                    or row_s.get("custom_item_specifics")
                                    or ""
                                )
                                desc_s = (
                                    row_s.get("Artikelbeschreibung")
                                    or row_s.get("Beschreibung")
                                    or row_s.get("Description")
                                    or p_s.get("description")
                                    or ""
                                )
                                pic_urls_s = extract_images_from_aftercool_row(row_s, p_s, account=account_name_str)
                                kurz_s = (
                                    row_s.get("Kurzbeschreibung")
                                    or row_s.get("kurzbeschreibung")
                                    or row_s.get("ShortDescription")
                                    or row_s.get("short_description")
                                    or p_s.get("kurzbeschreibung")
                                    or ""
                                )
                                parsed = parse_aftercool_product(p_s, default_factory_id=m_fid)
                                aftercool_product_map[m_ean] = {
                                    "price": str(parsed.price) if parsed and parsed.price is not None else None,
                                    "price_af": str(parsed.price_af) if parsed and parsed.price_af is not None else None,
                                    "images": pic_urls_s,
                                    "description": str(desc_s).strip(),
                                    "custom_item_specifics": str(cis_s).strip(),
                                    "factory_id": str(p_s.get("factory_id") or row_s.get("CatalogID") or "").strip(),
                                    "kurzbeschreibung": str(kurz_s).strip(),
                                }
                        except Exception as ex_m:
                            import logging
                            logging.debug(f"Fallback fetch error for EAN {m_ean}: {ex_m}")

            asyncio.run(fetch_af_data())
        except Exception as e:
            import logging
            logging.warning(f"Failed to fetch extra data from Aftercool for factory {plan.factory_id}: {e}")

        # 3. AI Title Generation Safety Net:
        # Check all items in the batch. If any item has len(product_name) > 70, automatically generate
        # a concise German title (<= 70 chars) via GPT-4o-mini so it meets OTTO's strict productLine limit.
        import asyncio
        import logging
        from ..ai_title_generator import resolve_openai_key, generate_single_ai_title
        from ..categories_service import resolve_category_names

        items_needing_ai = []
        for item in items:
            m = mapping_by_ean.get(item.product_ean)
            if m and len((m.product_name or "").strip()) > 70:
                items_needing_ai.append((item, m))

        if items_needing_ai:
            openai_api_key = resolve_openai_key(db=db)
            cat_ids = {m.category_id for _, m in items_needing_ai if m.category_id}
            cat_names = resolve_category_names(cat_ids, db) if cat_ids else {}

            async def run_ai_title_safety_net():
                sem_ai = asyncio.Semaphore(10)
                async def process_one(item, m):
                    ean = item.product_ean
                    curr_title = (m.product_name or "").strip()
                    cat_name = cat_names.get(str(m.category_id), "") or "Möbel"
                    if not openai_api_key:
                        # User requirement: If AI failed / key missing, DO NOT truncate or publish.
                        # Mark as Error / Failed and exclude from export!
                        item.attempts += 1
                        item.status = models.QueueItemStatus.FAILED
                        item.error_message = f"Длина названия > 70 симв. ({len(curr_title)} симв.). Отсутствует OPENAI_API_KEY для генерации ИИ-названия. Товар не выгружен."
                        m.status = "Error"
                        m.last_error = item.error_message
                        return

                    async with sem_ai:
                        new_title = await generate_single_ai_title(openai_api_key, curr_title, category_name=cat_name)
                        if new_title and len(new_title) <= 70:
                            m.product_name = new_title
                            m.has_ai_title = True
                            logging.info(f"AI Safety Net: Generated title for EAN {ean}: '{new_title}' ({len(new_title)} chars)")
                        else:
                            # User requirement: If AI gave an error, do NOT change title to broken/truncated,
                            # mark item status as error, do not publish.
                            item.attempts += 1
                            item.status = models.QueueItemStatus.FAILED
                            item.error_message = f"Ошибка генерации ИИ-названия (длина > 70 симв., {len(curr_title)} симв.). Товар не выгружен в OTTO."
                            m.status = "Error"
                            m.last_error = item.error_message

                await asyncio.gather(*[process_one(itm, m) for itm, m in items_needing_ai])

            try:
                asyncio.run(run_ai_title_safety_net())
                db.commit()
            except Exception as e:
                logging.warning(f"Error in AI title safety net in OttoApiExecutor: {e}")

        # 4. Image Pipeline: isolation, mirroring for XL, and OTTO dimension enforcement (min 960px)
        from ..image_processor import mirror_image_for_xl, ensure_image_dimensions_url
        from ..brand_guard import sanitize_brand_attributes
        import httpx

        is_xl = account_name_str.upper() == "XL"
        mirrored_map = {}

        async def run_image_pipeline():
            # 1. Filter brand logos for XL
            if is_xl:
                from ..brand_guard import filter_brand_images
                sem_filter = asyncio.Semaphore(20)
                async def filter_one(ean_key):
                    if ean_key in aftercool_product_map:
                        curr_imgs = aftercool_product_map[ean_key].get("images", [])
                        if curr_imgs:
                            async with sem_filter:
                                clean_imgs = await filter_brand_images("XL", curr_imgs)
                                aftercool_product_map[ean_key]["images"] = clean_imgs

                await asyncio.gather(*[filter_one(itm.product_ean) for itm in items])

            # 2. Parallel mirroring for XL and dimension verification/upscaling (min 960px) for all items
            sem_img = asyncio.Semaphore(20)
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as http_client:
                async def process_item_images(ean_val):
                    af_imgs = aftercool_product_map.get(ean_val, {}).get("images", [])
                    map_rec = mapping_by_ean.get(ean_val)
                    main_candidate = af_imgs[0] if af_imgs else (map_rec.image_url if map_rec else None)
                    if not main_candidate:
                        return
                    async with sem_img:
                        if is_xl:
                            m_url = await mirror_image_for_xl(main_candidate, ean_val, "XL", client=http_client)
                            if m_url:
                                mirrored_map[ean_val] = m_url
                        else:
                            # For JV: ensure main image meets OTTO dimensions
                            valid_main = await ensure_image_dimensions_url(main_candidate, ean_val, client=http_client)
                            if valid_main:
                                mirrored_map[ean_val] = valid_main

                        # Also ensure secondary gallery images meet OTTO dimensions
                        if len(af_imgs) > 1:
                            new_af_imgs = list(af_imgs)
                            if not is_xl and ean_val in mirrored_map:
                                new_af_imgs[0] = mirrored_map[ean_val]
                            for idx in range(1, min(len(af_imgs), 10)):
                                sec_img = af_imgs[idx]
                                valid_sec = await ensure_image_dimensions_url(sec_img, ean_val, client=http_client)
                                new_af_imgs[idx] = valid_sec
                            aftercool_product_map[ean_val]["images"] = new_af_imgs

                await asyncio.gather(*[process_item_images(itm.product_ean) for itm in items])

        try:
            asyncio.run(run_image_pipeline())
        except Exception as e:
            logging.warning(f"Error in image pipeline in OttoApiExecutor: {e}")

        payloads = []
        item_lookup = {}
        for item in items:
            m = mapping_by_ean.get(item.product_ean)
            if not m:
                item.attempts += 1
                item.status = models.QueueItemStatus.FAILED
                item.error_message = f"Product mapping not found for EAN {item.product_ean}"
                failed_count += 1
                continue

            # Skip items that failed the AI title safety net or other pre-checks
            if item.status == models.QueueItemStatus.FAILED:
                failed_count += 1
                continue

            # Refresh from the already fetched Aftercool row before checking local data.
            # A manually entered price always wins over automatic Aftercool values.
            af_data = aftercool_product_map.get(item.product_ean, {})
            # The user may have edited the price while external enrichment ran.
            db.refresh(m, attribute_names=["price", "price_af", "has_manual_price"])
            apply_aftercool_prices(m, af_data.get("price"), af_data.get("price_af"))
            price = parse_price(m.price)
            if price is None and not getattr(m, "has_manual_price", False):
                price = parse_price(m.price_af)
            prod_price = str(price) if price is not None else ""

            raw_attrs = attrs_by_ean.get(item.product_ean, {}).copy()

            # 1. Product Description: resolve real description, not just product_name
            full_desc = (
                af_data.get("description")
                or raw_attrs.get("Beschreibung")
                or raw_attrs.get("description")
                or m.product_name
                or f"Product {item.product_ean}"
            )
            # Remove Beschreibung/description from regular attributes to prevent duplication
            raw_attrs.pop("Beschreibung", None)
            raw_attrs.pop("description", None)

            # 2. Images: collect all available images up to 10 with strict JV / XL isolation
            all_images = []
            af_images = af_data.get("images", [])

            if is_xl:
                from ..brand_guard import is_forbidden_brand_image_url
                clean_imgs = [img for img in af_images if not is_forbidden_brand_image_url("XL", img)]
                base_main_img = clean_imgs[0] if clean_imgs else (m.image_url if (m and not is_forbidden_brand_image_url("XL", m.image_url)) else None)

                mirrored_main = mirrored_map.get(item.product_ean) or base_main_img
                if mirrored_main and not is_forbidden_brand_image_url("XL", mirrored_main):
                    all_images.append(mirrored_main)

                if len(clean_imgs) > 1:
                    for extra_img in clean_imgs[1:]:
                        if extra_img and extra_img not in all_images and not is_forbidden_brand_image_url("XL", extra_img):
                            all_images.append(extra_img)
            else:
                # JV: All images from Aftercool including brand logo photo, NO mirroring
                base_main_img = mirrored_map.get(item.product_ean) or (af_images[0] if af_images else (m.image_url if m else None))
                if base_main_img:
                    all_images.append(base_main_img)
                if len(af_images) > 1:
                    for extra_img in af_images[1:]:
                        if extra_img and extra_img not in all_images:
                            all_images.append(extra_img)

            # OTTO Market API strict gallery limit is 10 images
            all_images = all_images[:10]

            prod_img = all_images[0] if all_images else (mirrored_map.get(item.product_ean, m.image_url) if is_xl else (m.image_url if m else ""))

            if not prod_price or not prod_img:
                item.attempts += 1
                item.status = models.QueueItemStatus.FAILED
                missing_parts = []
                if not prod_price:
                    missing_parts.append("цена")
                if not prod_img:
                    missing_parts.append("изображение")
                item.error_message = f"Данные товара неполные (отсутствует {', '.join(missing_parts)}). Заполните данные перед выгрузкой."
                m.status = "Error"
                failed_count += 1
                continue

            aftercool_info = {
                "name": m.product_name or f"Product {item.product_ean}",
                "description": full_desc,
                "price": prod_price,
                "images": all_images,
                "custom_item_specifics": af_data.get("custom_item_specifics", ""),
            }

            # Strictly sanitize brand attributes before dispatch to Otto
            safe_attrs = sanitize_brand_attributes(account_name_str, raw_attrs)
            cat_name = cat_name_by_id.get(m.category_id, m.category_id or "Standard")
            mapping_dict = {
                "attributes": safe_attrs,
                "category_id": m.category_id,
                "category": cat_name,
                "product_name": m.product_name,
                "price": prod_price,
            }

            payload = format_otto_product(
                item.product_ean,
                aftercool_info,
                mapping_dict,
                cat_name,
                account_name=account_name_str,
            )
            payloads.append(payload)
            item_lookup[item.product_ean] = item

        if not payloads:
            db.commit()
            return {
                "success": True,
                "total": len(items),
                "published": published_count,
                "processing": processing_count,
                "failed": failed_count,
                "skipped": skipped_count,
            }

        # 4. Dispatch to Otto API
        try:
            res = client.publish_products(payloads)
            status_code = res.get("status_code", 500)

            if status_code in (200, 201, 204):
                # Synchronous success (rare in v5, but supported)
                now_utc = datetime.now(timezone.utc)
                for item in item_lookup.values():
                    item.status = models.QueueItemStatus.PUBLISHED
                    item.published_at = now_utc
                    item.error_message = None
                    if m := mapping_by_ean.get(item.product_ean):
                        m.published_at = now_utc
                        m.status = "Published"
                    published_count += 1

                # Dispatch Availability API (stock=20 and shipping profile) with delayed Celery task
                try:
                    from tasks import dispatch_otto_availability_batches
                    dispatch_otto_availability_batches(
                        item_ids=[item.id for item in item_lookup.values() if item.id],
                        account_name=account_name_str,
                        plan_id=plan.id if plan else None,
                        base_countdown=60,
                        jitter_step=5,
                        aftercool_product_map=aftercool_product_map,
                    )
                except Exception as avail_err:
                    import logging
                    logging.warning(f"Failed to dispatch availability tasks after sync publish: {avail_err}")
            elif status_code == 202:
                # Asynchronous task accepted by Otto:
                # Save task references and set state to PROCESSING.
                # Background periodic task poll_otto_processing_items will verify final status.
                resp_obj = res.get("response") or {}
                links = {link.get("rel"): link.get("href") for link in resp_obj.get("links", []) if isinstance(link, dict)}
                self_href = links.get("self")
                failed_href = links.get("failed")

                for item in item_lookup.values():
                    item.status = models.QueueItemStatus.PROCESSING
                    item.otto_task_href = self_href
                    item.otto_task_failed_href = failed_href
                    item.poll_attempts = 0
                    item.error_message = None
                    if m := mapping_by_ean.get(item.product_ean):
                        m.status = "In Progress"
                    processing_count += 1
            elif status_code == 429:
                # Rate limit hit: keep items in pending so next beat tick will retry
                for item in item_lookup.values():
                    item.attempts += 1
                    item.error_message = "Otto 429 Too Many Requests (Rate limit reached). Retrying on next tick."
                    skipped_count += 1
            elif status_code in (500, 502, 503, 504):
                # Transient server errors: keep items in pending so next beat tick will retry
                for item in item_lookup.values():
                    item.attempts += 1
                    item.error_message = f"Otto transient server error {status_code}. Retrying on next tick."
                    skipped_count += 1
            else:
                resp_text = res.get("response") or res.get("error") or "Unknown error"
                readable_err, attrs_to_remove = parse_otto_error_payload(resp_text)
                
                for item in item_lookup.values():
                    if attrs_to_remove and item.attempts < 5:
                        # Auto-remediation: remove bad attributes case-insensitively
                        attrs_lower = [a.lower().strip() for a in attrs_to_remove]
                        db.query(models.ProductAttribute).filter(
                            models.ProductAttribute.product_ean == item.product_ean,
                            models.ProductAttribute.account_name == account_name_str,
                            func.lower(models.ProductAttribute.attribute_name).in_(attrs_lower)
                        ).delete(synchronize_session=False)
                        
                        item.attempts += 1
                        item.status = models.QueueItemStatus.PENDING # Retry it automatically
                        item.error_message = f"Авто-исправление: удалены лишние атрибуты ({', '.join(attrs_to_remove)}). Ожидает повторной отправки. Исходная ошибка: {readable_err}"
                        if m := mapping_by_ean.get(item.product_ean):
                            m.status = "Error"
                            m.last_error = item.error_message
                    else:
                        item.attempts += 1
                        item.status = models.QueueItemStatus.FAILED
                        if attrs_to_remove and item.attempts >= 5:
                            item.error_message = f"Превышен лимит авто-исправлений (5 попыток). Ошибка: {readable_err}"[:500]
                        else:
                            item.error_message = f"Otto API error {status_code}: {readable_err}"[:500]
                        if m := mapping_by_ean.get(item.product_ean):
                            m.status = "Error"
                            m.last_error = item.error_message
                        failed_count += 1

        except Exception as e:
            for item in item_lookup.values():
                item.attempts += 1
                item.status = models.QueueItemStatus.FAILED
                item.error_message = f"Exception during publish: {str(e)[:500]}"
                if m := mapping_by_ean.get(item.product_ean):
                    m.status = "Error"
                failed_count += 1

        db.commit()
        return {
            "success": (published_count > 0 or processing_count > 0),
            "total": len(items),
            "published": published_count,
            "processing": processing_count,
            "failed": failed_count,
            "skipped": skipped_count,
        }
