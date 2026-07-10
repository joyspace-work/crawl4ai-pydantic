# Shanghai Policy Crawler

A robust, modern policy crawler for Shanghai government websites (`shanghai.gov.cn` and `zwdt.sh.gov.cn`) built on **Crawl4AI**, **Pydantic**, and **uv**.

## Features

- **Dual Crawl Engines**:
  - Preferred REST API client (`shanghai_gov_api.py`) for speed, correctness, and light server load.
  - Fallback dynamic headless browser crawler (`crawl4ai_crawler.py`) using Crawl4AI for JavaScript-heavy SPA pages.
- **Incremental Crawling**: `--stop-on-seen` logic automatically terminates the crawlers once all items on a page have already been saved.
- **Region Normalization**: Uses `cnloc` to standardize regions (e.g. `上海市/上海市/浦东新区`).
- **Bidirectional Linking**: Builds connections between parsed policy documents and project opportunities.

## Getting Started

Ensure you have [uv](https://github.com/astral-sh/uv) installed.

### Installation

Sync the project virtual environment and install all dependencies:

```bash
uv sync
```

### Usage

Run the unified command-line entrypoint to start crawling:

```bash
# Incremental crawl (runs default incremental logic, saves to output/ directory)
uv run crawl

# Full crawl (walks through historical pages and updates existing JSONL records)
uv run crawl --full

# Dry run (prints configuration and stops without writing anything)
uv run crawl --dry-run
```
