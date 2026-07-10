"""
crawl.py — Root wrapper for the Shanghai policy crawler.

Delegates execution to shanghai_policy_crawler.crawl.
"""
import sys
from pathlib import Path

# Add src/ directory to path to allow direct execution
src_path = str(Path(__file__).parent.resolve() / "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from shanghai_policy_crawler.crawl import main

if __name__ == "__main__":
    main()
