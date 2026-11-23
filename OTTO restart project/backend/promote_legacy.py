"""Fail-closed placeholder for auditable legacy promotion.

Promotion remains unavailable until the signed actor contract (S03) and
evidence ledger/validator (S11) exist. This command never connects to a DB.
"""

from __future__ import annotations

import argparse
import os
import sys

from services.legacy_scope_service import LEGACY_TABLES, LegacyPromotionRejected, require_legacy_promotion


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Request an audited legacy record promotion")
    parser.add_argument("--account", choices=("JV", "XL"), required=True)
    parser.add_argument("--table", choices=LEGACY_TABLES, required=True)
    parser.add_argument("--row-id", required=True)
    parser.add_argument("--evidence-ref", required=True)
    args = parser.parse_args(argv)

    enabled = os.environ.get("OTTORESTART_LEGACY_PROMOTION_ENABLED", "false").strip().lower() == "true"
    actor_id = os.environ.get("OTTORESTART_PROMOTION_ACTOR_ID")
    try:
        # There is intentionally no caller-controlled evidence_verified option.
        require_legacy_promotion(
            enabled=enabled,
            actor_id=actor_id,
            evidence_ref=args.evidence_ref,
            evidence_verified=False,
        )
    except LegacyPromotionRejected as exc:
        print(f"Legacy promotion refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
