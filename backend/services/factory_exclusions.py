"""Account- and factory-scoped product exclusions."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Iterable, Sequence

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

import models


PROTECTED_FACTORY_PAIRS = {
    ("JV", "504487", "504466"),
    ("XL", "483563", "483554"),
}
EXPECTED_MANIFEST_COUNTS = {"JV": 1057, "XL": 1058}
EXPECTED_SOURCE_PLAN_IDS = {"JV": 90, "XL": 92}


def is_protected_factory(factory_id: str | None) -> bool:
    clean = str(factory_id or "").strip()
    return bool(clean) and any(clean in (excluded, source) for _account, excluded, source in PROTECTED_FACTORY_PAIRS)


def require_protected_factory_account(account: str | None, factory_id: str | None) -> str:
    account_value = account.value if hasattr(account, "value") else account
    clean_account = str(account_value or "").upper().strip()
    if is_protected_factory(factory_id) and clean_account not in {"JV", "XL"}:
        raise HTTPException(status_code=409, detail={
            "code": "ACCOUNT_CONTEXT_REQUIRED",
            "message": "Для выбранной фабрики необходимо явно указать аккаунт JV или XL.",
        })
    if clean_account and clean_account not in {"JV", "XL"}:
        raise HTTPException(status_code=400, detail={
            "code": "INVALID_ACCOUNT",
            "message": "Аккаунт должен быть JV или XL.",
        })
    return clean_account or "JV"


def resolve_account_scope(
    db: Session, account: str | None, factory_id: str | None = None, eans: Iterable[str] = ()
) -> str:
    """Require explicit account context when factory/EAN policy needs it.

    Existing unprotected endpoints keep their historical JV default. An EAN in
    either account's active exclusion set must never be guessed into JV when an
    older client omits account.
    """
    account_value = account.value if hasattr(account, "value") else account
    clean_account = str(account_value or "").upper().strip()
    if clean_account and clean_account not in {"JV", "XL"}:
        raise HTTPException(status_code=400, detail={
            "code": "INVALID_ACCOUNT", "message": "Аккаунт должен быть JV или XL."
        })
    if not clean_account:
        if is_protected_factory(factory_id):
            raise HTTPException(status_code=409, detail={
                "code": "ACCOUNT_CONTEXT_REQUIRED",
                "message": "Для выбранной фабрики необходимо явно указать аккаунт JV или XL.",
            })
        cleaned = sorted({str(ean).strip() for ean in eans if ean and str(ean).strip()})
        if cleaned and db.query(models.FactoryProductExclusion.id).filter(
            models.FactoryProductExclusion.is_active.is_(True),
            models.FactoryProductExclusion.product_ean.in_(cleaned),
        ).first():
            raise HTTPException(status_code=409, detail={
                "code": "ACCOUNT_CONTEXT_REQUIRED",
                "message": "Для выбранных товаров необходимо явно указать аккаунт JV или XL.",
            })
        return "JV"
    return clean_account


def manifest_hash(eans: Iterable[str]) -> str:
    normalized = sorted({str(ean).strip() for ean in eans if str(ean).strip()})
    return hashlib.sha256(("\n".join(normalized) + "\n").encode("utf-8")).hexdigest()


def validate_manifest(manifest: dict, expected_hash: str | None = None) -> tuple[list[str], str]:
    account = str(manifest.get("account", "")).upper()
    excluded_factory = str(manifest.get("excluded_factory_id", "")).strip()
    source_factory = str(manifest.get("source_factory_id", "")).strip()
    if (account, excluded_factory, source_factory) not in PROTECTED_FACTORY_PAIRS:
        raise ValueError("Manifest does not match an approved account/factory pair")
    raw = manifest.get("eans")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Manifest must contain a non-empty EAN list")
    eans = [str(ean).strip() for ean in raw]
    if any(not ean or not ean.isascii() or not ean.isdigit() for ean in eans):
        raise ValueError("Manifest EANs must be non-empty ASCII digit strings")
    if len(set(eans)) != len(eans):
        raise ValueError("Manifest contains duplicate EANs")
    if len(eans) != EXPECTED_MANIFEST_COUNTS[account]:
        raise ValueError("Manifest count differs from the verified account snapshot")
    calculated = manifest_hash(eans)
    declared = str(manifest.get("sha256", ""))
    if declared and declared != calculated:
        raise ValueError("Manifest checksum does not match its EAN list")
    if expected_hash and expected_hash != calculated:
        raise ValueError("Manifest checksum differs from the explicitly expected checksum")
    if manifest.get("expected_count") is not None and int(manifest["expected_count"]) != len(eans):
        raise ValueError("Manifest count does not match its EAN list")
    if manifest.get("source_plan_id") != EXPECTED_SOURCE_PLAN_IDS[account]:
        raise ValueError("Manifest does not identify the reviewed source plan")
    if manifest.get("source_snapshot_complete") is not True:
        raise ValueError("A complete current source-factory snapshot is required")
    if int(manifest.get("source_unique_count", -1)) != len(eans):
        raise ValueError("Source snapshot unique count does not match the manifest")
    if manifest.get("source_snapshot_sha256") != calculated:
        raise ValueError("Source snapshot checksum does not match the manifest EAN set")
    if not str(manifest.get("source_evidence", "")).strip():
        raise ValueError("Source verification evidence is required")
    verified_at = str(manifest.get("source_verified_at_utc", ""))
    try:
        parsed_verified_at = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Source verification timestamp is invalid") from exc
    if parsed_verified_at.tzinfo is None:
        raise ValueError("Source verification timestamp must include a timezone")
    return sorted(eans), calculated


def excluded_eans(db: Session, account: str, factory_id: str) -> set[str]:
    account = str(account or "").upper().strip()
    factory_id = str(factory_id or "").strip()
    if not account or not factory_id:
        return set()
    if (account, factory_id) not in {
        (scope_account, excluded_factory)
        for scope_account, excluded_factory, _source_factory in PROTECTED_FACTORY_PAIRS
    }:
        return set()
    rows = db.query(models.FactoryProductExclusion.product_ean).filter_by(
        account_name=account, excluded_factory_id=factory_id, is_active=True
    ).all()
    return {row[0] for row in rows}


def filter_factory_products(db: Session, account: str, factory_id: str, items: Sequence[dict]) -> list[dict]:
    blocked = excluded_eans(db, account, factory_id)
    if not blocked:
        return list(items)
    return [item for item in items if _item_ean(item) not in blocked]


def filter_factory_mappings(db: Session, factory_id: str, mappings: Sequence[models.ProductMapping]) -> list[models.ProductMapping]:
    """Filter ORM mappings by each row's account without crossing account scope."""
    blocked_by_account: dict[str, set[str]] = {}
    result = []
    for mapping in mappings:
        account = str(mapping.account_name.value if hasattr(mapping.account_name, "value") else mapping.account_name or "JV").upper()
        if account not in blocked_by_account:
            blocked_by_account[account] = excluded_eans(db, account, factory_id)
        blocked = blocked_by_account[account]
        if mapping.product_ean not in blocked:
            result.append(mapping)
    return result


def filter_factory_eans(db: Session, account: str, factory_id: str, eans: Iterable[str]) -> set[str]:
    return {str(ean).strip() for ean in eans if str(ean).strip()} - excluded_eans(db, account, factory_id)


def require_factory_eans_allowed(db: Session, account: str, factory_id: str | None, eans: Iterable[str]) -> None:
    account = account.value if hasattr(account, "value") else account
    account = str(account or "").upper().strip()
    cleaned = {str(ean).strip() for ean in eans if ean and str(ean).strip()}
    if not cleaned:
        return
    if account not in {"JV", "XL"}:
        # Do not guess an account for an EAN in any active protected set.
        if db.query(models.FactoryProductExclusion.id).filter(
            models.FactoryProductExclusion.is_active.is_(True),
            models.FactoryProductExclusion.product_ean.in_(cleaned),
        ).first():
            raise HTTPException(status_code=409, detail={
                "code": "ACCOUNT_CONTEXT_REQUIRED",
                "message": "Для выбранных товаров необходимо явно указать аккаунт JV или XL.",
            })
        return
    rows = db.query(models.FactoryProductExclusion.product_ean, models.FactoryProductExclusion.source_factory_id).filter(
        models.FactoryProductExclusion.account_name == account,
        models.FactoryProductExclusion.is_active.is_(True),
        models.FactoryProductExclusion.product_ean.in_(cleaned),
    ).all()
    source_by_ean = {ean: source_factory for ean, source_factory in rows}
    if not source_by_ean:
        return
    if not factory_id:
        raise HTTPException(status_code=409, detail={"code": "FACTORY_CONTEXT_REQUIRED", "message": "Обновите карточку товара: для этого EAN требуется контекст фабрики."})
    target_factory = str(factory_id).strip()
    blocked = {ean for ean, source_factory in source_by_ean.items() if target_factory != source_factory}
    if blocked:
        code = "PRODUCT_EXCLUDED_FROM_FACTORY" if any(
            db.query(models.FactoryProductExclusion.id).filter_by(
                account_name=account, excluded_factory_id=target_factory, product_ean=ean, is_active=True
            ).first() for ean in blocked
        ) else "PRODUCT_FACTORY_SCOPE_MISMATCH"
        raise HTTPException(status_code=409, detail={
            "code": code,
            "message": "Часть товаров не относится к выбранной фабрике.",
            "product_eans": sorted(blocked),
        })


def lock_and_require_factory_eans_allowed(
    db: Session, account: str, factory_id: str | None, eans: Iterable[str]
) -> None:
    """Serialize a scoped mutation with manifest apply/rollback for existing mappings.

    Exclusion apply locks mappings before activating policy. Taking those same
    locks before a product mutation means the subsequent policy query observes
    whichever transaction won the race under PostgreSQL READ COMMITTED.
    """
    account = str(account or "").upper().strip()
    cleaned = sorted({str(ean).strip() for ean in eans if ean and str(ean).strip()})
    if cleaned:
        (db.query(models.ProductMapping.id)
         .filter(models.ProductMapping.account_name == account,
                 models.ProductMapping.product_ean.in_(cleaned))
         .order_by(models.ProductMapping.product_ean)
         .with_for_update().all())
    require_factory_eans_allowed(db, account, factory_id, cleaned)


def preview_batch(db: Session, manifest: dict, expected_hash: str | None = None) -> dict:
    eans, checksum = validate_manifest(manifest, expected_hash)
    account = manifest["account"].upper()
    excluded_factory = str(manifest["excluded_factory_id"])
    source_factory = str(manifest["source_factory_id"])
    mappings = db.query(models.ProductMapping.product_ean, models.ProductMapping.factory_id).filter(
        models.ProductMapping.account_name == account,
        models.ProductMapping.product_ean.in_(eans),
    ).all()
    by_ean = {ean: factory_id for ean, factory_id in mappings}
    missing = sorted(set(eans) - by_ean.keys())
    wrong_factory = sorted(ean for ean, factory in by_ean.items() if factory not in {excluded_factory, source_factory})
    already_source = sorted(ean for ean, factory in by_ean.items() if factory == source_factory)
    already_excluded = db.query(models.FactoryProductExclusion.product_ean).filter(
        models.FactoryProductExclusion.account_name == account,
        models.FactoryProductExclusion.excluded_factory_id == excluded_factory,
        models.FactoryProductExclusion.is_active.is_(True),
        models.FactoryProductExclusion.product_ean.in_(eans),
    ).all()
    batch_ids = {row[0] for row in db.query(models.FactoryProductExclusion.batch_id).filter(
        models.FactoryProductExclusion.account_name == account,
        models.FactoryProductExclusion.excluded_factory_id == excluded_factory,
        models.FactoryProductExclusion.product_ean.in_(eans),
    ).all()}
    result = {
        "account": account, "excluded_factory_id": excluded_factory,
        "source_factory_id": source_factory, "expected_count": len(eans),
        "sha256": checksum, "mapping_count": len(by_ean),
        "missing_mapping_count": len(missing), "wrong_factory_count": len(wrong_factory),
        "already_at_source_count": len(already_source), "already_excluded_count": len(already_excluded),
        "existing_batches": sorted(batch_ids),
        "source_verified_at_utc": manifest["source_verified_at_utc"],
        "source_snapshot_complete": manifest["source_snapshot_complete"],
    }
    if account == "XL":
        plan = db.query(models.PublishPlan).filter_by(id=EXPECTED_SOURCE_PLAN_IDS[account], account_name=account).first()
        queue_eans = set(db.query(models.PublishQueueItem.product_ean).filter_by(
            plan_id=EXPECTED_SOURCE_PLAN_IDS[account], account_name=account
        ).all()) if plan else set()
        queue_eans = {row[0] for row in queue_eans}
        processing = db.query(func.count(models.PublishQueueItem.id)).filter_by(
            plan_id=EXPECTED_SOURCE_PLAN_IDS[account], account_name=account,
            status=models.QueueItemStatus.PROCESSING,
        ).scalar() if plan else None
        result.update({
            "source_plan_status": str(plan.status.value if plan and hasattr(plan.status, "value") else plan.status) if plan else None,
            "source_plan_factory_id": plan.factory_id if plan else None,
            "source_plan_ean_count": len(queue_eans),
            "source_plan_matches_manifest": queue_eans == set(eans),
            "source_plan_processing_count": int(processing or 0),
        })
    return result


def apply_batch(db: Session, manifest: dict, expected_hash: str, actor: str) -> dict:
    eans, checksum = validate_manifest(manifest, expected_hash)
    account = manifest["account"].upper()
    excluded_factory = str(manifest["excluded_factory_id"])
    source_factory = str(manifest["source_factory_id"])
    batch_id = str(manifest.get("batch_id") or checksum[:24])
    reason = str(manifest.get("reason") or f"Remove duplicate membership in {excluded_factory}; canonical factory {source_factory}")
    if len(reason) > 1000 or not actor or len(actor) > 128:
        raise ValueError("A bounded reason and actor are required")

    try:
        prior_rows = db.query(models.FactoryProductExclusion).filter(
            models.FactoryProductExclusion.account_name == account,
            models.FactoryProductExclusion.excluded_factory_id == excluded_factory,
            models.FactoryProductExclusion.product_ean.in_(eans),
        ).with_for_update().all()
        prior_by_ean = {row.product_ean: row for row in prior_rows}
        active_rows = [row for row in prior_rows if row.is_active]
        if active_rows:
            prior_batch_ids = {row.batch_id for row in active_rows}
            prior_hashes = {row.source_manifest_hash for row in active_rows}
            if prior_batch_ids == {batch_id} and prior_hashes == {checksum} and len(active_rows) == len(eans):
                current_mappings = db.query(models.ProductMapping.product_ean, models.ProductMapping.factory_id).filter(
                    models.ProductMapping.account_name == account,
                    models.ProductMapping.product_ean.in_(eans),
                ).all()
                if len(current_mappings) != len(eans):
                    raise ValueError("Previously applied batch no longer has its complete mapping set")
                if account == "XL":
                    if any(factory_id != source_factory for _ean, factory_id in current_mappings):
                        raise ValueError("Previously corrected XL mappings changed; refusing to report an idempotent success")
                    audits = db.query(models.FactoryProductReassignment).filter_by(
                        batch_id=batch_id, account_name=account, status="APPLIED"
                    ).all()
                    reassigned_count = len(audits)
                    if any(audit.product_ean not in set(eans) or audit.target_factory_id != source_factory for audit in audits):
                        raise ValueError("XL reassignment audit does not match the applied manifest")
                else:
                    reassigned_count = 0
                return {"batch_id": batch_id, "sha256": checksum, "excluded_count": len(eans), "reassigned_count": reassigned_count, "already_applied": True}
            raise ValueError("Some EANs are already excluded by another or incomplete batch")

        # Lock all target mappings in deterministic order before any writes.
        mappings = db.query(models.ProductMapping).filter(
            models.ProductMapping.account_name == account,
            models.ProductMapping.product_ean.in_(eans),
        ).order_by(models.ProductMapping.product_ean).with_for_update().all()
        by_ean = {m.product_ean: m for m in mappings}
        if set(by_ean) != set(eans):
            raise ValueError("Some manifest EANs have no account-scoped product mapping")
        if account == "JV":
            conflicts = sorted(ean for ean, m in by_ean.items() if m.factory_id != source_factory)
        else:
            # A later source sync may already have restored canonical ownership.
            # Preserve those rows; only audit/reassign mappings still in NEW_OTTO_3.
            conflicts = sorted(ean for ean, m in by_ean.items() if m.factory_id not in {excluded_factory, source_factory})
        if conflicts:
            raise ValueError(f"Mappings have changed outside the approved factory pair: {conflicts[:10]}")

        if account == "XL":
            # Do not alter the live Drip plan here. The operator must pause it and
            # allow already claimed work to drain before invoking this command.
            plan = db.query(models.PublishPlan).filter_by(id=92, account_name="XL", factory_id=source_factory).with_for_update().first()
            if not plan or plan.status != models.PlanStatus.PAUSED:
                raise ValueError("XL plan 92 must already be paused before applying the ownership correction")
            queued = set(db.query(models.PublishQueueItem.product_ean).filter_by(plan_id=92, account_name="XL").all())
            queued = {row[0] for row in queued}
            if queued != set(eans):
                raise ValueError("XL plan 92 no longer matches this manifest")
            processing = db.query(func.count(models.PublishQueueItem.id)).filter_by(plan_id=92, account_name="XL", status=models.QueueItemStatus.PROCESSING).scalar()
            if processing:
                raise ValueError("XL plan 92 still has claimed work; wait for it to drain")

        now = datetime.now(timezone.utc)
        source_verified_at = datetime.fromisoformat(str(manifest["source_verified_at_utc"]).replace("Z", "+00:00"))
        for ean in eans:
            exclusion = prior_by_ean.get(ean)
            if exclusion is None:
                exclusion = models.FactoryProductExclusion(
                    account_name=account, excluded_factory_id=excluded_factory,
                    source_factory_id=source_factory, product_ean=ean,
                )
                db.add(exclusion)
            exclusion.source_factory_id = source_factory
            exclusion.batch_id = batch_id
            exclusion.source_manifest_hash = checksum
            exclusion.source_verified_at = source_verified_at
            exclusion.reason = reason
            exclusion.actor = actor
            exclusion.created_at = now
            exclusion.is_active = True
            exclusion.deactivated_at = None
            exclusion.deactivated_by = None
            db.add(models.FactoryProductExclusionEvent(
                batch_id=batch_id, account_name=account, excluded_factory_id=excluded_factory,
                source_factory_id=source_factory, product_ean=ean, source_manifest_hash=checksum,
                action="APPLIED", reason=reason, actor=actor,
            ))
        db.flush()
        reassigned = 0
        if account == "XL":
            for ean in eans:
                mapping = by_ean[ean]
                if mapping.factory_id == excluded_factory:
                    old_factory = mapping.factory_id
                    old_updated = mapping.updated_at
                    mapping.factory_id = source_factory
                    mapping.updated_at = now
                    db.add(models.FactoryProductReassignment(
                        batch_id=batch_id, account_name=account, product_ean=ean,
                        mapping_id=mapping.id, previous_factory_id=old_factory,
                        target_factory_id=source_factory, previous_updated_at=old_updated,
                        applied_updated_at=now, actor=actor,
                    ))
                    reassigned += 1
        db.commit()
        invalidate_authoritative_caches(account, excluded_factory, source_factory)
        return {"batch_id": batch_id, "sha256": checksum, "excluded_count": len(eans), "reassigned_count": reassigned}
    except Exception:
        db.rollback()
        raise


def rollback_batch(db: Session, batch_id: str, actor: str, expected_hash: str) -> dict:
    try:
        if not actor or len(actor) > 128:
            raise ValueError("A bounded actor is required")
        exclusions = db.query(models.FactoryProductExclusion).filter_by(batch_id=batch_id).with_for_update().all()
        prior_events = db.query(models.FactoryProductExclusionEvent).filter_by(batch_id=batch_id, action="ROLLED_BACK").count()
        if not exclusions:
            if prior_events:
                return {"batch_id": batch_id, "already_rolled_back": True, "removed_exclusions": 0, "restored_mappings": 0}
            raise ValueError("No exclusion batch found")
        account = exclusions[0].account_name
        if any(row.account_name != account or row.source_manifest_hash != expected_hash for row in exclusions):
            raise ValueError("Batch account or manifest hash does not match the explicit rollback confirmation")
        if manifest_hash(row.product_ean for row in exclusions) != expected_hash:
            raise ValueError("Batch EAN set does not match the explicit rollback checksum")
        active_exclusions = [row for row in exclusions if row.is_active]
        if not active_exclusions:
            return {"batch_id": batch_id, "already_rolled_back": True, "removed_exclusions": 0, "restored_mappings": 0}
        restored = 0
        if account == "XL":
            plan = db.query(models.PublishPlan).filter_by(id=EXPECTED_SOURCE_PLAN_IDS[account], account_name=account).with_for_update().first()
            if not plan or plan.status != models.PlanStatus.PAUSED:
                raise ValueError("XL plan 92 must be paused before rolling back the ownership correction")
            processing = db.query(func.count(models.PublishQueueItem.id)).filter_by(
                plan_id=EXPECTED_SOURCE_PLAN_IDS[account], account_name=account,
                status=models.QueueItemStatus.PROCESSING,
            ).scalar()
            if processing:
                raise ValueError("XL plan 92 still has claimed work; rollback stopped")
            for audit in db.query(models.FactoryProductReassignment).filter_by(batch_id=batch_id, status="APPLIED").order_by(models.FactoryProductReassignment.product_ean).with_for_update().all():
                mapping = db.query(models.ProductMapping).filter_by(
                    id=audit.mapping_id, account_name=audit.account_name,
                    product_ean=audit.product_ean, factory_id=audit.target_factory_id,
                    updated_at=audit.applied_updated_at,
                ).with_for_update().first()
                if not mapping:
                    raise ValueError(f"EAN {audit.product_ean} changed after apply; rollback stopped without partial changes")
                mapping.factory_id = audit.previous_factory_id
                mapping.updated_at = datetime.now(timezone.utc)
                audit.status = "ROLLED_BACK"
                audit.rolled_back_at = datetime.now(timezone.utc)
                audit.actor = actor[:128]
                restored += 1
        now = datetime.now(timezone.utc)
        for exclusion in active_exclusions:
            exclusion.is_active = False
            exclusion.deactivated_at = now
            exclusion.deactivated_by = actor
            db.add(models.FactoryProductExclusionEvent(
                batch_id=exclusion.batch_id, account_name=exclusion.account_name,
                excluded_factory_id=exclusion.excluded_factory_id,
                source_factory_id=exclusion.source_factory_id,
                product_ean=exclusion.product_ean,
                source_manifest_hash=exclusion.source_manifest_hash,
                action="ROLLED_BACK", reason=exclusion.reason, actor=actor,
            ))
        db.commit()
        invalidate_authoritative_caches(account, exclusions[0].excluded_factory_id, exclusions[0].source_factory_id)
        return {"batch_id": batch_id, "removed_exclusions": len(active_exclusions), "restored_mappings": restored}
    except Exception:
        db.rollback()
        raise


def _item_ean(item: dict) -> str:
    row = item.get("row") if isinstance(item.get("row"), dict) else {}
    return str(item.get("ean") or row.get("EAN") or "").strip()


def invalidate_authoritative_caches(account: str, excluded_factory: str, source_factory: str) -> None:
    """Best-effort cache invalidation; DB policy is still enforced on every cache read."""
    try:
        from routers.aftercool import redis_client
        if redis_client:
            redis_client.delete(
                f"authoritative_eans:{account}:{excluded_factory}",
                f"authoritative_eans:{account}:{source_factory}",
            )
            # The products endpoint caches pages independently. Delete only
            # these two account/factory namespaces; never touch the other account.
            for factory_id in (excluded_factory, source_factory):
                cursor = 0
                pattern = f"aftercool:products:{account}:{factory_id}:*"
                while True:
                    cursor, keys = redis_client.scan(cursor=cursor, match=pattern, count=500)
                    if keys:
                        redis_client.delete(*keys)
                    if cursor in (0, "0", b"0"):
                        break
    except Exception:
        # A stale excluded-factory cache is filtered by _apply_factory_exclusions on read.
        # Do not make a committed database transaction look rolled back to the operator.
        pass

