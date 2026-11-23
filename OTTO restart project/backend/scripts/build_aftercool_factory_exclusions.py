"""Build the bundled account-scoped factory exclusion policy from the Restart report."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_REPORT = PROJECT_ROOT / "reports" / "filtered_others_loeschen_uk_delete_dont_touch.csv"
OUTPUT_POLICY = PROJECT_ROOT / "backend" / "policies" / "aftercool_factory_exclusions.json"


def build_policy() -> dict[str, object]:
    raw_report = SOURCE_REPORT.read_bytes()
    with SOURCE_REPORT.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        expected_columns = {"account", "kind", "id", "name", "status"}
        if not expected_columns.issubset(set(reader.fieldnames or ())):
            raise ValueError("Factory exclusion report has an unexpected header")

        factory_ids: dict[str, set[str]] = {"JV": set(), "XL": set()}
        for row in reader:
            if str(row.get("kind") or "").strip().casefold() != "lister":
                continue
            account = str(row.get("account") or "").strip().upper()
            identifier = str(row.get("id") or "").strip()
            if account not in factory_ids or not re.fullmatch(r"\d+", identifier):
                raise ValueError("Factory exclusion report contains an invalid lister record")
            factory_ids[account].add(identifier)

    if not all(factory_ids.values()):
        raise ValueError("Factory exclusion report must contain JV and XL lister IDs")

    return {
        "schema_version": 1,
        "source_report": "reports/filtered_others_loeschen_uk_delete_dont_touch.csv",
        "source_report_sha256": hashlib.sha256(raw_report).hexdigest(),
        "source_kind": "lister",
        "excluded_factory_ids": {account: sorted(values) for account, values in factory_ids.items()},
    }


def main() -> None:
    policy = build_policy()
    OUTPUT_POLICY.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_POLICY.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = {account: len(values) for account, values in policy["excluded_factory_ids"].items()}
    print(f"Updated {OUTPUT_POLICY.relative_to(PROJECT_ROOT)} (JV {counts['JV']}, XL {counts['XL']})")


if __name__ == "__main__":
    main()
