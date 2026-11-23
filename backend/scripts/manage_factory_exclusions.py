#!/usr/bin/env python3
"""Preview/apply/rollback the reviewed JV and XL factory exclusion manifests."""
import argparse
import json
import sys
from pathlib import Path

from database import SessionLocal
from services.factory_exclusions import apply_batch, preview_batch, rollback_batch, validate_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preview", "apply", "rollback"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--batch-id")
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--actor")
    parser.add_argument("--confirm-batch", help="Must equal manifest batch_id for apply/rollback")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    eans, digest = validate_manifest(manifest, args.expected_sha256)

    if args.mode == "rollback":
        manifest_batch_id = str(manifest.get("batch_id", ""))
        batch_id = args.batch_id or manifest_batch_id
        if not batch_id or batch_id != manifest_batch_id or args.confirm_batch != batch_id:
            parser.error("rollback requires manifest batch_id, matching --batch-id, and --confirm-batch")
        if not args.actor:
            parser.error("rollback requires an explicit --actor")
        session = SessionLocal()
        try:
            result = rollback_batch(session, batch_id, args.actor, digest)
        finally:
            session.close()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0

    if args.mode == "preview":
        session = SessionLocal()
        try:
            result = preview_batch(session, manifest, digest)
        finally:
            session.close()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0

    batch_id = str(manifest.get("batch_id", ""))
    if not batch_id or args.confirm_batch != batch_id:
        parser.error("apply requires --confirm-batch exactly matching manifest batch_id")
    if not args.actor:
        parser.error("apply requires an explicit --actor")
    session = SessionLocal()
    try:
        result = apply_batch(session, manifest, digest, args.actor)
    finally:
        session.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"factory exclusion command failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
