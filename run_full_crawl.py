"""
run_full_crawl.py — Full crawl entry point.

Crawls ALL pages of projects and policies from scratch (no early stop).
Existing records are skipped via ID deduplication, but the crawler will
walk every available page to ensure no new item is missed.

Usage:
    python run_full_crawl.py

Recommended: run periodically (e.g. weekly) or after a long gap to ensure
the dataset is complete. For daily / frequent runs use run_incremental_crawl.py.
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
    # 1. Pre-link any existing records before new data arrives
    link_projects_and_policies(PROJECTS_FILE, POLICIES_FILE)

    # 2. Full crawl — iterate every page, no early stop
    crawl_projects(PROJECTS_FILE, limit_pages=None, stop_on_seen=False)
    crawl_policies(POLICIES_FILE, PROJECTS_FILE, limit_pages=None, stop_on_seen=False)

    # 3. Rebuild bidirectional links with newly crawled data
    link_projects_and_policies(PROJECTS_FILE, POLICIES_FILE)
