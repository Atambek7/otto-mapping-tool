import os
import sys
import asyncio
import logging
from typing import Dict, Set, List
import httpx

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from database import SessionLocal
import models
from routers.aftercool import get_aftercool_session, AFTERCOOL_BASE_URL, redis_client
from services.factory_scope import get_authoritative_factory_eans

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reconcile_factories")

async def reconcile_all_factories(target_factory_id: str = None, dry_run: bool = False, account: str = "JV"):
    """
    Reconciles PostgreSQL ProductMapping.factory_id against Aftercool's authoritative product inventory.
    Guarantees that every product in ProductMapping has the correct factory_id,
    and removes false/foreign associations (such as the 5,682 foreign items under 504201).
    """
    db = SessionLocal()
    try:
        account = str(account or "JV").upper().strip()
        if account not in {"JV", "XL"}:
            raise ValueError("account must be JV or XL")
        session = await get_aftercool_session()
        
        # 1. Fetch all active factories from Aftercool
        async with httpx.AsyncClient(cookies={"session": session}, timeout=30.0) as client:
            res = await client.get(f"{AFTERCOOL_BASE_URL}/api/factories", params={"refresh": "", "fast": ""})
            if res.status_code != 200:
                logger.error(f"Failed to fetch factories from Aftercool: HTTP {res.status_code}")
                return {"error": f"HTTP {res.status_code}"}
            factories_data = res.json()

        if isinstance(factories_data, dict):
            factories = factories_data.get("items") or factories_data.get("factories") or factories_data.get("data") or []
        elif isinstance(factories_data, list):
            factories = factories_data
        else:
            factories = []

        logger.info(f"Loaded {len(factories)} factories from Aftercool.")
        
        # If target_factory_id specified, filter to that factory
        if target_factory_id:
            factories = [f for f in factories if str(f.get("id") or f.get("factory_id")).strip() == str(target_factory_id).strip()]
            logger.info(f"Filtered to target factory: {target_factory_id}")

        # Keep this run strictly account-scoped. Some Aftercool responses omit
        # account, in which case the authenticated session supplies the scope.
        factories = [f for f in factories if str(f.get("account") or f.get("account_name") or account).upper() == account]

        # Collect candidates first. Conflicting factory memberships are not
        # resolved by response order.
        ean_factories: Dict[str, Set[str]] = {}
        factory_eans_map: Dict[str, Set[str]] = {}

        for f in factories:
            fid = str(f.get("id") or f.get("factory_id") or "").strip()
            if not fid:
                continue
            
            eans = await get_authoritative_factory_eans(fid, account=account, force_refresh=True)
            factory_eans_map[fid] = eans
            for ean in eans:
                ean_factories.setdefault(ean, set()).add(fid)
            logger.info(f"Factory {fid} ({f.get('name', 'Unknown')}): {len(eans)} authoritative products.")

        ean_to_factory = {ean: next(iter(fids)) for ean, fids in ean_factories.items() if len(fids) == 1}
        identity_conflicts = {ean for ean, fids in ean_factories.items() if len(fids) > 1}

        protected_factory_by_ean = {}
        for row in db.query(models.FactoryProductExclusion).filter_by(account_name=account, is_active=True).all():
            protected_factory_by_ean[row.product_ean] = row.source_factory_id

        # 2. Update DB mappings
        total_reassigned = 0
        total_cleared = 0
        total_confirmed = 0
        policy_conflicts = set(identity_conflicts)

        # Process each factory in DB
        for fid, auth_eans in factory_eans_map.items():
            # A) Set factory_id for authoritative products
            protected_seen = auth_eans.intersection(protected_factory_by_ean)
            for ean in protected_seen:
                current_mapping = db.query(models.ProductMapping).filter_by(
                    account_name=account, product_ean=ean
                ).first()
                if fid != protected_factory_by_ean[ean] or not current_mapping or current_mapping.factory_id != protected_factory_by_ean[ean]:
                    policy_conflicts.add(ean)
            # Ownership changes for manifest EANs are made only by the audited
            # apply command, never by a generic reconciliation run.
            allowed_eans = set(auth_eans) - protected_seen
            if allowed_eans:
                updated = (
                    db.query(models.ProductMapping)
                    .filter(
                        models.ProductMapping.product_ean.in_(allowed_eans),
                        models.ProductMapping.factory_id != fid,
                        models.ProductMapping.account_name == account,
                    )
                    .update({"factory_id": fid}, synchronize_session=False)
                )
                if updated > 0:
                    logger.info(f"Assigned factory_id='{fid}' to {updated} products in DB.")
                    total_reassigned += updated

            # B) Find products in DB that claim to belong to fid, but are NOT in auth_eans
            foreign_mappings = (
                db.query(models.ProductMapping)
                .filter(
                    models.ProductMapping.factory_id == fid,
                    ~models.ProductMapping.product_ean.in_(auth_eans),
                    models.ProductMapping.account_name == account,
                )
                .all()
            )

            if foreign_mappings:
                logger.info(f"Found {len(foreign_mappings)} foreign/orphaned products marked as factory {fid}.")
                for m in foreign_mappings:
                    canonical_factory = protected_factory_by_ean.get(m.product_ean)
                    if canonical_factory:
                        if fid != canonical_factory or m.factory_id != canonical_factory:
                            policy_conflicts.add(m.product_ean)
                        continue
                    real_fid = ean_to_factory.get(m.product_ean)
                    if real_fid:
                        m.factory_id = real_fid
                        total_reassigned += 1
                    else:
                        m.factory_id = None
                        total_cleared += 1

        if not dry_run:
            db.commit()
            logger.info(f"Reconciliation committed: {total_reassigned} reassigned, {total_cleared} cleared.")
        else:
            db.rollback()
            logger.info(f"Dry-run finished: would reassign {total_reassigned}, would clear {total_cleared}.")

        return {
            "success": True,
            "reassigned": total_reassigned,
            "cleared": total_cleared,
            "factories_processed": len(factory_eans_map),
            "account": account,
            "policy_conflicts": sorted(policy_conflicts),
            "identity_conflict_count": len(identity_conflicts),
        }
    except Exception as e:
        logger.exception(f"Error during reconciliation: {e}")
        db.rollback()
        return {"success": False, "error": str(e)}
    finally:
        db.close()

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(reconcile_all_factories(target_factory_id=target))
