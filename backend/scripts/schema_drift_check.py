import os
import sys
import logging
import argparse
from typing import List, Dict, Any, Optional, Set

# Add backend directory to sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from database import SessionLocal
import models
from services.categories_repo import get_category_by_id, get_groups, get_allowed_attribute_names
from services.otto_client import OttoMarketClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("schema_drift_check")

# Known mandatory GPSR & EU compliance attributes that must be verified
GPSR_COMPLIANCE_ATTRIBUTES = {
    "Produktsicherheitsverantwortlicher",
    "Herstellerinformationen",
    "Sicherheitsdatenblatt",
    "Gefahrenhinweise",
}


def get_local_category_attributes(category_id: str) -> Dict[str, Any]:
    """
    Extracts local schema requirements for a category from categories_repo and group definitions.
    Returns:
      {
        "category_id": str,
        "name": str,
        "group_id": str,
        "required_attributes": set[str],
        "allowed_attributes": set[str],
        "attribute_details": dict[str, dict],
      }
    """
    cat = get_category_by_id(category_id)
    if not cat:
        return {
            "category_id": category_id,
            "name": None,
            "group_id": None,
            "required_attributes": set(),
            "allowed_attributes": set(),
            "attribute_details": {},
        }

    group_id = str(cat.get("category_group_id") or "")
    group_data = get_groups().get(group_id, {})
    raw_attrs = group_data.get("attributes", {}).get("attributes", [])

    required = set()
    details = {}
    for a in raw_attrs:
        attr_name = a.get("name")
        if not attr_name:
            continue
        is_req = a.get("relevance") == "HIGH"
        if is_req:
            required.add(attr_name)
        details[attr_name] = {
            "type": a.get("type"),
            "required": is_req,
            "allowed_values": a.get("allowedValues"),
        }

    allowed = get_allowed_attribute_names(category_id) or set()
    return {
        "category_id": category_id,
        "name": cat.get("name"),
        "group_id": group_id,
        "required_attributes": required,
        "allowed_attributes": allowed,
        "attribute_details": details,
    }


def compare_category_schemas(
    local_schema: Dict[str, Any],
    remote_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compares local category schema with remote OTTO data and compliance rules.
    Returns a dict with drift flags and difference details.
    """
    diff: Dict[str, Any] = {
        "has_drift": False,
        "new_required": [],
        "removed_required": [],
        "changed_values": {},
        "missing_compliance_fields": [],
        "notes": [],
    }

    local_req = local_schema.get("required_attributes", set())
    local_allowed = local_schema.get("allowed_attributes", set())

    # 1. Compare against remote OTTO API response if available
    if remote_data and isinstance(remote_data, dict):
        remote_attrs = remote_data.get("attributes", [])
        if isinstance(remote_attrs, dict):
            remote_attrs = remote_attrs.get("attributes", [])

        remote_req: Set[str] = set()
        for r_attr in remote_attrs:
            r_name = r_attr.get("name")
            if not r_name:
                continue
            if r_attr.get("mandatory") is True or r_attr.get("relevance") == "HIGH":
                remote_req.add(r_name)

        new_req = sorted(list(remote_req - local_req))
        rem_req = sorted(list(local_req - remote_req))

        if new_req:
            diff["has_drift"] = True
            diff["new_required"] = new_req
            diff["notes"].append(f"Remote OTTO schema added {len(new_req)} new required attributes: {new_req}")

        if rem_req:
            diff["removed_required"] = rem_req

    # 2. Check GPSR compliance coverage
    for gpsr_field in GPSR_COMPLIANCE_ATTRIBUTES:
        if gpsr_field in local_allowed and gpsr_field not in local_req:
            diff["missing_compliance_fields"].append(gpsr_field)

    return diff


def audit_category_drift(
    category_id: str,
    db,
    otto_client: Optional[OttoMarketClient] = None,
) -> models.SchemaDriftLog:
    """
    Audits a single category for drift, logs the outcome to DB, and returns the log object.
    """
    local_schema = get_local_category_attributes(category_id)
    cat_name = local_schema.get("name") or f"Category {category_id}"

    remote_data = None
    if otto_client:
        try:
            res = otto_client.get_category_definition(category_id)
            if res.get("status_code") == 200:
                remote_data = res.get("data")
        except Exception as e:
            logger.debug(f"Could not fetch remote OTTO schema for {category_id}: {e}")

    diff = compare_category_schemas(local_schema, remote_data)

    log_entry = models.SchemaDriftLog(
        category_id=str(category_id),
        category_name=cat_name,
        has_drift=diff["has_drift"],
        diff_details=diff,
    )
    db.add(log_entry)
    return log_entry


def run_schema_drift_audit(
    db=None,
    category_id: Optional[str] = None,
    all_published: bool = True,
    check_api: bool = False,
    account_name: str = "JV",
) -> Dict[str, Any]:
    """
    Main audit runner for schema drift across published or specified categories.
    """
    should_close_db = False
    if db is None:
        db = SessionLocal()
        should_close_db = True

    results = {
        "categories_checked": 0,
        "drift_detected_count": 0,
        "categories_with_drift": [],
        "details": [],
    }

    try:
        categories_to_check: List[str] = []
        if category_id:
            categories_to_check = [str(category_id).strip()]
        elif all_published:
            rows = (
                db.query(models.ProductMapping.category_id)
                .filter(models.ProductMapping.status == "Published")
                .filter(models.ProductMapping.category_id.isnot(None))
                .distinct()
                .all()
            )
            categories_to_check = [str(r[0]).strip() for r in rows if r[0]]

        results["categories_checked"] = len(categories_to_check)
        logger.info(f"Checking schema drift for {len(categories_to_check)} categories...")

        # Setup OTTO client if check_api requested
        otto_client = None
        if check_api:
            settings = (
                db.query(models.AccountSettings)
                .filter(models.AccountSettings.account_name == account_name.upper())
                .first()
            )
            if settings and settings.otto_client_id and settings.otto_client_secret:
                otto_client = OttoMarketClient(
                    client_id=settings.otto_client_id,
                    client_secret=settings.otto_client_secret,
                    account_name=account_name.upper(),
                    partner_id=settings.otto_partner_id,
                )

        for cat_id in categories_to_check:
            log_item = audit_category_drift(cat_id, db=db, otto_client=otto_client)
            if log_item.has_drift:
                results["drift_detected_count"] += 1
                results["categories_with_drift"].append(cat_id)
            results["details"].append({
                "category_id": cat_id,
                "category_name": log_item.category_name,
                "has_drift": log_item.has_drift,
                "diff": log_item.diff_details,
            })

        db.commit()

    finally:
        if should_close_db and db:
            db.close()

    return results


def main():
    parser = argparse.ArgumentParser(description="Check OTTO category schema drift and compliance")
    parser.add_argument("--category-id", type=str, default=None, help="Check specific category ID")
    parser.add_argument("--all-published", action="store_true", default=True, help="Check all categories for Published products")
    parser.add_argument("--check-api", action="store_true", help="Query live OTTO API for comparison")
    parser.add_argument("--account", type=str, default="JV", help="Account name for API credentials")
    args = parser.parse_args()

    res = run_schema_drift_audit(
        category_id=args.category_id,
        all_published=args.all_published and not args.category_id,
        check_api=args.check_api,
        account_name=args.account,
    )

    print("\n================ SCHEMA DRIFT AUDIT REPORT ================")
    print(f"Categories checked: {res['categories_checked']}")
    print(f"Drift detected in:  {res['drift_detected_count']} categories")
    if res["categories_with_drift"]:
        print(f"Affected categories: {', '.join(res['categories_with_drift'])}")
    for d in res["details"]:
        status_icon = "⚠️ DRIFT" if d["has_drift"] else "✅ OK"
        print(f"[{status_icon}] Category {d['category_id']} ({d['category_name']})")
        if d["has_drift"]:
            for note in d["diff"].get("notes", []):
                print(f"    - {note}")
    print("===========================================================\n")


if __name__ == "__main__":
    main()
