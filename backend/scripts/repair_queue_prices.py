"""Inspect a single plan, then optionally apply its local-only repair report.

Default: read Aftercool and OTTO and save a private report. --apply only fills
missing local prices and stores remote observations; no publishing or OTTO writes.
"""
import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import SessionLocal
from services.queue_reconciliation import build_report, apply_report, refresh_unknown


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan-id', type=int, required=True)
    parser.add_argument('--account', choices=['JV', 'XL'], required=True)
    parser.add_argument('--factory-id', required=True)
    parser.add_argument('--report', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--recheck', action='store_true', help='Retry inconclusive OTTO reads in an existing report')
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.apply or args.recheck:
            report = json.loads(Path(args.report).read_text())
            if (report['plan_id'], report['account'], report['factory_id']) != (args.plan_id, args.account, args.factory_id):
                raise ValueError('Report scope does not match CLI arguments')
            if args.apply:
                print(json.dumps(apply_report(db, report)))
            else:
                report = refresh_unknown(db, report)
                temporary = args.report + '.pending'
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as output:
                    json.dump(report, output, ensure_ascii=False, indent=2)
                os.replace(temporary, args.report)
                print(json.dumps({
                    'existing_otto_cards': sum(r['otto'].get('exists') is True for r in report['items']),
                    'unknown_otto_status': sum(r['otto'].get('exists') is None for r in report['items']),
                }))
        else:
            report = build_report(db, args.plan_id, args.account, args.factory_id)
            fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as output:
                json.dump(report, output, ensure_ascii=False, indent=2)
            print(json.dumps({
                'items_checked': len(report['items']),
                'existing_otto_cards': sum(r['otto'].get('exists') is True for r in report['items']),
                'unknown_otto_status': sum(r['otto'].get('exists') is None for r in report['items']),
                'missing_local_prices': sum(not r['old_price'] and not r['old_price_af'] for r in report['items']),
                'report': args.report,
            }))


if __name__ == '__main__':
    main()
