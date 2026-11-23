"""Shared, account-scoped publication evidence used by Library and Drip Feed."""

from __future__ import annotations


def publication_evidence_joins(*, operation_alias: str = "op", prefix: str = "publication") -> str:
    """Return joins for the exact processing event and latest verified marketplace state.

    Marketplace observations are authoritative only when the requested and returned
    SKU match, the HTTP call succeeded, the response was classifiable, and the
    endpoint supplied one consistent, recognized status. Account and scope are part
    of every lookup. Provider ``lastModified`` prevents a late older response from
    replacing a newer marketplace state; observation time breaks ties/fills gaps.
    """
    processing = f"{prefix}_processing"
    marketplace = f"{prefix}_marketplace"
    return f"""
    LEFT JOIN restart_events {processing}
      ON {processing}.id = {operation_alias}.processing_evidence_event_id
     AND {processing}.account = {operation_alias}.account
     AND {processing}.data_scope = {operation_alias}.data_scope
    LEFT JOIN LATERAL (
      SELECT observation.id,
             observation.requested_sku,
             observation.response_sku,
             observation.http_status,
             observation.classification,
             observation.observed_at,
             observation.last_modified,
             {_marketplace_status_sql('observation')} AS marketplace_status
      FROM otto_status_observations observation
      WHERE observation.account = {operation_alias}.account
        AND observation.data_scope = {operation_alias}.data_scope
        AND observation.requested_sku = {operation_alias}.target_sku
        AND observation.response_sku = {operation_alias}.target_sku
        AND observation.http_status = 200
        AND observation.classification <> 'UNCHECKABLE'
        AND {_marketplace_status_sql('observation')} IN
            ('PENDING','ONLINE','RESTRICTED','REJECTED','INACTIVE')
      ORDER BY COALESCE(observation.last_modified, observation.observed_at) DESC,
               observation.observed_at DESC, observation.id DESC
      LIMIT 1
    ) {marketplace} ON TRUE
    """


def _marketplace_status_sql(alias: str) -> str:
    """Use the endpoint status field, accepting old marketplaceStatus-only records."""
    return (
        f"CASE WHEN {alias}.status IS NOT NULL "
        f"AND {alias}.market_place_status IS NOT NULL "
        f"AND {alias}.status <> {alias}.market_place_status THEN NULL "
        f"ELSE COALESCE({alias}.market_place_status, {alias}.status) END"
    )


def processing_confirmed_sql(*, operation_alias: str = "op", event_alias: str = "publication_processing") -> str:
    return (
        f"({operation_alias}.submission_state = 'CONFIRMED' "
        f"AND {event_alias}.entity_type = 'OTTO_PROCESSING_RESULT' "
        f"AND {event_alias}.entity_id = {operation_alias}.id::text "
        f"AND {event_alias}.metadata_json->>'targetSku' = {operation_alias}.target_sku "
        f"AND {event_alias}.metadata_json->>'result' = 'SUCCEEDED')"
    )


def online_confirmed_sql(*, operation_alias: str = "op", marketplace_alias: str = "publication_marketplace") -> str:
    """A current exact-SKU ONLINE marketplace observation is the publication proof."""
    return (
        f"({operation_alias}.target_sku IS NOT NULL "
        f"AND {operation_alias}.target_sku = {operation_alias}.new_ean "
        f"AND {marketplace_alias}.requested_sku = {operation_alias}.target_sku "
        f"AND {marketplace_alias}.response_sku = {operation_alias}.target_sku "
        f"AND {marketplace_alias}.marketplace_status = 'ONLINE')"
    )


def operation_publication_state_sql(
    *, operation_alias: str = "op", processing_alias: str = "publication_processing",
    marketplace_alias: str = "publication_marketplace",
) -> str:
    """Shared operation state projection; historical errors never override live evidence."""
    processing = processing_confirmed_sql(operation_alias=operation_alias, event_alias=processing_alias)
    online = online_confirmed_sql(operation_alias=operation_alias, marketplace_alias=marketplace_alias)
    return f"""CASE
      WHEN {operation_alias}.id IS NULL THEN 'NOT_PREPARED'
      WHEN {online} THEN 'ONLINE_CONFIRMED'
      WHEN {processing} AND {operation_alias}.status = 'AWAITING_ONLINE' AND {operation_alias}.online_observation_id IS NULL THEN 'PROCESSING_CONFIRMED_AWAITING_ONLINE'
      WHEN {marketplace_alias}.marketplace_status IN ('RESTRICTED','REJECTED','INACTIVE') THEN 'NEEDS_REVIEW'
      WHEN {processing} THEN 'PROCESSING_CONFIRMED_AWAITING_ONLINE'
      WHEN {marketplace_alias}.marketplace_status = 'PENDING' THEN 'SUBMITTED_AWAITING_PROCESSING'
      WHEN {operation_alias}.status = 'PUBLISHED' THEN 'NEEDS_REVIEW'
      WHEN {operation_alias}.submission_state IN ('IN_FLIGHT','UNKNOWN') THEN 'SUBMITTED_AWAITING_PROCESSING'
      ELSE COALESCE({operation_alias}.status, {operation_alias}.submission_state, 'DRAFT')
    END"""


def drip_item_status_sql(
    *, item_alias: str = "item", operation_alias: str = "op",
    processing_alias: str = "publication_processing", marketplace_alias: str = "publication_marketplace",
) -> str:
    """Normalize the operational queue status using the same marketplace evidence."""
    processing = processing_confirmed_sql(operation_alias=operation_alias, event_alias=processing_alias)
    online = online_confirmed_sql(operation_alias=operation_alias, marketplace_alias=marketplace_alias)
    return f"""CASE
      WHEN {online} THEN 'PUBLISHED'
      WHEN {processing} AND {operation_alias}.status = 'AWAITING_ONLINE' AND {operation_alias}.online_observation_id IS NULL AND {item_alias}.status <> 'NEEDS_REVIEW' THEN 'AWAITING_ONLINE'
      WHEN {marketplace_alias}.marketplace_status IN ('RESTRICTED','REJECTED','INACTIVE') THEN 'NEEDS_REVIEW'
      WHEN {processing} THEN 'AWAITING_ONLINE'
      WHEN {marketplace_alias}.marketplace_status = 'PENDING' AND {item_alias}.status = 'PUBLISHED' THEN 'AWAITING_ONLINE'
      WHEN {item_alias}.status = 'PUBLISHED' THEN 'NEEDS_REVIEW'
      ELSE {item_alias}.status
    END"""
