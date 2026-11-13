"""Production-only read projections for the OttoRestart Library."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

MAX_PAGE_SIZE = 200
MAX_AFTERCOOL_PAGE_SIZE = 100
MAX_LINEAGE_PER_PRODUCT = 50
MAX_OPERATION_HISTORY_PER_PRODUCT = 50
MAX_EVENT_HISTORY_PER_PRODUCT = 50


class LibraryQueryError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _require_page(page: int, page_size: int, *, maximum: int = MAX_PAGE_SIZE) -> None:
    if page < 1 or page_size < 1 or page_size > maximum:
        raise LibraryQueryError("VALIDATION_ERROR", f"Pagination must be within 1..{maximum}.")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _page(page: int, page_size: int, total: int) -> dict[str, int]:
    return {
        "page": page,
        "pageSize": page_size,
        "totalItems": total,
        "totalPages": (total + page_size - 1) // page_size,
    }


def _processing_confirmed(row: Any, *, operation_id: str, target_sku: str | None) -> bool:
    if not target_sku or not row["processing_event_id"]:
        return False
    metadata = _json_object(row["processing_metadata"])
    return (
        row["processing_event_type"] == "OTTO_PROCESSING_RESULT"
        and str(row["processing_entity_id"]) == operation_id
        and metadata.get("targetSku") == target_sku
        and metadata.get("result") == "SUCCEEDED"
    )


def _online_confirmed(row: Any, *, operation_id: str, target_sku: str | None) -> bool:
    if not target_sku or not row["online_observation_id"]:
        return False
    return (
        str(row["online_requested_sku"] or "") == target_sku
        and str(row["online_response_sku"] or "") == target_sku
        and row["online_marketplace_status"] == "ONLINE"
        and row["online_observed_at"] is not None
        and row["processing_created_at"] is not None
        and row["online_observed_at"] >= row["processing_created_at"]
    )


def _availability_item(row: Any, action: str, target: dict[str, Any]) -> dict[str, Any]:
    actions = _json_object(row["availability_actions"])
    current = _json_object(actions.get(action)) or _json_object(_json_object(row["availability_errors"]).get(action))
    return {
        "status": str(current.get("status") or "NOT_STARTED"),
        "attemptCount": int(current.get("attemptCount") or 0),
        "lastAttemptAt": current.get("lastAttemptAt"),
        "httpStatus": current.get("httpStatus"),
        "errorCode": current.get("errorCode"),
        "manualResolution": current.get("manualResolution"),
        "target": target.get("stockQuantity") if action == "QUANTITY" else {
            "shippingProfile": target.get("shippingProfile"),
            "shippingProfileName": target.get("shippingProfileName"),
            "processingTime": target.get("processingTime"),
            "originClassification": target.get("originClassification"),
            "classificationBasis": target.get("classificationBasis"),
        },
    }


def _link_item(row: Any) -> dict[str, Any]:
    operation_id = str(row["operation_id"]) if row["operation_id"] else None
    target_sku = row["target_sku"]
    processed = bool(
        operation_id
        and row["submission_state"] == "CONFIRMED"
        and _processing_confirmed(row, operation_id=operation_id, target_sku=target_sku)
    )
    online = bool(
        processed
        and row["operation_status"] == "PUBLISHED"
        and _online_confirmed(row, operation_id=operation_id, target_sku=target_sku)
    )
    linked_identifier = {
        "ean": row["new_ean"],
        "targetSku": target_sku,
        "linkId": str(row["link_id"]),
        "linkStatus": row["link_status"],
        "linkedAt": _iso(row["linked_at"]),
    }
    confirmed_online_identifiers = []
    if online:
        confirmed_online_identifiers.append(
            {
                "ean": row["new_ean"],
                "sku": target_sku,
                "status": "ONLINE",
                "checkedAt": _iso(row["online_observed_at"]),
                "observationId": str(row["online_observation_id"]),
            }
        )

    latest = None
    if row["latest_observation_id"]:
        latest = {
            "sku": row["latest_requested_sku"],
            "responseSku": row["latest_response_sku"],
            "marketPlaceStatus": row["latest_marketplace_status"],
            "checkedAt": _iso(row["latest_observed_at"]),
            "identityVerified": row["latest_requested_sku"] == row["latest_response_sku"] == target_sku,
        }

    fulfillment_snapshot = _json_object(row["fulfillment_snapshot_json"])
    target_fulfillment = _json_object(fulfillment_snapshot.get("target"))
    publication_state = "NOT_PREPARED"
    if operation_id:
        if online:
            publication_state = "ONLINE_CONFIRMED"
        elif processed:
            publication_state = "PROCESSING_CONFIRMED_AWAITING_ONLINE"
        elif row["submission_state"] in {"IN_FLIGHT", "UNKNOWN"}:
            publication_state = "SUBMITTED_AWAITING_PROCESSING"
        else:
            publication_state = str(row["operation_status"] or row["submission_state"] or "DRAFT")

    return {
        "id": str(row["link_id"]),
        "account": row["account"],
        "sourceProductId": str(row["source_product_id"]),
        "sourceType": row["source_type"],
        "aftercoolProductId": row["aftercool_product_id"],
        "aftercoolSku": row["aftercool_sku"],
        "sourceEan": row["source_ean"],
        "newEan": row["new_ean"],
        "factoryId": row["factory_id"],
        "factoryName": row["factory_name"],
        "title": row["original_title"],
        "imageUrl": row["image_url"],
        "status": row["link_status"],
        "createdAt": _iso(row["linked_at"]),
        "linkStatus": row["link_status"],
        "operationId": operation_id,
        "attemptNumber": row["attempt_number"],
        "operationStatus": row["operation_status"],
        "revisionNumber": row["current_revision_number"],
        "publicationState": publication_state,
        "targetSku": target_sku,
        "linkedIdentifier": linked_identifier,
        "confirmedOnlineIdentifiers": confirmed_online_identifiers,
        "latestMarketplaceObservation": latest,
        "processingConfirmed": processed,
        "onlineConfirmed": online,
        "availability": {
            "stock": _availability_item(row, "QUANTITY", target_fulfillment),
            "delivery": _availability_item(row, "DELIVERY_INFORMATION", target_fulfillment),
        },
    }


_LINK_SELECT = """
    SELECT l.id AS link_id, l.account, l.source_product_id, l.source_ean, l.new_ean,
           l.status AS link_status, l.created_at AS linked_at,
           p.source_type, p.aftercool_product_id, p.aftercool_sku, p.factory_id,
           p.factory_name, p.original_title,
           COALESCE(
             CASE WHEN jsonb_typeof(revision.content_json->'images') = 'array'
                  THEN NULLIF(revision.content_json->'images'->0->>'url', '') END,
             p.image_url
           ) AS image_url,
           op.id AS operation_id, op.attempt_number, op.status AS operation_status,
           op.target_sku, op.submission_state, op.online_observation_id, op.current_revision_number,
           revision.fulfillment_snapshot_json,
           availability.actions AS availability_actions,
           availability_errors.actions AS availability_errors,
           pe.id AS processing_event_id, pe.entity_type AS processing_event_type,
           pe.entity_id AS processing_entity_id, pe.metadata_json AS processing_metadata,
           pe.created_at AS processing_created_at,
           online.requested_sku AS online_requested_sku,
           online.response_sku AS online_response_sku,
           online.market_place_status AS online_marketplace_status,
           online.observed_at AS online_observed_at,
           latest.id AS latest_observation_id,
           latest.requested_sku AS latest_requested_sku,
           latest.response_sku AS latest_response_sku,
           latest.market_place_status AS latest_marketplace_status,
           latest.observed_at AS latest_observed_at
    FROM ean_links l
    JOIN source_products p ON p.id = l.source_product_id
      AND p.account = l.account AND p.data_scope = 'PRODUCTION'
    LEFT JOIN restart_operations op ON op.id = l.operation_id
      AND op.account = l.account AND op.data_scope = 'PRODUCTION'
    LEFT JOIN restart_events pe ON pe.id = op.processing_evidence_event_id
      AND pe.account = op.account AND pe.data_scope = 'PRODUCTION'
    LEFT JOIN otto_status_observations online ON online.id = op.online_observation_id
      AND online.account = op.account AND online.data_scope = 'PRODUCTION'
    LEFT JOIN operation_revisions revision ON revision.operation_id=op.id AND revision.account=op.account
      AND revision.data_scope='PRODUCTION' AND revision.revision_number=op.current_revision_number
    LEFT JOIN LATERAL (
      SELECT jsonb_object_agg(latest.action, jsonb_build_object(
        'status', latest.status, 'attemptCount', latest.attempt_count + (
          SELECT COUNT(*) FROM restart_events retry_event
          WHERE retry_event.account=op.account AND retry_event.data_scope='PRODUCTION'
            AND retry_event.entity_type='RESTART_OPERATION' AND retry_event.entity_id=op.id::text
            AND retry_event.event_type='OTTO_AVAILABILITY_RETRY_REQUESTED'
            AND retry_event.metadata_json->>'action'=latest.action
            AND COALESCE(retry_event.metadata_json->>'previousStatus','') <> 'NOT_QUEUED'
        ),
        'httpStatus', latest.http_status, 'errorCode', latest.error_code,
        'manualResolution', (
          SELECT resolution_event.metadata_json->>'resolution'
          FROM restart_events resolution_event
          WHERE resolution_event.account=op.account AND resolution_event.data_scope='PRODUCTION'
            AND resolution_event.entity_type='RESTART_OPERATION' AND resolution_event.entity_id=op.id::text
            AND resolution_event.event_type='OTTO_AVAILABILITY_MANUALLY_RESOLVED'
            AND resolution_event.metadata_json->>'action'=latest.action
            AND latest.completed_at IS NOT NULL
            AND resolution_event.created_at >= latest.completed_at
          ORDER BY resolution_event.created_at DESC,resolution_event.id DESC LIMIT 1
        ),
        'lastAttemptAt', COALESCE(latest.completed_at,latest.dispatch_started_at,latest.created_at)
      )) AS actions
      FROM (
        SELECT DISTINCT ON (a.action) a.action,a.status,a.http_status,a.error_code,
               COUNT(*) OVER (PARTITION BY a.action) AS attempt_count,
               a.created_at,a.dispatch_started_at,a.completed_at
        FROM availability_attempts a
        WHERE a.operation_id=op.id AND a.account=op.account AND a.data_scope='PRODUCTION'
        ORDER BY a.action,a.created_at DESC,a.id DESC
      ) latest
    ) availability ON TRUE
    LEFT JOIN LATERAL (
      SELECT jsonb_object_agg(failure.action, jsonb_build_object(
        'status','NEEDS_REVIEW','attemptCount',0,'httpStatus',NULL::integer,
        'errorCode',failure.error_code,'lastAttemptAt',failure.created_at
      )) AS actions
      FROM (
        SELECT DISTINCT ON (event.metadata_json->>'action')
               event.metadata_json->>'action' AS action,
               event.metadata_json->>'errorCode' AS error_code,
               event.created_at
        FROM restart_events event
        WHERE event.account=op.account AND event.data_scope='PRODUCTION'
          AND event.entity_type='RESTART_OPERATION' AND event.entity_id=op.id::text
          AND event.event_type='OTTO_AVAILABILITY_NEEDS_REVIEW'
          AND event.metadata_json->>'action' IN ('QUANTITY','DELIVERY_INFORMATION')
          AND NOT EXISTS (
            SELECT 1 FROM availability_attempts attempt
            WHERE attempt.operation_id=op.id AND attempt.account=op.account
              AND attempt.data_scope='PRODUCTION' AND attempt.action=event.metadata_json->>'action'
          )
        ORDER BY event.metadata_json->>'action',event.created_at DESC,event.id DESC
      ) failure
    ) availability_errors ON TRUE
    LEFT JOIN LATERAL (
      SELECT observation.id, observation.requested_sku, observation.response_sku,
             observation.market_place_status, observation.observed_at
      FROM otto_status_observations observation
      WHERE observation.account = op.account
        AND observation.data_scope = 'PRODUCTION'
        AND observation.requested_sku = op.target_sku
      ORDER BY observation.observed_at DESC, observation.id DESC
      LIMIT 1
    ) latest ON TRUE
"""


def _link_query(conn: Connection, account: str, source_ids: list[str], *, per_product_limit: int | None = None) -> list[Any]:
    if not source_ids:
        return []
    if per_product_limit is None:
        statement = _LINK_SELECT + """
            WHERE l.account = :account AND l.data_scope = 'PRODUCTION'
              AND l.source_product_id = ANY(:source_ids)
            ORDER BY l.source_product_id, l.created_at DESC, l.id DESC
        """
    else:
        statement = """
            WITH ranked_links AS (
              SELECT id, row_number() OVER (
                PARTITION BY source_product_id ORDER BY created_at DESC, id DESC
              ) AS row_number
              FROM ean_links
              WHERE account = :account AND data_scope = 'PRODUCTION'
                AND source_product_id = ANY(:source_ids)
            )
        """ + _LINK_SELECT + """
            JOIN ranked_links ranked ON ranked.id = l.id
            WHERE l.account = :account AND l.data_scope = 'PRODUCTION'
              AND l.source_product_id = ANY(:source_ids)
              AND ranked.row_number <= :per_product_limit
            ORDER BY l.source_product_id, l.created_at DESC, l.id DESC
        """
    params: dict[str, Any] = {"account": account, "source_ids": source_ids}
    if per_product_limit is not None:
        params["per_product_limit"] = per_product_limit
    return list(conn.execute(text(statement), params).mappings().all())


def list_links(
    conn: Connection,
    *,
    account: str,
    query: str | None = None,
    status: str | None = None,
    factory_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    _require_page(page, page_size)
    conditions = [
        "l.account = :account",
        "l.data_scope = 'PRODUCTION'",
        "p.account = l.account",
        "p.data_scope = 'PRODUCTION'",
    ]
    params: dict[str, Any] = {"account": account}
    if status:
        normalized = status.strip().upper()
        if normalized not in {"ACTIVE", "DEACTIVATED", "PENDING_PUBLISH", "PUBLISHED"}:
            raise LibraryQueryError("VALIDATION_ERROR", "Unsupported Library link status filter.")
        conditions.append("l.status = :status")
        params["status"] = normalized
    if factory_id and factory_id.strip():
        conditions.append("p.factory_id = :factory_id")
        params["factory_id"] = factory_id.strip()
    if query and query.strip():
        conditions.append(
            "(p.original_title ILIKE :query OR p.aftercool_product_id ILIKE :query "
            "OR p.aftercool_sku ILIKE :query OR p.factory_name ILIKE :query "
            "OR l.source_ean ILIKE :query OR l.new_ean ILIKE :query "
            "OR EXISTS (SELECT 1 FROM restart_operations search_op WHERE search_op.id=l.operation_id "
            "AND search_op.account=l.account AND search_op.data_scope='PRODUCTION' "
            "AND (search_op.target_sku ILIKE :query OR search_op.id::text ILIKE :query)) "
            "OR p.id::text ILIKE :query OR p.factory_id::text ILIKE :query)"
        )
        params["query"] = f"%{query.strip()}%"
    where_clause = " AND ".join(conditions)
    total = int(conn.execute(
        text(f"SELECT COUNT(*) FROM ean_links l JOIN source_products p ON p.id=l.source_product_id WHERE {where_clause}"),
        params,
    ).scalar_one())
    link_ids = [
        str(value)
        for value in conn.execute(
            text(
                f"SELECT l.id FROM ean_links l JOIN source_products p ON p.id=l.source_product_id "
                f"WHERE {where_clause} ORDER BY l.created_at DESC,l.id DESC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": page_size, "offset": (page - 1) * page_size},
        ).scalars().all()
    ]
    if link_ids:
        rows = conn.execute(
            text(_LINK_SELECT + """
                WHERE l.account=:account AND l.data_scope='PRODUCTION' AND l.id=ANY(:link_ids)
                ORDER BY l.created_at DESC,l.id DESC
            """),
            {"account": account, "link_ids": link_ids},
        ).mappings().all()
    else:
        rows = []
    items = [_link_item(row) for row in rows]
    available = int(conn.execute(text(
        "SELECT COUNT(*) FROM ean_pool WHERE account=:account AND data_scope='PRODUCTION' AND status='AVAILABLE'"
    ), {"account": account}).scalar_one())
    pool_total = int(conn.execute(text(
        "SELECT COUNT(*) FROM ean_pool WHERE account=:account AND data_scope='PRODUCTION'"
    ), {"account": account}).scalar_one())
    event_total = int(conn.execute(text(
        "SELECT COUNT(*) FROM restart_events WHERE account=:account AND data_scope='PRODUCTION'"
    ), {"account": account}).scalar_one())
    return {
        "items": items,
        "page": _page(page, page_size, total),
        "summary": {
            "linkedRecords": total,
            "confirmedOnlineOnPage": sum(len(item["confirmedOnlineIdentifiers"]) for item in items),
            "poolAvailable": available,
            "poolTotal": pool_total,
            "eventsCount": event_total,
        },
    }


def lookup_aftercool(
    conn: Connection,
    *,
    account: str,
    aftercool_ean: str | None = None,
    source_ean: str | None = None,
    aftercool_product_id: str | None = None,
    factory_id: str | None = None,
    new_ean: str | None = None,
    sku: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    _require_page(page, page_size, maximum=MAX_AFTERCOOL_PAGE_SIZE)
    selectors = {
        "source_ean": aftercool_ean or source_ean,
        "aftercool_product_id": aftercool_product_id,
        "factory_id": factory_id,
        "new_ean": new_ean,
        "sku": sku,
    }
    selectors = {key: value.strip() for key, value in selectors.items() if isinstance(value, str) and value.strip()}
    if not selectors:
        raise LibraryQueryError("VALIDATION_ERROR", "At least one exact Library lookup selector is required.")

    conditions = ["p.account=:account", "p.data_scope='PRODUCTION'", "l.account=p.account", "l.data_scope='PRODUCTION'"]
    params: dict[str, Any] = {"account": account}
    if "source_ean" in selectors:
        conditions.append("(p.aftercool_ean=:source_ean OR l.source_ean=:source_ean)")
        params["source_ean"] = selectors["source_ean"]
    if "aftercool_product_id" in selectors:
        conditions.append("p.aftercool_product_id=:aftercool_product_id")
        params["aftercool_product_id"] = selectors["aftercool_product_id"]
    if "factory_id" in selectors:
        conditions.append("p.factory_id=:factory_id")
        params["factory_id"] = selectors["factory_id"]
    if "new_ean" in selectors:
        conditions.append("l.new_ean=:new_ean")
        params["new_ean"] = selectors["new_ean"]
    if "sku" in selectors:
        conditions.append("(p.aftercool_sku=:sku OR EXISTS (SELECT 1 FROM restart_operations sku_op "
                           "WHERE sku_op.source_product_id=p.id AND sku_op.account=p.account "
                           "AND sku_op.data_scope='PRODUCTION' AND sku_op.target_sku=:sku))")
        params["sku"] = selectors["sku"]
    where_clause = " AND ".join(conditions)
    total = int(conn.execute(
        text(f"SELECT COUNT(DISTINCT p.id) FROM source_products p JOIN ean_links l ON l.source_product_id=p.id WHERE {where_clause}"),
        params,
    ).scalar_one())
    products = conn.execute(
        text(f"""
            SELECT p.id,p.account,p.source_type,p.aftercool_product_id,p.aftercool_sku,p.aftercool_ean,
                   p.factory_id,p.factory_name,p.original_title
            FROM source_products p JOIN ean_links l ON l.source_product_id=p.id
            WHERE {where_clause}
            GROUP BY p.id,p.account,p.source_type,p.aftercool_product_id,p.aftercool_sku,p.aftercool_ean,
                     p.factory_id,p.factory_name,p.original_title
            ORDER BY p.factory_id NULLS LAST,p.aftercool_product_id NULLS LAST,p.id
            LIMIT :limit OFFSET :offset
        """),
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    ).mappings().all()
    source_ids = [str(row["id"]) for row in products]
    rows = _link_query(conn, account, source_ids, per_product_limit=MAX_LINEAGE_PER_PRODUCT)
    counts = {}
    if source_ids:
        for row in conn.execute(
            text("""SELECT source_product_id, COUNT(*) AS total FROM ean_links
                   WHERE account=:account AND data_scope='PRODUCTION' AND source_product_id=ANY(:source_ids)
                   GROUP BY source_product_id"""),
            {"account": account, "source_ids": source_ids},
        ).mappings():
            counts[str(row["source_product_id"])] = int(row["total"])
    links_by_source: dict[str, list[dict[str, Any]]] = {source_id: [] for source_id in source_ids}
    for row in rows:
        source_id = str(row["source_product_id"])
        links_by_source.setdefault(source_id, []).append(_link_item(row))

    operations_by_source: dict[str, list[dict[str, Any]]] = {source_id: [] for source_id in source_ids}
    operation_ids: list[str] = []
    if source_ids:
        operation_rows = conn.execute(text("""
            WITH ranked AS (
              SELECT op.*, COUNT(*) OVER (PARTITION BY op.source_product_id) AS attempt_count,
                     ROW_NUMBER() OVER (PARTITION BY op.source_product_id
                       ORDER BY op.attempt_number DESC NULLS LAST,op.created_at DESC,op.id DESC) AS rn
              FROM restart_operations op
              WHERE op.account=:account AND op.data_scope='PRODUCTION' AND op.source_product_id=ANY(:source_ids)
            )
            SELECT ranked.*, pe.event_type AS processing_event_type, pe.entity_id AS processing_entity_id,
                   pe.metadata_json AS processing_metadata, pe.created_at AS processing_created_at,
                   online.requested_sku AS online_requested_sku, online.response_sku AS online_response_sku,
                   online.market_place_status AS online_marketplace_status, online.observed_at AS online_observed_at
            FROM ranked
            LEFT JOIN restart_events pe ON pe.id=ranked.processing_evidence_event_id
              AND pe.account=ranked.account AND pe.data_scope='PRODUCTION'
            LEFT JOIN otto_status_observations online ON online.id=ranked.online_observation_id
              AND online.account=ranked.account AND online.data_scope='PRODUCTION'
            WHERE ranked.rn <= :limit
            ORDER BY ranked.source_product_id,ranked.rn
        """), {"account": account, "source_ids": source_ids, "limit": MAX_OPERATION_HISTORY_PER_PRODUCT}).mappings().all()
        for row in operation_rows:
            source_id, op_id = str(row["source_product_id"]), str(row["id"])
            operation_ids.append(op_id)
            processed = bool(
                row["submission_state"] == "CONFIRMED"
                and row["processing_event_type"] == "OTTO_PROCESSING_RESULT"
                and str(row["processing_entity_id"]) == op_id
                and _json_object(row["processing_metadata"]).get("targetSku") == row["target_sku"]
                and _json_object(row["processing_metadata"]).get("result") == "SUCCEEDED"
            )
            online = bool(
                processed and row["status"] == "PUBLISHED"
                and row["online_requested_sku"] == row["target_sku"]
                and row["online_response_sku"] == row["target_sku"]
                and row["online_marketplace_status"] == "ONLINE"
                and row["online_observed_at"] is not None
                and row["processing_created_at"] is not None
                and row["online_observed_at"] >= row["processing_created_at"]
            )
            operations_by_source.setdefault(source_id, []).append({
                "operationId": op_id,
                "attemptNumber": row["attempt_number"],
                "period": row["period"],
                "status": row["status"],
                "targetSku": row["target_sku"],
                "newEan": row["new_ean"],
                "createdAt": _iso(row["created_at"]),
                "updatedAt": _iso(row["updated_at"]),
                "processingConfirmed": processed,
                "onlineConfirmed": online,
                "onlineCheckedAt": _iso(row["online_observed_at"]) if online else None,
            })

    events_by_source = _load_event_history(conn, account, source_ids, operation_ids, rows)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    items = []
    for product in products:
        source_id = str(product["id"])
        links = links_by_source.get(source_id, [])
        confirmed = [identifier for link in links for identifier in link["confirmedOnlineIdentifiers"]]
        latest_checks = [
            link["latestMarketplaceObservation"]["checkedAt"]
            for link in links if link.get("latestMarketplaceObservation")
        ]
        items.append({
            "sourceProduct": {
                "id": source_id,
                "sourceType": product["source_type"],
                "aftercoolProductId": product["aftercool_product_id"],
                "sku": product["aftercool_sku"],
                "ean": product["aftercool_ean"],
                "factoryId": product["factory_id"],
                "factoryName": product["factory_name"],
                "title": product["original_title"],
            },
            "linkedIdentifiers": [link["linkedIdentifier"] for link in links],
            "confirmedOnlineIdentifiers": confirmed,
            "latestMarketplaceObservations": [
                link["latestMarketplaceObservation"] for link in links if link.get("latestMarketplaceObservation")
            ],
            "history": operations_by_source.get(source_id, []),
            "events": events_by_source.get(source_id, []),
            "historyTruncated": (
                counts.get(source_id, 0) > MAX_LINEAGE_PER_PRODUCT
                or len(operations_by_source.get(source_id, [])) >= MAX_OPERATION_HISTORY_PER_PRODUCT
                or len(events_by_source.get(source_id, [])) >= MAX_EVENT_HISTORY_PER_PRODUCT
            ),
            "checkedAt": max(latest_checks) if latest_checks else None,
            "lookupReadAt": now,
        })
    return {
        "account": account,
        "dataScope": "PRODUCTION",
        "page": _page(page, page_size, total),
        "checkedAt": max((item["checkedAt"] for item in items if item["checkedAt"]), default=None),
        "lookupReadAt": now,
        "items": items,
    }


def _load_event_history(
    conn: Connection,
    account: str,
    source_ids: list[str],
    operation_ids: list[str],
    link_rows: list[Any],
) -> dict[str, list[dict[str, Any]]]:
    if not source_ids:
        return {}
    link_ids = [str(row["link_id"]) for row in link_rows]
    events = conn.execute(text("""
        WITH owned AS (
          SELECT ev.id, ev.account, ev.data_scope, ev.event_type, ev.entity_type, ev.entity_id,
                 ev.summary, ev.metadata_json, ev.created_at,
                 COALESCE(source.id, operation.source_product_id, link.source_product_id) AS source_product_id
          FROM restart_events ev
          LEFT JOIN source_products source ON ev.entity_type='SOURCE_PRODUCT' AND ev.entity_id=source.id::text
            AND source.account=ev.account AND source.data_scope='PRODUCTION'
          LEFT JOIN restart_operations operation ON ev.entity_type IN ('RESTART_OPERATION','OTTO_PROCESSING_RESULT')
            AND ev.entity_id=operation.id::text
            AND operation.account=ev.account AND operation.data_scope='PRODUCTION'
          LEFT JOIN ean_links link ON ev.entity_type='EAN_LINK' AND ev.entity_id=link.id::text
            AND link.account=ev.account AND link.data_scope='PRODUCTION'
          WHERE ev.account=:account AND ev.data_scope='PRODUCTION'
            AND (source.id=ANY(:source_ids) OR operation.id=ANY(:operation_ids) OR link.id=ANY(:link_ids))
        ), ranked AS (
          SELECT owned.*, COUNT(*) OVER (PARTITION BY source_product_id) AS event_count,
                 ROW_NUMBER() OVER (PARTITION BY source_product_id ORDER BY created_at DESC,id DESC) AS rn
          FROM owned
        )
        SELECT * FROM ranked WHERE rn <= :limit ORDER BY source_product_id,created_at DESC,id DESC
    """), {
        "account": account,
        "source_ids": source_ids,
        "operation_ids": operation_ids or ["00000000-0000-0000-0000-000000000000"],
        "link_ids": link_ids or ["00000000-0000-0000-0000-000000000000"],
        "limit": MAX_EVENT_HISTORY_PER_PRODUCT,
    }).mappings().all()
    result: dict[str, list[dict[str, Any]]] = {source_id: [] for source_id in source_ids}
    for event in events:
        owner = str(event["source_product_id"])
        result.setdefault(owner, []).append({
            "id": str(event["id"]),
            "eventType": event["event_type"],
            "entityType": event["entity_type"],
            "entityId": event["entity_id"],
            "summary": event["summary"],
            "metadata": _json_object(event["metadata_json"]),
            "createdAt": _iso(event["created_at"]),
        })
    return result
