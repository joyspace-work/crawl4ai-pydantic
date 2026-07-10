"""
shanghai_policy_crawler.crawl — Unified command-line interface entry point.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import time

from .crawlers import (
    crawl_projects,
    crawl_policies,
    link_projects_and_policies,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Shanghai policy crawler — full or incremental mode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Directory to save crawled data (default: project root/output)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Determine paths relative to this file if not specified
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        # Go up to the project root (above src/shanghai_policy_crawler)
        output_dir = Path(__file__).resolve().parents[2] / "output"

    projects_file = output_dir / "projects.jsonl"
    policies_file = output_dir / "policies.jsonl"

    mode         = "full" if args.full else "incremental"
    stop_on_seen = not args.full        # incremental = stop early
    limit_pages  = args.limit_pages

    print(f"{'=' * 50}")
    print(f"  Mode        : {mode}")
    print(f"  Stop on seen: {stop_on_seen}")
    print(f"  Limit pages : {limit_pages or 'unlimited'}")
    print(f"  Output dir  : {output_dir}")
    print(f"{'=' * 50}")

    if args.dry_run:
        print("[dry-run] No files will be written. Exiting.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    # Full crawl pre-links existing records before adding new ones
    if args.full:
        link_projects_and_policies(projects_file, policies_file)

    new_projects = crawl_projects(
        projects_file,
        limit_pages=limit_pages,
        stop_on_seen=stop_on_seen,
    )
    new_policies = crawl_policies(
        policies_file,
        projects_file,
        limit_pages=limit_pages,
        stop_on_seen=stop_on_seen,
    )

    if new_projects > 0 or new_policies > 0:
        link_projects_and_policies(projects_file, policies_file)
        print(f"\n✅ Done (+{new_projects} projects, +{new_policies} policies)")
    else:
        print("\n✅ Done — dataset already up-to-date.")


if __name__ == "__main__":
    main()
