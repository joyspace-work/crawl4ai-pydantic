from __future__ import annotations

import logging
import re
import json
import asyncio
import os
import time
import requests
from datetime import datetime
from typing import Any, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
from crawl4ai.extraction_strategy import LLMExtractionStrategy
from shanghai_policy_crawler.categories import infer_categories
from shanghai_policy_crawler.utils import utc_now_iso
from shanghai_policy_crawler.firecrawl_crawler import PolicyDetailSchema

logger = logging.getLogger(__name__)

class Crawl4AIPolicyCrawler:
    def __init__(
        self,
        city_filter: str = "上海市",
        min_year: int = 2024,
        keyword: str = "",
        cookie: Optional[str] = None,
        llm_provider: str = "meta-llama/llama-3.3-70b-instruct:free",
        llm_api_key: Optional[str] = None,
    ) -> None:
        self.city_filter = city_filter
        self.min_year = min_year
        self.keyword = keyword
        self.cookie = cookie
        self.date_re = re.compile(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})")
        
        # Load API keys
        self.llm_api_key = (
            llm_api_key 
            or os.getenv("OPENROUTER_API_KEY") 
            or os.getenv("OPENROUTER_KEY")
            or os.getenv("OPENAI_API_KEY")
        )
        self.llm_provider = llm_provider
        
        # Configure Browser settings with best practices (headless, undetected-like headers)
        self.browser_config = BrowserConfig(
            headless=True,
            verbose=False,
            headers={"Cookie": cookie} if cookie else None
        )

    def extract_links(self, list_url: str, list_item_selectors: Optional[List[str]] = None) -> Tuple[List[str], Optional[str]]:
        """
        Scrape a policy list page using AsyncWebCrawler.
        """
        try:
            return asyncio.run(self._extract_links_async(list_url, list_item_selectors))
        except Exception as e:
            logger.error("Crawl4AI failed to extract links from %s: %s", list_url, e)
            return [], None

    async def _extract_links_async(self, list_url: str, list_item_selectors: Optional[List[str]] = None) -> Tuple[List[str], Optional[str]]:
        logger.info("Crawl4AI harvesting links from list page: %s", list_url)
        next_page_url = None
        
        async with AsyncWebCrawler(config=self.browser_config) as crawler:
            run_config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS)
            result = await crawler.arun(url=list_url, config=run_config)
            
            if not result.success:
                logger.warning("Crawl4AI failed to load page: %s. Error: %s", list_url, result.error_message)
                return [], None
                
            html = result.html or ""
            if not html:
                return [], None
                
            soup = BeautifulSoup(html, "html.parser")
            links = []
            
            if list_item_selectors:
                for selector in list_item_selectors:
                    elements = soup.select(selector)
                    for el in elements:
                        href = el.get("href")
                        if href:
                            links.append(urljoin(list_url, href.strip()))
            else:
                for a in soup.find_all("a"):
                    href = a.get("href")
                    if href:
                        links.append(urljoin(list_url, href.strip()))
                        
            unique_links = list(dict.fromkeys(links))
            logger.info("Extracted %d links via Crawl4AI BeautifulSoup parsing", len(unique_links))
            
            # Find next page link in pagination
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

    def extract_detail(self, detail_url: str) -> Optional[dict[str, Any]]:
        """
        Scrape a single detail page. Uses Crawl4AI to fetch markdown, and OpenRouter to extract details.
        """
        try:
            return asyncio.run(self._extract_detail_async(detail_url))
        except Exception as e:
            logger.error("Crawl4AI failed to extract detail from %s: %s", detail_url, e)
            return None

    async def _extract_detail_async(self, detail_url: str) -> Optional[dict[str, Any]]:
        logger.info("Crawl4AI extracting detail from: %s", detail_url)
        
        # 1. Setup LLMExtractionStrategy if API Key is available
        if not self.llm_api_key:
            logger.warning("No LLM API Key provided. LLMExtractionStrategy requires an API key. Falling back to local/BS4 extraction.")
            run_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                delay_before_return_html=2.0
            )
        else:
            llm_config = LLMConfig(
                provider=self.llm_provider,
                api_token=self.llm_api_key,
                extra_args={
                    "temperature": 0.1,
                    "max_tokens": 4000
                }
            )
            extraction_strategy = LLMExtractionStrategy(
                llm_config=llm_config,
                schema=PolicyDetailSchema.model_json_schema(),
                extraction_type="schema",
                instruction="""请根据网页内容，提取出该政策的所有字段，并严格以符合 schema 结构的 JSON 输出。
特别注意：content 必须尽可能完整包含政策正文，publish_date 必须符合 YYYY-MM-DD 格式，若无发文日期则返回 null。""",
                input_format="markdown",
                apply_chunking=False
            )
            run_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                delay_before_return_html=2.0,
                extraction_strategy=extraction_strategy
            )
        
        async with AsyncWebCrawler(config=self.browser_config) as crawler:
            result = await crawler.arun(url=detail_url, config=run_config)
            
            if not result.success:
                logger.warning("Crawl4AI failed to extract details from: %s. Error: %s", detail_url, result.error_message)
                return None
                
            markdown = result.markdown or ""
            html = result.html or ""
            
            data = {}
            if self.llm_api_key and result.extracted_content:
                try:
                    parsed = json.loads(result.extracted_content)
                    if isinstance(parsed, list) and len(parsed) > 0:
                        data = parsed[0]
                    elif isinstance(parsed, dict):
                        data = parsed
                except Exception as e:
                    logger.error("Failed to parse extracted structured JSON: %s. Using regex parser fallback...", e)
                
            # 2. Defensive Fallback Parser (extract title, publish_date, etc., locally if LLM failed)
            title = data.get("title") or ""
            publish_date = data.get("publish_date") or ""
            source = data.get("source") or ""
            content = data.get("content") or ""
            gov_url = data.get("government_original_url") or ""
            
            soup = BeautifulSoup(html, "html.parser")
            
            if not title:
                # Find first H1 or title element or first line of markdown
                h1_el = soup.find("h1")
                title = h1_el.get_text().strip() if h1_el else ""
                if not title:
                    lines = [l.strip() for l in markdown.split("\n") if l.strip()]
                    title = lines[0] if lines else "无标题"
                    
            if not content:
                content = markdown or ""
                
            if not source:
                # Find common source containers
                source_el = soup.select_one(".source, .agency, .department")
                if source_el:
                    source = source_el.get_text().strip()
                    
            text_blob = f"{title}\n{source}\n{content}"
            
            # Apply city filter
            if not self._passes_city_filter(text_blob, url=detail_url):
                logger.info("Filtered by city (%s): %s", self.city_filter, detail_url)
                return None
                
            # Apply min year filter
            normalized_date = self._normalize_date(publish_date) or self._extract_date_from_text(text_blob)
            if not self._passes_min_year(normalized_date, text_blob):
                logger.info("Filtered by year (min_year=%d, found_date=%s): %s", self.min_year, normalized_date, detail_url)
                return None
                
            # Apply keyword filter
            if self.keyword and self.keyword not in text_blob:
                logger.info("Filtered by keyword (%s): %s", self.keyword, detail_url)
                return None
                
            # Infer categories
            category_labels, category_values = infer_categories(text_blob)
            crawled_at = utc_now_iso()
            
            record = {
                "title": title.strip(),
                "publish_date": normalized_date,
                "source": source.strip(),
                "government_original_url": gov_url.strip() if gov_url else "",
                "content": content,
                "url": detail_url,
                "attachments": [],
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
                "source_type": "crawl4ai",
                "source_site": urlparse(detail_url).netloc,
                
                # LLM Extracted Fields
                "policy_object": data.get("policy_object") or "见正文",
                "policy_conditions": data.get("policy_conditions") or "见正文",
                "payment_standard": data.get("payment_standard") or "见正文",
                "contact_information": data.get("contact_information") or "见正文",
                "application_period": data.get("application_period") or "未明确",
                "payment_method": data.get("payment_method") or "奖励/补贴",
                "document_number": data.get("document_number"),
            }
            return record

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
