"""
crawl.py — Unified entry point for the Shanghai policy crawler.

Usage
-----
  # Incremental crawl (default) — stops when all items on a page are seen
  python crawl.py

  # Full crawl — walks every available page (weekly / backfill)
  python crawl.py --full

  # Limit pages (useful for smoke-testing)
  python crawl.py --full --limit-pages 3
  python crawl.py --limit-pages 1

  # Dry-run — print what would be done, touch nothing
  python crawl.py --dry-run
  python crawl.py --full --dry-run

Modes
-----
  incremental (default)
    Stops as soon as a full page of recent items is already crawled.
    Suitable for daily scheduled runs.

  full (--full)
    Walks every page regardless; existing IDs are still skipped.
    Suitable for first-time runs or after a long gap.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from shanghai_policy_crawler.crawlers import (
    crawl_projects,
    crawl_policies,
    link_projects_and_policies,
)

OUTPUT_DIR    = Path(__file__).parent / "output"
PROJECTS_FILE = OUTPUT_DIR / "projects.jsonl"
POLICIES_FILE = OUTPUT_DIR / "policies.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Shanghai policy crawler — full or incremental mode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--full",
        action="store_true",
        default=False,
        help="Full crawl: walk every page (default: incremental)",
    )
    parser.add_argument(
        "--limit-pages",
        type=int,
        default=None,
        metavar="N",
        help="Stop after N pages (for testing)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print configuration and exit without crawling",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    mode         = "full" if args.full else "incremental"
    stop_on_seen = not args.full        # incremental = stop early
    limit_pages  = args.limit_pages

    print(f"{'=' * 50}")
    print(f"  Mode        : {mode}")
    print(f"  Stop on seen: {stop_on_seen}")
    print(f"  Limit pages : {limit_pages or 'unlimited'}")
    print(f"  Output dir  : {OUTPUT_DIR}")
    print(f"{'=' * 50}")

    if args.dry_run:
        print("[dry-run] No files will be written. Exiting.")
        return

    # Full crawl pre-links existing records before adding new ones
    if args.full:
        link_projects_and_policies(PROJECTS_FILE, POLICIES_FILE)

    new_projects = crawl_projects(
        PROJECTS_FILE,
        limit_pages=limit_pages,
        stop_on_seen=stop_on_seen,
    )
    new_policies = crawl_policies(
        POLICIES_FILE,
        PROJECTS_FILE,
        limit_pages=limit_pages,
        stop_on_seen=stop_on_seen,
    )

    if new_projects > 0 or new_policies > 0:
        link_projects_and_policies(PROJECTS_FILE, POLICIES_FILE)
        print(f"\n✅ Done (+{new_projects} projects, +{new_policies} policies)")
    else:
        print("\n✅ Done — dataset already up-to-date.")


if __name__ == "__main__":
    main()
