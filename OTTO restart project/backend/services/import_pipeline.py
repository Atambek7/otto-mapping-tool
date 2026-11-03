"""Bulk Import Pipeline for OTTO Restart 3/12M.

Parses TXT/CSV files containing EANs or SKUs:
1. Normalizes and validates EAN (GS1 check-digit) and SKU identifiers.
2. Checks against existing records in `source_products` to prevent duplicate insertion.
3. Classifies period into 3M vs 12M via OttoStatusService (using confirmed 602016 codes).
4. Routes items via AftercoolLookupService:
   - Found in Aftercool -> 'AFTERBUY' (assigned to factory_id)
   - Clean Not Found in Aftercool -> 'OTTO_DIRECT'
5. Supports `dry_run=True` for safe execution preview without DB mutation.
6. When committing (`dry_run=False`):
   - Logs entry in `import_runs`.
   - Inserts new items as `is_new_candidate = TRUE`.
   - If a factory was previously COMPLETE, automatically switches its status to NOT_COMPLETE
     while PRESERVING `last_completed_at` and setting `new_candidates_count`.

Operational rule:
- Zero external network requests made ("пока не обращайся сам в никакой апи").
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from typing import Any, List, Optional
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from services.aftercool_lookup_service import AftercoolLookupService
from services.factory_service import FactoryService
from services.otto_status_service import OttoStatusService

logger = logging.getLogger("import_pipeline")

EAN_PATTERN = re.compile(r"^\d{8,14}$")


def normalize_identifier(raw: str) -> str:
    """Strips BOM, whitespace, quotes, and control chars."""
    if not raw:
        return ""
    val = raw.strip().replace("\ufeff", "").replace('"', "").replace("'", "")
    # Remove leading/trailing non-alphanumeric except hyphen
    return val.strip()


def validate_ean13(ean: str) -> bool:
    """Verifies GS1 check digit for 13-digit EANs."""
    if not EAN_PATTERN.fullmatch(ean) or len(ean) != 13:
        return False
    digits = [int(char) for char in ean]
    check_digit = digits.pop()
    weighted_sum = sum(digit * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(digits)))
    return (10 - weighted_sum % 10) % 10 == check_digit


class ImportPipeline:
    """Executes bulk import, classification, routing, and factory state maintenance."""

    def __init__(
        self,
        otto_service: Optional[OttoStatusService] = None,
        aftercool_service: Optional[AftercoolLookupService] = None,
    ) -> None:
        self.otto_service = otto_service or OttoStatusService(live_enabled=False)
        self.aftercool_service = aftercool_service or AftercoolLookupService(live_enabled=False)

    def parse_raw_content(self, content_str: str) -> List[dict[str, Any]]:
        """Parses plain text lines or CSV/TSV table into row dictionaries."""
        lines = [line.strip() for line in content_str.splitlines() if line.strip()]
        if not lines:
            return []

        # Check if first line looks like a CSV header
        first_line = lines[0].lower()
        delimiter = None
        for delim in [";", ",", "\t", "|"]:
            if delim in first_line:
                delimiter = delim
                break

        parsed_items: List[dict[str, Any]] = []

        if delimiter and any(kw in first_line for kw in ["ean", "sku", "artikel", "produkt", "factory"]):
            # Process as CSV
            reader = csv.DictReader(io.StringIO(content_str), delimiter=delimiter)
            for row in reader:
                cleaned = {k.strip().lower(): v.strip() for k, v in row.items() if k and v}
                # Find identifier
                raw_id = (
                    cleaned.get("ean")
                    or cleaned.get("sku")
                    or cleaned.get("artikelnr")
                    or cleaned.get("identifier")
                    or cleaned.get("id")
                    or ""
                )
                ident = normalize_identifier(raw_id)
                if not ident:
                    continue

                period_hint = cleaned.get("period") or cleaned.get("zeitraum") or cleaned.get("gruppe")
                if period_hint:
                    period_hint = period_hint.upper()

                factory_hint = cleaned.get("factory_id") or cleaned.get("fabriknr") or cleaned.get("factory")
                title_hint = cleaned.get("title") or cleaned.get("titel") or cleaned.get("name")
                price_hint = cleaned.get("price") or cleaned.get("preis") or cleaned.get("startpreis")

                parsed_items.append({
                    "raw_identifier": ident,
                    "period_hint": period_hint if period_hint in ("3M", "12M") else None,
                    "factory_hint": factory_hint,
                    "title_hint": title_hint,
                    "price_hint": price_hint,
                })
        else:
            # Process as plain list of identifiers (one per line)
            for line in lines:
                parts = re.split(r"[\s,;\t|]+", line)
                if not parts:
                    continue
                raw_id = parts[0]
                ident = normalize_identifier(raw_id)
                if not ident:
                    continue

                period_hint = None
                if len(parts) > 1 and parts[1].upper() in ("3M", "12M"):
                    period_hint = parts[1].upper()

                parsed_items.append({
                    "raw_identifier": ident,
                    "period_hint": period_hint,
                    "factory_hint": None,
                    "title_hint": None,
                    "price_hint": None,
                })

        return parsed_items

    def execute_import(
        self,
        conn: Connection,
        account: str,
        content_str: str,
        filename: str = "manual_import.txt",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Main pipeline execution."""
        parsed_rows = self.parse_raw_content(content_str)
        if not parsed_rows:
            return {
                "success": False,
                "error": "Файл не содержит распознаваемых идентификаторов (EAN или SKU).",
                "totalRows": 0,
            }

        # 1. Fetch existing identifiers in source_products for this account
        existing_res = conn.execute(
            text(
                """
                SELECT aftercool_ean, aftercool_sku
                FROM source_products
                WHERE account = :account;
                """
            ),
            {"account": account},
        ).fetchall()

        existing_eans = {r[0] for r in existing_res if r[0]}
        existing_skus = {r[1] for r in existing_res if r[1]}

        processed: List[dict[str, Any]] = []
        skipped_existing: List[str] = []
        invalid_format: List[str] = []
        afterbuy_items: List[dict[str, Any]] = []
        direct_otto_items: List[dict[str, Any]] = []
        seen_in_batch: set[str] = set()

        for row in parsed_rows:
            ident = row["raw_identifier"]
            if ident in seen_in_batch:
                continue
            seen_in_batch.add(ident)

            # Check format
            is_ean = bool(EAN_PATTERN.fullmatch(ident))
            if is_ean and len(ident) == 13 and not validate_ean13(ident):
                invalid_format.append(f"{ident} (неверная контрольная цифра GS1)")
                continue

            # Check duplication
            if ident in existing_eans or ident in existing_skus:
                skipped_existing.append(ident)
                continue

            # Classify period (3M vs 12M)
            status_info = self.otto_service.get_status(account, ident, hint_period=row["period_hint"])
            period = status_info.get("period") or "3M"

            # Route (Afterbuy vs Direct OTTO)
            lookup = self.aftercool_service.lookup_product(account, ident, factory_hint=row["factory_hint"])

            item_data = {
                "identifier": ident,
                "is_ean": is_ean,
                "period": period,
                "period_code": status_info.get("code"),
                "source_type": lookup["source_type"],
                "factory_id": lookup.get("factory_id"),
                "factory_name": lookup.get("factory_name"),
                "aftercool_product_id": lookup.get("aftercool_product_id"),
                "title": row["title_hint"] or lookup.get("title", f"Möbel Artikel {ident[-6:]}"),
                "price_cents": lookup.get("price_cents", 99900),
                "category": lookup.get("category", "Möbel"),
            }

            if lookup["source_type"] == "AFTERBUY":
                afterbuy_items.append(item_data)
            else:
                direct_otto_items.append(item_data)

            processed.append(item_data)

        summary = {
            "account": account,
            "filename": filename,
            "dryRun": dry_run,
            "totalParsed": len(parsed_rows),
            "uniqueValidCount": len(processed),
            "skippedExistingCount": len(skipped_existing),
            "invalidCount": len(invalid_format),
            "afterbuyCount": len(afterbuy_items),
            "directOttoCount": len(direct_otto_items),
            "period3mCount": sum(1 for it in processed if it["period"] == "3M"),
            "period12mCount": sum(1 for it in processed if it["period"] == "12M"),
            "skippedExisting": skipped_existing[:50],
            "invalidFormat": invalid_format[:20],
            "samplePreview": processed[:10],
        }

        # If dry-run, stop before database persistence
        if dry_run:
            return {
                "success": True,
                "dryRun": True,
                "summary": summary,
            }

        # 2. Commit persistence when dry_run=False
        run_id = str(uuid4())

        # Insert into import_runs
        conn.execute(
            text(
                """
                INSERT INTO import_runs (
                    id, account, source_filename, total_rows, processed_count,
                    afterbuy_count, direct_otto_count, skipped_count, error_count,
                    status, summary_json, created_at
                ) VALUES (
                    :id, :account, :filename, :total, :processed,
                    :afterbuy, :direct, :skipped, :error,
                    'COMPLETED', CAST(:summary_json AS jsonb), CURRENT_TIMESTAMP
                );
                """
            ),
            {
                "id": run_id,
                "account": account,
                "filename": filename,
                "total": len(parsed_rows),
                "processed": len(processed),
                "afterbuy": len(afterbuy_items),
                "direct": len(direct_otto_items),
                "skipped": len(skipped_existing),
                "error": len(invalid_format),
                "summary_json": json.dumps(summary),
            },
        )

        affected_factory_ids: set[str] = set()

        # Insert items into source_products
        for it in processed:
            prod_id = str(uuid4())
            ean_val = it["identifier"] if it["is_ean"] else None
            sku_val = it["identifier"] if not it["is_ean"] else None
            factory_id_val = it["factory_id"]
            if factory_id_val:
                affected_factory_ids.add(factory_id_val)

            title_len = len(it["title"])
            initial_status = "ERROR" if title_len > 70 or not it["category"] else "READY"

            conn.execute(
                text(
                    """
                    INSERT INTO source_products (
                        id, account, source_type, aftercool_product_id, aftercool_sku,
                        aftercool_ean, factory_id, factory_name, original_title,
                        original_price_cents, category, import_run_id, is_new_candidate,
                        current_status, period, last_checked_at, created_at, updated_at
                    ) VALUES (
                        :id, :account, :source_type, :aftercool_prod_id, :sku,
                        :ean, :factory_id, :factory_name, :title,
                        :price, :category, :import_run_id, TRUE,
                        :status, :period, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    );
                    """
                ),
                {
                    "id": prod_id,
                    "account": account,
                    "source_type": it["source_type"],
                    "aftercool_prod_id": it["aftercool_product_id"],
                    "sku": sku_val,
                    "ean": ean_val,
                    "factory_id": factory_id_val,
                    "factory_name": it["factory_name"],
                    "title": it["title"],
                    "price": it["price_cents"],
                    "category": it["category"],
                    "import_run_id": run_id,
                    "status": initial_status,
                    "period": it["period"],
                },
            )

        # 3. Synchronize affected factories:
        # Guarantee factory existence in restart_factories and recalculate state
        for fid in affected_factory_ids:
            # Insert factory if missing
            fname = next((it["factory_name"] for it in afterbuy_items if it["factory_id"] == fid), fid)
            conn.execute(
                text(
                    """
                    INSERT INTO restart_factories (account, id, name, status, created_at, updated_at)
                    VALUES (:account, :id, :name, 'NOT_COMPLETE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT (account, id) DO NOTHING;
                    """
                ),
                {"account": account, "id": fid, "name": fname},
            )
            # Recompute factory status (transitions COMPLETE -> NOT_COMPLETE if new candidates exist)
            FactoryService.sync_factory_status(conn, account, fid)

        summary["importRunId"] = run_id
        summary["affectedFactoriesCount"] = len(affected_factory_ids)

        return {
            "success": True,
            "dryRun": False,
            "summary": summary,
        }
