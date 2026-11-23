"""
Export published EANs via Drip Feed grouped by factory into two files:
drip_published_jv.txt and drip_published_xl.txt.
"""
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

# Add backend directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
import models
from routers.published_export import _load_factory_names_map

OUTPUT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def export_account_published_eans(account_name: str, db, fmap):
    rows = (
        db.query(
            models.PublishPlan.factory_id,
            models.PublishPlan.factory_name,
            models.PublishQueueItem.product_ean,
            models.PublishQueueItem.published_at
        )
        .join(models.PublishPlan, models.PublishQueueItem.plan_id == models.PublishPlan.id)
        .filter(
            models.PublishQueueItem.account_name == account_name,
            models.PublishQueueItem.status == models.QueueItemStatus.PUBLISHED
        )
        .order_by(models.PublishPlan.factory_id, models.PublishQueueItem.published_at.asc())
        .all()
    )

    factories = defaultdict(lambda: {'name': '', 'eans': []})
    for fid, pf_name, ean, pub_at in rows:
        fid_str = str(fid)
        if not factories[fid_str]['name']:
            resolved = fmap.get(fid_str) or (pf_name if pf_name and pf_name != fid_str else None) or f"Factory_{fid_str}"
            factories[fid_str]['name'] = resolved
        if ean not in factories[fid_str]['eans']:
            factories[fid_str]['eans'].append(ean)

    # Sort factories alphabetically by name
    sorted_factories = sorted(factories.items(), key=lambda item: item[1]['name'].lower())
    total_unique_eans = sum(len(f['eans']) for f in factories.values())
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = []
    lines.append("=" * 80)
    lines.append(f"АККАУНТ: {account_name}")
    lines.append(f"СПИСОК ОПУБЛИКОВАННЫХ ТОВАРОВ (EAN) ЧЕРЕЗ DRIP FEED")
    lines.append(f"ДАТА ВЫГРУЗКИ: {now_str}")
    lines.append(f"ВСЕГО ФАБРИК: {len(sorted_factories)} | ВСЕГО ОПУБЛИКОВАНО УНИКАЛЬНЫХ EAN: {total_unique_eans}")
    lines.append("=" * 80)
    lines.append("")
    lines.append("СВОДКА ПО ФАБРИКАМ:")
    for idx, (fid, data) in enumerate(sorted_factories, 1):
        lines.append(f"  {idx:2d}. {data['name']} (ID: {fid}) — {len(data['eans'])} EAN")
    lines.append("")
    lines.append("=" * 80)
    lines.append("")

    for fid, data in sorted_factories:
        lines.append("-" * 80)
        lines.append(f"ФАБРИКА: {data['name']}")
        lines.append(f"ID фабрики: {fid}")
        lines.append(f"Аккаунт: {account_name}")
        lines.append(f"Количество опубликованных EAN: {len(data['eans'])}")
        lines.append("-" * 80)
        for ean in data['eans']:
            lines.append(ean)
        lines.append("")

    filename = f"drip_published_{account_name.lower()}.txt"
    out_path = os.path.join(OUTPUT_DIR, filename)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Successfully written {len(sorted_factories)} factories and {total_unique_eans} EANs to {out_path}")

def main():
    db = SessionLocal()
    fmap = _load_factory_names_map()
    try:
        export_account_published_eans("JV", db, fmap)
        export_account_published_eans("XL", db, fmap)
    finally:
        db.close()

if __name__ == "__main__":
    main()

