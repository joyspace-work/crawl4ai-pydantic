"""
crawl4ai_crawler.py — Browser-rendered page crawler using crawl4ai.

Used ONLY when the REST API is unavailable (e.g., SPA pages that require JS rendering).
Extracts structured data via CSS selectors + BeautifulSoup; no LLM calls.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

from shanghai_policy_crawler.categories import infer_categories
from shanghai_policy_crawler.utils import utc_now_iso

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CSS selector maps — tuples of (field_name, css_selector, attribute_or_None)
# None attribute means use .get_text().strip()
# ---------------------------------------------------------------------------
LIST_ITEM_SELECTORS: list[str] = [
    "ul.list-ul li a",
    "ul.news-list li a",
    ".policy-list a",
    ".article-list a",
    "table.list-table td a",
    "li.list-item a",
    ".content-list li a",
]

NEXT_PAGE_SELECTORS: list[str] = [
    "a[rel='next']",
    "a.next",
    ".pagination .next",
    ".pagination a.next-page",
    "li.next a",
    "a[aria-label='Next']",
]

# Field-level selectors for detail pages; first match wins.
DETAIL_FIELD_SELECTORS: dict[str, list[str]] = {
    "title": [
        "h1.article-title",
        "h1.policy-title",
        "h1",
        ".title",
        "title",
    ],
    "publish_date": [
        ".publish-date",
        ".release-date",
        ".date",
        "span.time",
        "time",
        "meta[name='publish_date']",
        "meta[property='article:published_time']",
    ],
    "source": [
        ".source-org",
        ".department",
        ".agency",
        ".origin",
        "meta[name='author']",
    ],
    "document_number": [
        ".doc-number",
        ".document-no",
        ".wen-hao",
        ".file-number",
    ],
    "content": [
        "article",
        ".article-content",
        ".policy-content",
        ".content-area",
        ".main-content",
        "main",
        "#main-content",
    ],
}


class Crawl4AIPolicyCrawler:
    """
    Fallback crawler for pages that require browser rendering.
    API-based crawlers (e.g. ShanghaiGovApiClient) should always be tried first.
    """

    def __init__(
        self,
        city_filter: str = "上海市",
        min_year: int = 2024,
        keyword: str = "",
        cookie: Optional[str] = None,
    ) -> None:
        self.city_filter = city_filter
        self.min_year = min_year
        self.keyword = keyword
        self.date_re = re.compile(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})")

        self.browser_config = BrowserConfig(
            headless=True,
            verbose=False,
            headers={"Cookie": cookie} if cookie else None,
        )

    # ------------------------------------------------------------------
    # Public sync wrappers (crawlers.py calls these from sync context)
    # ------------------------------------------------------------------

    def extract_links(
        self,
        list_url: str,
        list_item_selectors: Optional[list[str]] = None,
    ) -> tuple[list[str], Optional[str]]:
        try:
            return asyncio.run(
                self._extract_links_async(list_url, list_item_selectors)
            )
        except Exception as exc:
            logger.error("Crawl4AI failed to extract links from %s: %s", list_url, exc)
            return [], None

    def extract_detail(self, detail_url: str) -> Optional[dict[str, Any]]:
        try:
            return asyncio.run(self._extract_detail_async(detail_url))
        except Exception as exc:
            logger.error("Crawl4AI failed to extract detail from %s: %s", detail_url, exc)
            return None

    # ------------------------------------------------------------------
    # Async implementation — list page
    # ------------------------------------------------------------------

    async def _extract_links_async(
        self,
        list_url: str,
        extra_selectors: Optional[list[str]] = None,
    ) -> tuple[list[str], Optional[str]]:
        logger.info("Crawl4AI harvesting links from: %s", list_url)

        async with AsyncWebCrawler(config=self.browser_config) as crawler:
            result = await crawler.arun(
                url=list_url,
                config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS),
            )

        if not result.success:
            logger.warning("Crawl4AI failed: %s — %s", list_url, result.error_message)
            return [], None

        html = result.html or ""
        if not html:
            return [], None

        soup = BeautifulSoup(html, "html.parser")
        selectors = (extra_selectors or []) + LIST_ITEM_SELECTORS
        links: list[str] = []

        for selector in selectors:
            elements = soup.select(selector)
            for el in elements:
                href = el.get("href")
                if href:
                    links.append(urljoin(list_url, str(href).strip()))
            if links:
                break  # first matching selector wins

        # fallback: collect ALL anchors
        if not links:
            for a in soup.find_all("a", href=True):
                links.append(urljoin(list_url, str(a["href"]).strip()))

        unique_links = list(dict.fromkeys(links))
        logger.info("Extracted %d links via Crawl4AI CSS selectors", len(unique_links))

        # --- next page ---
        next_page_url: Optional[str] = None
        for sel in NEXT_PAGE_SELECTORS:
            el = soup.select_one(sel)
            if el and el.get("href"):
                next_page_url = urljoin(list_url, str(el["href"]).strip())
                break
        if not next_page_url:
            for a in soup.find_all("a", href=True):
                text = a.get_text() or ""
                if "下一页" in text or text.strip().lower() in ("next", ">"):
                    next_page_url = urljoin(list_url, str(a["href"]).strip())
                    break

        return unique_links, next_page_url

    # ------------------------------------------------------------------
    # Async implementation — detail page (CSS selector only, no LLM)
    # ------------------------------------------------------------------

    async def _extract_detail_async(
        self, detail_url: str
    ) -> Optional[dict[str, Any]]:
        logger.info("Crawl4AI extracting detail from: %s", detail_url)

        async with AsyncWebCrawler(config=self.browser_config) as crawler:
            result = await crawler.arun(
                url=detail_url,
                config=CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS,
                    delay_before_return_html=1.5,
                ),
            )

        if not result.success:
            logger.warning(
                "Crawl4AI failed to load detail: %s — %s",
                detail_url,
                result.error_message,
            )
            return None

        html = result.html or ""
        markdown = result.markdown or ""

        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")

        title = self._select_text(soup, DETAIL_FIELD_SELECTORS["title"]) or ""
        if not title:
            # fallback to first non-empty line of markdown
            lines = [ln.strip() for ln in markdown.splitlines() if ln.strip()]
            title = lines[0] if lines else "无标题"

        publish_date_raw = self._select_meta_or_text(
            soup, DETAIL_FIELD_SELECTORS["publish_date"]
        )
        publish_date = self._normalize_date(publish_date_raw)

        source = self._select_meta_or_text(soup, DETAIL_FIELD_SELECTORS["source"]) or ""
        document_number = self._select_text(soup, DETAIL_FIELD_SELECTORS["document_number"]) or ""

        content_el = self._select_element(soup, DETAIL_FIELD_SELECTORS["content"])
        content = content_el.get_text("\n", strip=True) if content_el else markdown

        text_blob = f"{title}\n{source}\n{content}"

        # --- filters ---
        if not self._passes_city_filter(text_blob, url=detail_url):
            logger.info("Filtered by city (%s): %s", self.city_filter, detail_url)
            return None

        if not publish_date:
            publish_date = self._extract_date_from_text(text_blob)

        if not self._passes_min_year(publish_date, text_blob):
            logger.info(
                "Filtered by year (min=%d, found=%s): %s",
                self.min_year,
                publish_date,
                detail_url,
            )
            return None

        if self.keyword and self.keyword not in text_blob:
            logger.info("Filtered by keyword (%s): %s", self.keyword, detail_url)
            return None

        category_labels, category_values = infer_categories(text_blob)
        crawled_at = utc_now_iso()

        return {
            "title": title.strip(),
            "publish_date": publish_date,
            "source": source.strip(),
            "document_number": document_number.strip(),
            "content": content,
            "url": detail_url,
            "source_url": detail_url,
            "attachments": [],
            "crawled_at": crawled_at,
            "scraped_at": crawled_at,
            "keyword": self.keyword,
            "city": self.city_filter,
            "category_labels": category_labels,
            "category_values": category_values,
            "source_type": "crawl4ai",
            "source_site": urlparse(detail_url).netloc,
            "government_original_url": "",
        }

    # ------------------------------------------------------------------
    # CSS selector helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _select_element(soup: BeautifulSoup, selectors: list[str]) -> Optional[Tag]:
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                return el
        return None

    @staticmethod
    def _select_text(soup: BeautifulSoup, selectors: list[str]) -> str:
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if text:
                    return text
        return ""

    @staticmethod
    def _select_meta_or_text(soup: BeautifulSoup, selectors: list[str]) -> str:
        """Handles both <meta> (content attr) and regular elements (text)."""
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                if el.name == "meta":
                    val = el.get("content", "")
                    if val:
                        return str(val).strip()
                else:
                    text = el.get_text(strip=True)
                    if text:
                        return text
        return ""

    # ------------------------------------------------------------------
    # Filter / normalise helpers
    # ------------------------------------------------------------------

    def _passes_city_filter(self, text: str, url: str = "") -> bool:
        if not self.city_filter:
            return True
        known_sh_domains = ("shanghai.gov.cn", "zwdt.sh.gov.cn", "sh.gov.cn")
        if any(d in url for d in known_sh_domains):
            return True
        aliases = {self.city_filter, self.city_filter.replace("市", "")}
        if "上海" in self.city_filter or "沪" in self.city_filter:
            aliases.update({"沪", "浦东", "临港", "本市", "全市"})
        return any(alias and alias in text for alias in aliases)

    def _passes_min_year(self, publish_date: str, text: str) -> bool:
        if not self.min_year:
            return True
        match = re.search(r"\d{4}", publish_date or "")
        if match:
            return int(match.group(0)) >= self.min_year
        years = [int(y) for y in re.findall(r"20\d{2}", text or "")]
        return bool(years) and max(years) >= self.min_year

    def _extract_date_from_text(self, text: str) -> str:
        m = self.date_re.search(text or "")
        if not m:
            return ""
        year, month, day = m.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    def _normalize_date(self, value: str) -> str:
        return self._extract_date_from_text(value) if value else ""
