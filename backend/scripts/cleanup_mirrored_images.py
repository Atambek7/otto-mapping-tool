import os
import sys
import time
import logging
import argparse
from typing import Set, Dict, Any, Optional

# Add backend directory to path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from database import SessionLocal
import models

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cleanup_mirrored_images")


import re

def get_active_eans(db) -> Set[str]:
    """Returns set of all cleaned/alphanumeric EANs currently present in ProductMapping."""
    rows = db.query(models.ProductMapping.product_ean).all()
    return {"".join(c for c in str(r[0]) if c.isalnum()) for r in rows if r[0]}


def extract_ean_from_filename(filename: str) -> Optional[str]:
    """
    Extracts clean EAN from mirrored image names like:
    mirrored_XL_4071489325744_0624bb0fff.jpg -> 4071489325744
    mirrored_upscaled_4071489325744_0624bb0fff.jpg -> 4071489325744
    """
    m = re.match(r"^mirrored_(?:XL_|upscaled_)?([0-9a-zA-Z]+)_[a-f0-9]+(?:\.jpg|\.jpeg|\.png)?$", filename, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def get_referenced_mirrored_files(db) -> Set[str]:
    """
    Queries database for all active image URLs that point to mirrored files in static/mirrored/.
    Returns a set of filenames (e.g. {'mirrored_XL_123_abc.jpg', ...}).
    """
    referenced = set()

    # 1. ProductMapping.image_url
    mappings = (
        db.query(models.ProductMapping.image_url)
        .filter(models.ProductMapping.image_url.isnot(None))
        .all()
    )
    for (img_url,) in mappings:
        if img_url and "/static/mirrored/" in img_url:
            clean_name = os.path.basename(img_url.split("?")[0].strip())
            if clean_name:
                referenced.add(clean_name)

    # 2. Check PublishQueueItem payload / error references if applicable
    try:
        queue_items = db.query(models.PublishQueueItem.item_payload).all()
        for (payload,) in queue_items:
            if isinstance(payload, dict):
                # Check main image and media
                for k, v in payload.items():
                    if isinstance(v, str) and "/static/mirrored/" in v:
                        clean_name = os.path.basename(v.split("?")[0].strip())
                        if clean_name:
                            referenced.add(clean_name)
                    elif isinstance(v, list):
                        for sub_v in v:
                            if isinstance(sub_v, str) and "/static/mirrored/" in sub_v:
                                clean_name = os.path.basename(sub_v.split("?")[0].strip())
                                if clean_name:
                                    referenced.add(clean_name)
    except Exception as q_err:
        logger.debug(f"Could not inspect PublishQueueItem payloads: {q_err}")

    return referenced


def clean_mirrored_images(
    db=None,
    mirrored_dir: Optional[str] = None,
    older_than_days: int = 14,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """
    Scans mirrored directory and cleans up unreferenced files.
    
    Args:
        db: SQLAlchemy session (if None, creates one)
        mirrored_dir: Path to mirrored files (defaults to static/mirrored)
        older_than_days: Only delete files older than N days (0 to check all)
        dry_run: If True, only report what would be deleted without deleting
    """
    should_close_db = False
    if db is None:
        db = SessionLocal()
        should_close_db = True

    if not mirrored_dir:
        mirrored_dir = os.path.join(backend_dir, "static", "mirrored")

    stats = {
        "mirrored_dir": mirrored_dir,
        "dry_run": dry_run,
        "older_than_days": older_than_days,
        "total_files_on_disk": 0,
        "total_disk_mb": 0.0,
        "referenced_files_count": 0,
        "orphaned_files_count": 0,
        "eligible_for_deletion_count": 0,
        "reclaimable_mb": 0.0,
        "deleted_files_count": 0,
        "reclaimed_mb": 0.0,
        "errors": [],
    }

    try:
        if not os.path.exists(mirrored_dir):
            logger.warning(f"Mirrored directory {mirrored_dir} does not exist.")
            return stats

        referenced_files = get_referenced_mirrored_files(db)
        active_eans = get_active_eans(db)
        stats["referenced_files_count"] = len(referenced_files)
        logger.info(
            f"Discovered {len(referenced_files)} explicit mirrored image URLs "
            f"and {len(active_eans)} active product EANs in DB."
        )

        all_entries = os.listdir(mirrored_dir)
        files = [f for f in all_entries if os.path.isfile(os.path.join(mirrored_dir, f))]
        stats["total_files_on_disk"] = len(files)

        now = time.time()
        age_threshold_sec = older_than_days * 86400

        total_bytes = 0
        reclaimable_bytes = 0
        files_to_delete = []

        for fname in files:
            fpath = os.path.join(mirrored_dir, fname)
            try:
                fstat = os.stat(fpath)
                fsize = fstat.st_size
                total_bytes += fsize
                file_age_sec = now - fstat.st_mtime

                # Check if file belongs to an active product or is explicitly referenced
                file_ean = extract_ean_from_filename(fname)
                is_in_use = (fname in referenced_files) or (file_ean and file_ean in active_eans)

                if not is_in_use:
                    stats["orphaned_files_count"] += 1
                    if file_age_sec >= age_threshold_sec:
                        files_to_delete.append((fname, fpath, fsize))
                        reclaimable_bytes += fsize
            except Exception as stat_err:
                logger.debug(f"Could not stat {fpath}: {stat_err}")

        stats["total_disk_mb"] = round(total_bytes / (1024 * 1024), 2)
        stats["eligible_for_deletion_count"] = len(files_to_delete)
        stats["reclaimable_mb"] = round(reclaimable_bytes / (1024 * 1024), 2)

        logger.info(
            f"Disk status: {stats['total_files_on_disk']} files ({stats['total_disk_mb']} MB). "
            f"Orphaned: {stats['orphaned_files_count']}. "
            f"Eligible (> {older_than_days} days): {len(files_to_delete)} ({stats['reclaimable_mb']} MB)."
        )

        if dry_run:
            logger.info("DRY-RUN mode active. No files were removed. Use --execute to delete.")
            return stats

        # Actual deletion
        deleted_count = 0
        deleted_bytes = 0
        for fname, fpath, fsize in files_to_delete:
            try:
                os.remove(fpath)
                deleted_count += 1
                deleted_bytes += fsize
            except Exception as del_err:
                logger.warning(f"Failed to delete {fpath}: {del_err}")
                stats["errors"].append(f"{fname}: {str(del_err)}")

        stats["deleted_files_count"] = deleted_count
        stats["reclaimed_mb"] = round(deleted_bytes / (1024 * 1024), 2)
        logger.info(f"Successfully deleted {deleted_count} files. Reclaimed {stats['reclaimed_mb']} MB.")

    finally:
        if should_close_db and db:
            db.close()

    return stats


def main():
    parser = argparse.ArgumentParser(description="Clean up unused mirrored images in static/mirrored/")
    parser.add_argument("--execute", action="store_true", help="Actually delete files (default is dry-run)")
    parser.add_argument("--older-than", type=int, default=14, help="Only delete files older than N days (default: 14)")
    parser.add_argument("--dir", type=str, default=None, help="Path to static/mirrored directory")
    args = parser.parse_args()

    dry_run = not args.execute
    stats = clean_mirrored_images(
        mirrored_dir=args.dir,
        older_than_days=args.older_than,
        dry_run=dry_run,
    )
    print("\n--- Summary ---")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
