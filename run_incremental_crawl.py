"""
run_incremental_crawl.py — Incremental crawl entry point.

Crawls only NEW projects and policies published since the last run.
Stops as soon as a full page of recent items is already in the local dataset,
avoiding unnecessary API requests.

Usage:
    python run_incremental_crawl.py

Recommended: schedule daily (e.g. via cron) to stay up-to-date without
re-crawling the entire history.

Difference from run_full_crawl.py
----------------------------------
| Behaviour            | run_full_crawl | run_incremental_crawl |
|----------------------|---------------|-----------------------|
| Pages scanned        | ALL           | Until no new items    |
| Pre-link step        | Yes           | No (not needed)       |
| Typical runtime      | Long          | Short                 |
| Best for             | First run /   | Daily scheduled runs  |
|                      | backfill      |                       |
"""
import sys
from pathlib import Path

# Ensure src/ is on the Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from shanghai_policy_crawler.crawlers import (
    crawl_projects,
    crawl_policies,
    link_projects_and_policies,
)

OUTPUT_DIR    = Path(__file__).parent / "output"
PROJECTS_FILE = OUTPUT_DIR / "projects.jsonl"
POLICIES_FILE = OUTPUT_DIR / "policies.jsonl"

if __name__ == "__main__":
    # 1. Incremental project crawl — stop as soon as a page is fully seen
    new_projects = crawl_projects(PROJECTS_FILE, limit_pages=None, stop_on_seen=True)

    # 2. Incremental policy crawl — stop as soon as a page is fully seen
    new_policies = crawl_policies(
        POLICIES_FILE, PROJECTS_FILE, limit_pages=None, stop_on_seen=True
    )

    # 3. Link only if something new was written
    if new_projects > 0 or new_policies > 0:
        link_projects_and_policies(PROJECTS_FILE, POLICIES_FILE)
        print(f"Incremental run complete: +{new_projects} projects, +{new_policies} policies.")
    else:
        print("Incremental run complete: dataset is already up-to-date.")
