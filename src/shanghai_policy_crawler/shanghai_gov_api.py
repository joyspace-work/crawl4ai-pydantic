"""
shanghai_gov_api.py — Pure REST API crawler for www.shanghai.gov.cn

Two-step pipeline (zero Firecrawl):
  Step 1 — List API:   POST /gwk/policy/page       → yields businessId list
  Step 2 — Detail API: POST /gwk/policy/detail     → returns full HTML content (txt field)

Both APIs discovered via browser DevTools on the SPA frontend.
The detail endpoint contains the complete policy text in the `txt` field (HTML),
metadata (title, docType, docNo, publishDate, agency, effectiveFlag, indexNo, …),
and a PDF download URL (attrs.zwPDFUrl).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Generator, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── API endpoints ────────────────────────────────────────────────────────────
BASE_URL        = "https://www.shanghai.gov.cn"
LIST_API        = f"{BASE_URL}/gwk/policy/page"
DETAIL_API      = f"{BASE_URL}/gwk/policy/detail"
DETAIL_PAGE_TPL = f"{BASE_URL}/zhengce/detail?businessId={{businessId}}&siteId={{siteId}}"

# siteId for 市级政策
CITY_SITE_IDS = ["0001"]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/json",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/zhengce/more?level=city",
}


def _html_to_text(html: str) -> str:
    """Strip HTML tags and normalize whitespace for the policy body."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    # Collapse blank lines
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


class ShanghaiGovApiClient:
    """
    Two-step REST API crawler for www.shanghai.gov.cn policies.

    Usage:
        client = ShanghaiGovApiClient(site_id_list=["0001"])
        for record in client.iter_full_records(max_pages=77):
            print(record["title"], record["content"][:200])
    """

    def __init__(
        self,
        site_id_list: list[str] | None = None,
        page_size: int = 20,
        list_delay: float = 0.5,
        detail_delay: float = 0.3,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.site_id_list = site_id_list or CITY_SITE_IDS
        self.page_size = page_size
        self.list_delay = list_delay
        self.detail_delay = detail_delay
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    # ── Step 1: List API ─────────────────────────────────────────────────────

    def fetch_list_page(self, page_no: int) -> dict[str, Any]:
        """Fetch one page of the policy list."""
        payload: dict[str, Any] = {
            "pageNo": page_no,
            "pageSize": self.page_size,
        }
        if self.site_id_list:
            payload["siteIdList"] = self.site_id_list

        resp = self.session.post(LIST_API, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"List API error: {data.get('msg')} (code={data.get('code')})")
        return data

    def iter_list_records(
        self,
        max_pages: int = 0,
        min_year: int = 0,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Yields lightweight list records (no full body yet).
        Each record includes `businessId`, `siteId`, `publishDate`, `title`, `summary`.
        """
        page_no = 1
        total_pages: int | None = None

        while True:
            if max_pages and page_no > max_pages:
                logger.info("Reached max_pages=%d, stopping list fetch.", max_pages)
                break

            logger.info("List page %d/%s …", page_no, total_pages or "?")
            try:
                data = self.fetch_list_page(page_no)
            except Exception as exc:
                logger.error("Failed to fetch list page %d: %s", page_no, exc)
                break

            page_data = data.get("data", {})
            total_pages = page_data.get("totalPage", 1)
            records: list[dict] = page_data.get("records", [])

            if not records:
                logger.info("Empty records on page %d, stopping.", page_no)
                break

            for rec in records:
                publish_date = (rec.get("publishDate") or rec.get("displayDate") or "")[:10]
                year = int(publish_date[:4]) if publish_date and len(publish_date) >= 4 else 0
                if min_year and year and year < min_year:
                    logger.debug("Skip %s (year %d < min_year %d)", rec.get("businessId"), year, min_year)
                    continue
                yield rec

            if page_no >= total_pages:
                logger.info("Last list page reached (%d/%d).", page_no, total_pages)
                break

            page_no += 1
            time.sleep(self.list_delay)

    # ── Step 2: Detail API ───────────────────────────────────────────────────

    def fetch_detail(self, business_id: str, site_id: str = "0001") -> dict[str, Any]:
        """
        Fetch full policy detail from POST /gwk/policy/detail.
        Returns the `data` dict which includes `txt` (full HTML content).
        """
        payload = {"siteId": site_id, "businessId": business_id}
        # Detail page as Referer to pass server checks
        self.session.headers.update({
            "Referer": DETAIL_PAGE_TPL.format(businessId=business_id, siteId=site_id)
        })
        resp = self.session.post(DETAIL_API, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(
                f"Detail API error for {business_id}: {data.get('msg')} (code={data.get('code')})"
            )
        return data.get("data", {})

    # ── Combined pipeline ────────────────────────────────────────────────────

    def iter_full_records(
        self,
        max_pages: int = 0,
        min_year: int = 0,
        existing_ids: Optional[set[str]] = None,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Main entry point: yields one fully-enriched record per policy.

        Each yielded dict contains:
          - title, summary, publishDate, displayDate
          - docType, docNo, docYear, indexNo, genre, effectiveFlag
          - agency (from attrs), openType, draftUnit, theme
          - content       : plain text body (stripped HTML from `txt` field)
          - content_html  : raw HTML body
          - pdf_url       : absolute URL to PDF attachment (if any)
          - detail_url    : canonical frontend URL
          - businessId, siteId
          - relates       : list of related interpretations/readings
        """
        for list_rec in self.iter_list_records(max_pages=max_pages, min_year=min_year):
            business_id = list_rec.get("businessId", "")
            if existing_ids and business_id in existing_ids:
                logger.info("Policy already crawled: %s (Skipping detail fetch)", business_id)
                continue
            site_id = list_rec.get("siteId", "0001")

            time.sleep(self.detail_delay)
            try:
                detail = self.fetch_detail(business_id, site_id)
            except Exception as exc:
                logger.warning("Detail fetch failed for %s: %s", business_id, exc)
                continue

            attrs = detail.get("attrs", {})
            txt_html = detail.get("txt", "")
            content_text = _html_to_text(txt_html)

            # Build absolute PDF URL
            pdf_path = attrs.get("zwPDFUrl", "")
            pdf_url = f"{BASE_URL}{pdf_path}" if pdf_path and pdf_path.startswith("/") else pdf_path

            yield {
                # Identifiers
                "businessId":   business_id,
                "siteId":       site_id,
                "detail_url":   DETAIL_PAGE_TPL.format(businessId=business_id, siteId=site_id),
                # Core metadata
                "title":        detail.get("title", ""),
                "summary":      detail.get("summary", ""),
                "publishDate":  (detail.get("publishDate") or detail.get("displayDate") or "")[:10],
                "displayDate":  detail.get("displayDate", ""),
                "effectiveFlag": detail.get("effectiveFlag", ""),
                # Document identifiers
                "docType":      detail.get("docType", ""),
                "docYear":      detail.get("docYear", ""),
                "docNo":        detail.get("docNo", ""),
                "indexNo":      detail.get("indexNo", ""),
                "genre":        detail.get("genre", ""),
                # Org info
                "agency":       attrs.get("agency", ""),
                "draftUnit":    attrs.get("draftUnit", ""),
                "openType":     attrs.get("openType", ""),
                "theme":        attrs.get("theme", ""),
                # Content
                "content":      content_text,
                "content_html": txt_html,
                "pdf_url":      pdf_url,
                # Related readings
                "relates":      detail.get("relates", []),
            }


def build_detail_urls(
    site_id_list: list[str] | None = None,
    max_pages: int = 10,
    min_year: int = 0,
) -> list[str]:
    """Convenience helper: return list of detail page URLs from the list API."""
    client = ShanghaiGovApiClient(site_id_list=site_id_list)
    urls = [
        DETAIL_PAGE_TPL.format(
            businessId=rec.get("businessId", ""),
            siteId=rec.get("siteId", "0001"),
        )
        for rec in client.iter_list_records(max_pages=max_pages, min_year=min_year)
    ]
    logger.info("Collected %d detail URLs.", len(urls))
    return urls
