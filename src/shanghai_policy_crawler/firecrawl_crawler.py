from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from firecrawl import FirecrawlApp
from pydantic import BaseModel, Field

from shanghai_policy_crawler.categories import infer_categories
from shanghai_policy_crawler.utils import utc_now_iso

logger = logging.getLogger(__name__)


class PolicyDetailSchema(BaseModel):
    title: str = Field(description="政策的标题名称")
    publish_date: Optional[str] = Field(description="政策的发布时间/发文日期，格式必须为 YYYY-MM-DD。若无明确日期，可从正文提取最可能的发文年份和月份并格式化。若实在没有，返回 null")
    source: Optional[str] = Field(description="政策的发布主管机构或发文部门")
    document_number: Optional[str] = Field(description="政策的发文字号或文号，如：国发〔2024〕1号。若无，返回 null")
    government_original_url: Optional[str] = Field(description="指向官方政府网站政策原文的链接或来源网页URL，若无，返回 null")
    policy_object: Optional[str] = Field(description="政策适用对象或什么类型的企业可以申报。若无，返回 null")
    policy_conditions: Optional[str] = Field(description="申报条件或满足什么条件可以申请。若无，返回 null")
    payment_standard: Optional[str] = Field(description="补贴金额、扶持比例、税惠减免或固定额度标准。若无，返回 null")
    contact_information: Optional[str] = Field(description="联系电话、地址或部门咨询方式")
    application_period: Optional[str] = Field(description="政策的有效期、申报期限或持续时间")
    payment_method: Optional[str] = Field(description="兑付或补贴方式（如前补贴、后补贴、税收优惠、资质奖励、荣誉奖励等）")
    content: str = Field(description="政策的完整正文内容，尽量保留原意和核心条款段落")


class PolicyListSchema(BaseModel):
    urls: List[str] = Field(description="Extracted absolute links/URLs leading to policy detail pages or article pages from the listing page.")


class FirecrawlPolicyCrawler:
    def __init__(
        self,
        api_key: str,
        api_url: Optional[str] = None,
        city_filter: str = "上海市",
        min_year: int = 2024,
        keyword: str = "",
        cookie: Optional[str] = None,
    ) -> None:
        if api_url:
            self.app = FirecrawlApp(api_key=api_key, api_url=api_url)
        else:
            self.app = FirecrawlApp(api_key=api_key)
        self.city_filter = city_filter
        self.min_year = min_year
        self.keyword = keyword
        self.headers = {"Cookie": cookie} if cookie else None
        self.date_re = re.compile(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})")

    def extract_links(self, list_url: str, list_item_selectors: Optional[List[str]] = None) -> Tuple[List[str], Optional[str]]:
        """
        Scrape a policy list page. If list_item_selectors are provided, uses BeautifulSoup to parse links.
        Otherwise, uses Firecrawl LLM structured extraction to fetch links.
        Returns a tuple: (list_of_detail_urls, next_page_url)
        """
        logger.info("Scraping list page: %s", list_url)
        next_page_url = None
        
        # Use CSS selectors and BeautifulSoup if provided
        if list_item_selectors:
            try:
                # Setup scroll actions to trigger infinite scroll for lazy-loaded items
                scroll_actions = []
                for _ in range(5):
                    scroll_actions.append({"type": "scroll", "direction": "down", "amount": 2000})
                    scroll_actions.append({"type": "wait", "milliseconds": 2000})

                response = self.app.scrape(
                    list_url,
                    formats=["html"],
                    headers=self.headers,
                    actions=scroll_actions
                )
                html = getattr(response, "html", "") or ""
                if not html:
                    logger.warning("No HTML returned from Firecrawl for list: %s", list_url)
                    return [], None
                
                soup = BeautifulSoup(html, "html.parser")
                links = []
                for selector in list_item_selectors:
                    elements = soup.select(selector)
                    for el in elements:
                        href = el.get("href")
                        if href:
                            links.append(urljoin(list_url, href.strip()))
                
                unique_links = list(dict.fromkeys(links))
                logger.info("Extracted %d links via CSS selectors", len(unique_links))
                
                # Try to find next page link
                next_selectors = [
                    "a[rel='next']", "a.next", ".pagination .next",
                    ".pagination a.next-page", "li.next a",
                    "a[aria-label='Next']", "a[aria-label*='next']"
                ]
                for sel in next_selectors:
                    el = soup.select_one(sel)
                    if el and el.get("href"):
                        next_page_url = urljoin(list_url, el.get("href").strip())
                        break
                
                if not next_page_url:
                    for a in soup.find_all("a"):
                        text = a.get_text() or ""
                        if "下一页" in text or "Next" in text or "next" in text.lower():
                            href = a.get("href")
                            if href:
                                next_page_url = urljoin(list_url, href.strip())
                                break
                                
                return unique_links, next_page_url
            except Exception as e:
                logger.error("Failed to extract links via CSS selectors: %s, falling back to LLM extraction", e)

        # Fallback to LLM structured extraction
        try:
            logger.info("Using Firecrawl LLM extraction to find policy links on: %s", list_url)
            response = self.app.scrape(
                list_url,
                formats=[
                    {
                        "type": "json",
                        "schema": PolicyListSchema.model_json_schema()
                    }
                ],
                only_main_content=True,
                headers=self.headers
            )
            data = getattr(response, "json", {}) or {}
            urls = data.get("urls", []) if isinstance(data, dict) else []
            # Normalize URLs
            normalized = []
            for u in urls:
                if u and isinstance(u, str):
                    normalized.append(urljoin(list_url, u.strip()))
            
            unique_links = list(dict.fromkeys(normalized))
            logger.info("Extracted %d links via Firecrawl LLM extraction", len(unique_links))
            
            # Try to increment page query parameter as fallback for LLM mode
            from urllib.parse import parse_qs, urlencode, urlunparse
            parsed = urlparse(list_url)
            query = parse_qs(parsed.query)
            incremented = False
            for key in ["page", "p", "pageNo", "page_no", "pageindex", "page_index"]:
                for k in query.keys():
                    if k.lower() == key.lower() and query[k]:
                        try:
                            val = int(query[k][0])
                            query[k] = [str(val + 1)]
                            new_query = urlencode(query, doseq=True)
                            next_page_url = urlunparse(parsed._replace(query=new_query))
                            incremented = True
                            break
                        except ValueError:
                            pass
                if incremented:
                    break
            
            return unique_links, next_page_url
        except Exception as e:
            logger.error("Failed to extract links via Firecrawl LLM extraction: %s", e)
            return [], None

    def extract_detail(self, detail_url: str) -> Optional[dict[str, Any]]:
        """
        Scrape a single detail page using Firecrawl LLM extraction.
        """
        logger.info("Scraping detail page: %s", detail_url)
        try:
            response = self.app.scrape(
                detail_url,
                formats=[
                    {
                        "type": "json",
                        "schema": PolicyDetailSchema.model_json_schema()
                    }
                ],
                only_main_content=True,
                headers=self.headers
            )
            
            data = getattr(response, "json", {}) or {}
            if not data or not isinstance(data, dict):
                logger.warning("No structured data returned for detail page: %s", detail_url)
                return None
            
            # Post-process and filter
            title = data.get("title") or ""
            publish_date = data.get("publish_date") or ""
            source = data.get("source") or ""
            content = data.get("content") or ""
            gov_url = data.get("government_original_url") or ""

            # Guard: if content is completely empty, Firecrawl likely failed to render the SPA
            if not title and not content:
                logger.warning("Empty content returned (SPA render failure?): %s", detail_url)
                return None

            text_blob = f"{title}\n{source}\n{content}"
            
            # 1. Apply city filter (pass URL for domain auto-pass logic)
            if not self._passes_city_filter(text_blob, url=detail_url):
                logger.info("Filtered by city (%s): %s", self.city_filter, detail_url)
                return None
            
            # 2. Apply min year filter
            normalized_date = self._normalize_date(publish_date) or self._extract_date_from_text(text_blob)
            if not self._passes_min_year(normalized_date, text_blob):
                logger.info("Filtered by year (min_year=%d, found_date=%s): %s", self.min_year, normalized_date, detail_url)
                return None
            
            # 3. Apply keyword filter
            if self.keyword and self.keyword not in text_blob:
                logger.info("Filtered by keyword (%s): %s", self.keyword, detail_url)
                return None
            
            # Infer categories
            category_labels, category_values = infer_categories(text_blob)
            crawled_at = utc_now_iso()
            
            # Build unified compatible structure
            record = {
                "title": title.strip(),
                "publish_date": normalized_date,
                "source": source.strip(),
                "government_original_url": gov_url.strip() if gov_url else "",
                "content": content,
                "url": detail_url,
                "attachments": [],  # LLM scrape does not easily isolate attachments separately unless requested, we default to empty
                "crawled_at": crawled_at,
                "keyword": self.keyword,
                
                # Compatibility fields
                "source_url": detail_url,
                "content_text": content,
                "content_markdown": content,
                "scraped_at": crawled_at,
                "city": self.city_filter,
                "category_labels": category_labels,
                "category_values": category_values,
                "source_type": "firecrawl",
                "source_site": urlparse(detail_url).netloc,
                
                # LLM Extracted Fields
                "policy_object": data.get("policy_object"),
                "policy_conditions": data.get("policy_conditions"),
                "payment_standard": data.get("payment_standard"),
                "contact_information": data.get("contact_information"),
                "application_period": data.get("application_period"),
                "payment_method": data.get("payment_method"),
                "document_number": data.get("document_number"),
            }
            return record
        except Exception as e:
            logger.error("Failed to extract details from %s: %s", detail_url, e)
            return None

    def _passes_city_filter(self, text: str, url: str = "") -> bool:
        if not self.city_filter:
            return True
        # Auto-pass for known Shanghai government domains — these are definitionally Shanghai
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
        years = [int(year) for year in re.findall(r"20\d{2}", text or "")]
        return bool(years) and max(years) >= self.min_year

    def _extract_date_from_text(self, text: str) -> str:
        match = self.date_re.search(text or "")
        if not match:
            return ""
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    def _normalize_date(self, value: str) -> str:
        if not value:
            return ""
        return self._extract_date_from_text(value)
