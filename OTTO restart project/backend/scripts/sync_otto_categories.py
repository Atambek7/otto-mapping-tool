"""Sync local OTTO category taxonomy snapshots from live /v5/products/categories (read-only)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))


def merge_taxonomy(
    live_groups: list[dict[str, Any]],
    existing_categories: list[dict[str, Any]],
    existing_groups: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, int]]:
    existing_gid_by_name: dict[str, str] = {}
    existing_attr_ids_by_group: dict[str, dict[str, tuple[Any, Any]]] = {}
    for gid, gdata in existing_groups.items():
        gname = str(gdata.get("group_name") or "").strip()
        if gname:
            existing_gid_by_name[gname] = str(gid)
        attr_ids: dict[str, tuple[Any, Any]] = {}
        for attr in (gdata.get("attributes") or {}).get("attributes") or []:
            aname = str(attr.get("name") or "").strip()
            if aname:
                attr_ids[aname] = (attr.get("attributeId"), attr.get("attributeKey"))
        existing_attr_ids_by_group[str(gid)] = attr_ids

    existing_cid_by_name: dict[str, Any] = {}
    for cat in existing_categories:
        cname = str(cat.get("name") or "").strip()
        if cname and cname not in existing_cid_by_name:
            existing_cid_by_name[cname] = cat.get("categoryId")

    max_gid = max((int(k) for k in existing_groups if str(k).isdigit()), default=0)
    max_cid = max((int(c["categoryId"]) for c in existing_categories if str(c.get("categoryId", "")).isdigit()), default=0)

    new_groups: dict[str, dict[str, Any]] = {}
    new_categories: list[dict[str, Any]] = []
    seen_categories: set[str] = set()
    stats = {"groups": 0, "new_groups": 0, "categories": 0, "new_categories": 0}

    for live_group in live_groups:
        gname = str(live_group.get("categoryGroup") or "").strip()
        if not gname:
            continue
        gid = existing_gid_by_name.get(gname)
        if gid is None:
            max_gid += 1
            gid = str(max_gid)
            stats["new_groups"] += 1

        prior_group = existing_groups.get(gid) or {}
        prior_attr_ids = existing_attr_ids_by_group.get(gid, {})

        cat_names = [str(c).strip() for c in (live_group.get("categories") or []) if str(c).strip()]
        sample_cid = prior_group.get("sample_category_id")
        cat_ids_in_group: list[Any] = []
        for cname in cat_names:
            if cname in seen_categories:
                continue
            seen_categories.add(cname)
            cid = existing_cid_by_name.get(cname)
            if cid is None:
                max_cid += 1
                cid = max_cid
                stats["new_categories"] += 1
            cat_ids_in_group.append(cid)
            new_categories.append({
                "category_group": gname,
                "category_group_id": int(gid) if str(gid).isdigit() else gid,
                "categoryId": cid,
                "name": cname,
            })
            stats["categories"] += 1

        if sample_cid not in cat_ids_in_group and cat_ids_in_group:
            sample_cid = cat_ids_in_group[0]

        formatted_attrs: list[dict[str, Any]] = []
        for attr in live_group.get("attributes") or []:
            if not isinstance(attr, dict):
                continue
            aname = str(attr.get("name") or "").strip()
            if not aname:
                continue
            prior_id, prior_key = prior_attr_ids.get(aname, (aname, aname))
            formatted_attrs.append({
                "attributeId": prior_id if prior_id is not None else aname,
                "attributeKey": prior_key if prior_key is not None else aname,
                "name": aname,
                "type": attr.get("type"),
                "attributeGroup": attr.get("attributeGroup") or "",
                "description": attr.get("description") or "",
                "relevance": attr.get("relevance"),
                "featureRelevance": list(attr.get("featureRelevance") or []),
                "multiValue": bool(attr.get("multiValue", False)),
                "unit": attr.get("unit") or "",
                "unitDisplayName": attr.get("unitDisplayName"),
                "allowedValues": list(attr.get("allowedValues") or []),
                "recommendedValues": list(attr.get("recommendedValues") or []),
                "exampleValues": list(attr.get("exampleValues") or []),
            })

        new_groups[gid] = {
            "group_name": gname,
            "sample_category_id": sample_cid,
            "attributes": {"attributes": formatted_attrs},
        }
        stats["groups"] += 1

    new_categories.sort(key=lambda item: (str(item.get("name") or "").lower(), str(item.get("categoryId") or "")))
    return new_categories, new_groups, stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync OttoRestart categories.json and category_groups.json from OTTO API")
    parser.add_argument("--account", default="JV", choices=["JV", "XL"])
    parser.add_argument("--data-dir", default=str(BACKEND_ROOT / "data"))
    parser.add_argument("--apply", action="store_true", help="Write updated JSON files (default is dry-run)")
    args = parser.parse_args()

    from dotenv import load_dotenv
    import os
    from integrations.otto_client import OttoMarketClient
    from runtime_mode import RuntimeConfig

    load_dotenv(BACKEND_ROOT / ".env")
    runtime_config = RuntimeConfig.from_environment(os.environ)
    client = OttoMarketClient.from_environment(args.account, runtime_config=runtime_config)
    live_groups = client.list_categories(max_pages=50)

    data_dir = Path(args.data_dir)
    cats_path = data_dir / "categories.json"
    groups_path = data_dir / "category_groups.json"
    existing_categories = json.loads(cats_path.read_text(encoding="utf-8")) if cats_path.exists() else []
    existing_groups = json.loads(groups_path.read_text(encoding="utf-8")) if groups_path.exists() else {}

    new_categories, new_groups, stats = merge_taxonomy(live_groups, existing_categories, existing_groups)
    print(json.dumps({"apply": args.apply, **stats}, ensure_ascii=False))
    if args.apply:
        cats_path.write_text(json.dumps(new_categories, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        groups_path.write_text(json.dumps(new_groups, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
