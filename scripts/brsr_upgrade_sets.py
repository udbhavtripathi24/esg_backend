"""Upgrade BRSR response sets from the v1 question bank to the current one.

v1 sets hold their quantitative figures in BRSR table cells; the current bank
builds those tables from the short Annual data form. This fills each set's
blank annual figures from its v1 tables (see app/brsr/upgrade.py).

Safe to run more than once: it only fills blanks and skips sets already on
the current bank.

    python -m scripts.brsr_upgrade_sets            # dry run: shows what would change
    python -m scripts.brsr_upgrade_sets --apply    # writes the changes
"""
import argparse
import json
import sys

from sqlmodel import Session, select

from app.brsr.upgrade import needs_upgrade, upgrade_response_set
from app.db.session import engine
from app.models.brsr import BrsrResponseSet


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = ap.parse_args(argv)
    with Session(engine) as session:
        sets = [rs for rs in session.exec(select(BrsrResponseSet).order_by(BrsrResponseSet.id)).all()
                if needs_upgrade(rs)]
        if not sets:
            print("All BRSR response sets are on the current question bank. Nothing to do.")
            return 0
        for rs in sets:
            report = upgrade_response_set(session, rs, dry_run=not args.apply)
            print(json.dumps(report, indent=2, default=str))
        print(f"{'Upgraded' if args.apply else 'Would upgrade'} {len(sets)} response set(s)."
              + ("" if args.apply else " Re-run with --apply to write."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
