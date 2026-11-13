"""Account-scoped operator edits, separate from source and publication records."""

from __future__ import annotations

import json
import hashlib
import re
from html import unescape
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from services.operation_service import OperationError, append_operation_revision
from services.title_policy import PreparedTitleError, prepare_title


def _candidate(conn: Connection, account: str, candidate_id: str, *, lock: bool = False) -> str:
    try:
        candidate_id = str(UUID(candidate_id))
    except (TypeError, ValueError, AttributeError):
        raise OperationError("INVALID_PRODUCT_ID", "Product ID must be a candidate episode UUID.") from None
    suffix = " FOR UPDATE OF ce" if lock else ""
    found = conn.execute(text(
        "SELECT ce.id FROM candidate_episodes ce JOIN source_products sp "
        "ON sp.id=ce.source_product_id AND sp.account=ce.account AND sp.data_scope=ce.data_scope "
        "WHERE ce.id=:id AND ce.account=:account AND ce.data_scope='PRODUCTION'" + suffix
    ), {"id": candidate_id, "account": account}).scalar_one_or_none()
    if found is None:
        raise OperationError("PRODUCT_NOT_FOUND", "Production product was not found in this account.")
    return candidate_id


def _read(conn: Connection, account: str, candidate_id: str) -> dict[str, Any] | None:
    row = conn.execute(text(
        "SELECT title, category, attributes_json, version, updated_by, updated_at "
        "FROM restart_product_edits WHERE account=:account AND candidate_episode_id=:id "
        "AND data_scope='PRODUCTION'"
    ), {"id": candidate_id, "account": account}).mappings().first()
    if row is None:
        return None
    return {"title": row["title"], "category": row["category"], "attributes": row["attributes_json"],
            "version": row["version"], "updatedBy": row["updated_by"], "updatedAt": row["updated_at"].isoformat()}


def get_product_edit(conn: Connection, *, account: str, candidate_id: str) -> dict[str, Any]:
    candidate_id = _candidate(conn, account, candidate_id)
    return {"edit": _read(conn, account, candidate_id)}


def save_product_edit(conn: Connection, *, account: str, candidate_id: str, expected_version: int,
                      title: str, category: str, attributes: dict[str, str], actor: str) -> dict[str, Any]:
    candidate_id = _candidate(conn, account, candidate_id, lock=True)
    current = _read(conn, account, candidate_id)
    current_version = current["version"] if current else 0
    if expected_version != current_version:
        raise OperationError("VERSION_CONFLICT", "Product edits changed since they were loaded.",
                             {"currentVersion": current_version})
    operation = conn.execute(text(
        "SELECT id, status, submission_state FROM restart_operations WHERE account=:account "
        "AND candidate_episode_id=:id AND data_scope='PRODUCTION' ORDER BY created_at DESC LIMIT 1 FOR UPDATE"
    ), {"account": account, "id": candidate_id}).mappings().first()
    if operation is not None:
        if operation["submission_state"] != "NEVER_SUBMITTED" or operation["status"] not in {
            "DRAFT", "PREPARING", "QC_PENDING", "NEEDS_REVIEW", "READY"
        }:
            raise OperationError("OPERATION_NOT_EDITABLE", "This operation has already entered submission or a terminal state.")
        queued = conn.execute(text(
            "SELECT 1 FROM restart_items WHERE account=:account AND data_scope='PRODUCTION' "
            "AND operation_id=:operation_id AND status NOT IN ('FAILED','CANCELLED','PUBLISHED') LIMIT 1"
        ), {"account": account, "operation_id": operation["id"]}).first()
        if queued:
            raise OperationError("OPERATION_QUEUED", "Remove the operation from the active queue before editing.")
        if operation["status"] in {"READY", "QC_PENDING"}:
            conn.execute(text(
                "UPDATE restart_operations SET status='NEEDS_REVIEW', version=version+1, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=:operation_id AND account=:account AND data_scope='PRODUCTION'"
            ), {"operation_id": operation["id"], "account": account})
            conn.execute(text(
                "INSERT INTO restart_events (id,account,data_scope,event_type,entity_type,entity_id,summary,metadata_json) "
                "VALUES (:id,:account,'PRODUCTION','PRODUCT_EDIT_INVALIDATED_APPROVAL','RESTART_OPERATION',:operation_id,"
                "'Saved product edits require a new review',CAST(:metadata AS jsonb))"
            ), {"id": str(uuid4()), "account": account, "operation_id": str(operation["id"]),
                "metadata": json.dumps({"actor": actor, "candidateEpisodeId": candidate_id})})
    conn.execute(text(
        "INSERT INTO restart_product_edits "
        "(account,candidate_episode_id,title,category,attributes_json,created_by,updated_by) "
        "VALUES (:account,:id,:title,:category,CAST(:attributes AS jsonb),:actor,:actor) "
        "ON CONFLICT (account,candidate_episode_id) DO UPDATE SET "
        "title=EXCLUDED.title, category=EXCLUDED.category, attributes_json=EXCLUDED.attributes_json, "
        "version=restart_product_edits.version+1, updated_by=EXCLUDED.updated_by, updated_at=CURRENT_TIMESTAMP"
    ), {"account": account, "id": candidate_id, "title": title.strip(), "category": category.strip(),
        "attributes": json.dumps(attributes, ensure_ascii=False), "actor": actor})
    edit = _read(conn, account, candidate_id)
    revision = None
    if operation is not None:
        revision = _append_saved_edit_revision(
            conn, account=account, candidate_id=candidate_id, edit=edit, actor=actor
        )
    return {"edit": edit, "revision": revision}


def _append_saved_edit_revision(
    conn: Connection, *, account: str, candidate_id: str, edit: dict[str, Any], actor: str
) -> dict[str, Any] | None:
    """Copy a saved ProductDrawer edit into a new immutable current revision when one exists."""
    operation = conn.execute(text(
        "SELECT id,status,version,current_revision_number,submission_state,new_ean,target_sku FROM restart_operations "
        "WHERE account=:account AND candidate_episode_id=:candidate_id AND data_scope='PRODUCTION' "
        "ORDER BY created_at DESC LIMIT 1 FOR UPDATE"
    ), {"account": account, "candidate_id": candidate_id}).mappings().first()
    if operation is None or not operation["current_revision_number"] or operation["submission_state"] != "NEVER_SUBMITTED":
        return None
    current = conn.execute(text(
        "SELECT * FROM operation_revisions WHERE operation_id=:id AND account=:account AND data_scope='PRODUCTION' "
        "AND revision_number=:number FOR UPDATE"
    ), {"id": operation["id"], "account": account,
        "number": operation["current_revision_number"]}).mappings().first()
    if current is None:
        return None
    def obj(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                result = json.loads(value)
                return result if isinstance(result, dict) else {}
            except (TypeError, ValueError):
                return {}
        return {}
    source = obj(current["source_snapshot_json"])
    content = obj(current["content_json"])
    payload = obj(current["payload_json"])
    product_payload = obj(payload.get("ottoProductPayload"))
    # Initial revisions without a package will consume the saved edit during
    # preparation, where current account snapshots and the EAN are available.
    if not product_payload:
        return None
    target_sku = operation["target_sku"]
    target_ean = operation["new_ean"]
    target_identity = payload.get("targetIdentity") if isinstance(payload.get("targetIdentity"), dict) else {}
    if (
        not isinstance(target_sku, str) or not re.fullmatch(r"[0-9]{13}", target_sku)
        or target_ean != target_sku
        or any(product_payload.get(field) != target_sku for field in ("productReference", "sku", "ean"))
        or any(payload.get(field) != target_sku for field in ("targetSku", "newEan", "productReference"))
        or any(target_identity.get(field) != target_sku for field in ("targetSku", "newEan", "productReference"))
    ):
        raise OperationError("TARGET_BARCODE_IDENTITY_MISMATCH", "Saved edits require the assigned EAN, SKU, and productReference to remain identical.")
    source_title = str(content.get("sourceTitle") or source.get("originalTitle") or edit["title"] or "")
    try:
        selected_title = prepare_title(source_title, edit["title"])
    except PreparedTitleError as error:
        raise OperationError(error.code, str(error), {"sourceLength": error.length, "limit": error.limit}) from None
    prepared_title = selected_title["title"]
    edit_ref = f"restart_product_edits/{candidate_id}/{edit['version']}"
    references = source.get("references") if isinstance(source.get("references"), list) else []
    source["references"] = references
    if not any(isinstance(item, dict) and item.get("system") == "OPERATOR" and item.get("resource") == edit_ref
               for item in references):
        references.append({
            "system": "OPERATOR", "method": "SAVED_PRODUCT_EDIT", "resource": edit_ref,
            "actor": actor[:200], "version": edit["version"],
        })
    from services.categories_repo import get_category, get_category_schema_attributes
    from services.brand_guard import is_forbidden_warranty_attribute
    selected_category = edit["category"].strip()
    category_record = get_category(selected_category)
    category_id = str(category_record.get("categoryId")) if category_record else selected_category
    schema_attrs = get_category_schema_attributes(category_id) if category_record else []
    source["categorySchema"] = {
        "verified": bool(category_record), "source": "OTTO_CATEGORY_SCHEMA" if category_record else "UNAVAILABLE",
        "account": account, "category": selected_category, "categoryId": category_id if category_record else None,
        "attributes": schema_attrs, "capturedAt": edit["updatedAt"],
        "referenceHash": hashlib.sha256(json.dumps(
            schema_attrs, sort_keys=True, ensure_ascii=False,
        ).encode("utf-8")).hexdigest(),
    }
    operator_ref = f"OPERATOR:{edit_ref}"
    review_basis = "Operator saved these account-scoped values from the OttoRestart ProductDrawer."
    source_refs = {
        f"{item.get('system').strip()}:{item.get('resource').strip()}"
        for item in references if isinstance(item, dict)
        and isinstance(item.get("system"), str) and isinstance(item.get("resource"), str)
        and item["system"].strip() and item["resource"].strip()
    }
    schema_names = {
        str(item.get("name") or "").strip().casefold(): str(item.get("name") or "").strip()
        for item in schema_attrs if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    attribute_map: dict[str, dict[str, Any]] = {}
    excluded_names: set[str] = set()

    def add_source_attribute(name: Any, raw_value: Any, source_ref: str, origin: str) -> None:
        clean_name = str(name or "").strip()
        value = str(raw_value or "").strip()
        canonical = schema_names.get(clean_name.casefold())
        if not clean_name or not value:
            return
        if source_ref not in source_refs:
            return
        if not canonical:
            excluded_names.add(clean_name)
            return
        if canonical not in attribute_map:
            attribute_map[canonical] = {"name": canonical, "value": value,
                                        "sourceRefs": [source_ref], "origin": origin}

    # Re-select source values from immutable raw snapshots when a category
    # change exposes fields that the previous category did not allow.
    otto_product = source.get("ottoProductResponse")
    product_description = otto_product.get("productDescription") if isinstance(otto_product, dict) else None
    otto_attributes = product_description.get("attributes") if isinstance(product_description, dict) else None
    if isinstance(otto_attributes, list):
        for item in otto_attributes:
            if not isinstance(item, dict):
                continue
            raw_values = item.get("values")
            if not isinstance(raw_values, list):
                raw_values = [item.get("value")]
            values = [str(value).strip() for value in raw_values if value is not None and str(value).strip()]
            add_source_attribute(item.get("name"), "; ".join(values), f"OTTO:/v5/products/{source.get('sourceSku', '')}", "OTTO_SOURCE")

    aftercool_identity = source.get("aftercoolIdentity")
    aftercool_attributes = aftercool_identity.get("attributes") if isinstance(aftercool_identity, dict) else None
    if isinstance(aftercool_attributes, list):
        for item in aftercool_attributes:
            if isinstance(item, dict):
                add_source_attribute(item.get("name"), item.get("value"), "Aftercool:/api/products", "AFTERCOOL_SOURCE")
    elif isinstance(aftercool_attributes, dict):
        for name, value in aftercool_attributes.items():
            add_source_attribute(name, value, "Aftercool:/api/products", "AFTERCOOL_SOURCE")

    # Keep existing prepared source values that are not represented in the raw
    # product envelopes, while discarding references not present in this revision.
    prior_attributes = content.get("attributes") if isinstance(content.get("attributes"), list) else []
    for item in prior_attributes:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        refs = item.get("sourceRefs")
        usable_refs = [ref for ref in refs if isinstance(ref, str) and ref in source_refs] if isinstance(refs, list) else []
        if usable_refs:
            add_source_attribute(name, item.get("value"), usable_refs[0], str(item.get("origin") or "OTTO_SOURCE"))

    confirmations = [{"field": "category", "value": selected_category, "sourceRef": operator_ref,
                      "basis": review_basis, "reviewedBy": actor[:200]}]
    if selected_title["strategy"] == "OPERATOR_EDIT":
        confirmations.append({"field": "title", "value": prepared_title, "sourceRef": operator_ref,
                              "basis": review_basis, "reviewedBy": actor[:200]})
    for name, raw_value in (edit.get("attributes") or {}).items():
        value = str(raw_value or "").strip()
        clean_name = str(name).strip()
        canonical = schema_names.get(clean_name.casefold())
        if not value:
            continue
        if not canonical:
            excluded_names.add(clean_name)
            continue
        if is_forbidden_warranty_attribute(canonical):
            continue
        attribute_map[canonical] = {"name": canonical, "value": value,
                                    "sourceRefs": [operator_ref], "origin": "MANUAL"}
        confirmations.append({"field": f"attribute:{canonical}", "value": value, "sourceRef": operator_ref,
                             "basis": review_basis, "reviewedBy": actor[:200]})
    attributes = [item for name, item in attribute_map.items()
                  if name.strip() and item["value"].strip() and not is_forbidden_warranty_attribute(name)]
    images = content.get("images") if isinstance(content.get("images"), list) else []
    confirmed_facts = [
        f"{item['name']}: {item['value']}"
        for item in attributes
        if item["name"].strip().casefold() not in {"marke", "brandid", "brand id"}
    ]
    raw_description = product_description.get("description") if isinstance(product_description, dict) else None
    if isinstance(raw_description, str):
        raw_description = " ".join(unescape(re.sub(r"<[^>]*>", " ", raw_description)).split())
    else:
        raw_description = ""
    target_text = raw_description or (
        f"{prepared_title}. " + "; ".join(confirmed_facts[:5])
        if prepared_title and confirmed_facts else ""
    )
    raw_bullets = product_description.get("bulletPoints") if isinstance(product_description, dict) else None
    if isinstance(raw_bullets, list):
        preserved_bullets = [
            " ".join(unescape(re.sub(r"<[^>]*>", " ", item)).split())
            for item in raw_bullets if isinstance(item, str) and item.strip()
        ]
        preserved_bullets = [item for item in preserved_bullets if item]
    else:
        preserved_bullets = []
    target_bullets = preserved_bullets or confirmed_facts[:5]
    content.update({"title": prepared_title, "sourceTitle": source_title,
                    "titlePreparation": {"strategy": selected_title["strategy"],
                                         "sourceLength": selected_title["sourceLength"],
                                         "preparedLength": selected_title["preparedLength"],
                                         "maxCodePoints": 70},
                    "category": selected_category, "attributes": attributes, "images": images,
                    "description": target_text, "bulletPoints": target_bullets,
                    "operatorEdit": {"candidateEpisodeId": candidate_id, "version": edit["version"],
                                     "updatedBy": actor[:200], "updatedAt": edit["updatedAt"]},
                    "attributeSchemaFilter": {"schemaVerified": bool(category_record),
                                               "acceptedCount": len(attributes),
                                               "excludedNames": sorted(excluded_names)},
                    "manualReview": {"reviewedBy": actor[:200], "reviewedAt": edit["updatedAt"],
                                     "reason": "Operator-saved ProductDrawer changes were copied into a new immutable revision.",
                                     "confirmations": confirmations}})
    target_description = obj(product_payload.get("productDescription"))
    target_description.update({
        "category": category_id,
        "productLine": prepared_title,
        "description": target_text,
        "bulletPoints": target_bullets,
        "attributes": [{"name": item["name"],
                        "values": [part.strip() for part in item["value"].split(";") if part.strip()],
                        "additional": False}
                        for item in attributes if item["name"].strip().casefold() not in {"marke", "brandid", "brand id"}],
    })
    product_payload["productDescription"] = target_description
    if any(product_payload.get(field) != target_sku for field in ("productReference", "sku", "ean")):
        raise OperationError("TARGET_BARCODE_IDENTITY_MISMATCH", "Saved edits cannot change the reserved target identity.")
    content["ottoProductPayload"] = product_payload
    prior_gaps = payload.get("readinessGaps") if isinstance(payload.get("readinessGaps"), list) else []
    gaps = [gap for gap in prior_gaps if isinstance(gap, str)
            if gap not in {"CATEGORY_SCHEMA_UNAVAILABLE", "REQUIRED_ATTRIBUTES_MISSING", "ATTRIBUTES_MISSING"}]
    if not category_record:
        gaps.append("CATEGORY_SCHEMA_UNAVAILABLE")
    present_names = {item["name"] for item in attributes}
    if any(isinstance(item, dict) and (item.get("required") is True or item.get("legal") is True)
           and item.get("name") not in present_names for item in schema_attrs):
        gaps.append("REQUIRED_ATTRIBUTES_MISSING")
    if not attributes:
        gaps.append("ATTRIBUTES_MISSING")
    payload.update({"title": prepared_title, "category": selected_category, "attributes": attributes,
                    "images": images, "ottoProductPayload": product_payload,
                    "readinessGaps": sorted(set(gaps))})
    from services.operation_service import _json_object, _json_list
    result = append_operation_revision(
        conn, account=account, operation_id=str(operation["id"]), expected_version=int(operation["version"]),
        source_snapshot=source, content=content, price_snapshot=_json_object(current["price_snapshot_json"]),
        fulfillment_snapshot=_json_object(current["fulfillment_snapshot_json"]),
        gtin_evidence_ids=_json_list(current["gtin_evidence_ids_json"]), payload=payload, actor=actor,
    )
    return result
