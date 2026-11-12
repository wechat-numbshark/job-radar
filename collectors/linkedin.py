from __future__ import annotations
"""
LinkedIn 访客接口采集器（无需登录）。
通过 LinkedIn jobs-guest API 搜索，解析 HTML 提取职位列表。
每个搜索组合翻 3 页（30条），多关键词并发。
"""
import re
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from collectors.base import BaseJobCollector, RawJob
from config.settings import settings

LI_BASE = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    "?keywords={keywords}&location={location}&f_TPR=r{tpr}&start={start}"
)

# 最近3天
TPR = 259200

SEARCHES = [
    # (keywords, location, country)
    # ── 美国 ────────────────────────────────────────────────
    ("AI Solutions Engineer", "United States", "US"),
    ("Forward Deployment Engineer", "United States", "US"),
    ("AI Product Manager", "United States", "US"),
    ("Technical Program Manager AI", "United States", "US"),
    ("AI Implementation Consultant", "United States", "US"),
    ("Technical Account Manager AI", "United States", "US"),
    # ── 英国 ────────────────────────────────────────────────
    ("AI Solutions Engineer", "United Kingdom", "UK"),
    ("AI Product Manager", "United Kingdom", "UK"),
    ("AI Consultant", "London", "UK"),
    ("Technical Account Manager AI", "United Kingdom", "UK"),
    ("Forward Deployment Engineer", "United Kingdom", "UK"),
    # ── 加拿大 ──────────────────────────────────────────────
    ("AI Solutions Engineer", "Canada", "CA"),
    ("AI Product Manager", "Canada", "CA"),
    ("Technical Account Manager AI", "Canada", "CA"),
    # ── 澳大利亚 ────────────────────────────────────────────
    ("AI Solutions Engineer", "Australia", "AU"),
    ("AI Consultant", "Sydney", "AU"),
    ("Technical Account Manager", "Melbourne", "AU"),
    ("AI Product Manager", "Australia", "AU"),
    ("Forward Deployment Engineer", "Australia", "AU"),
    # ── 新西兰 ──────────────────────────────────────────────
    ("AI Solutions Engineer", "New Zealand", "NZ"),
    ("Technical Account Manager AI", "Auckland", "NZ"),
    ("AI Consultant", "Wellington", "NZ"),
    # ── 新加坡 ──────────────────────────────────────────────
    ("AI Solutions Engineer", "Singapore", "SG"),
    ("AI Project Manager", "Singapore", "SG"),
    ("AI Solutions Architect", "Singapore", "SG"),
    ("Technical Account Manager AI", "Singapore", "SG"),
    # ── 日本 ────────────────────────────────────────────────
    ("AI Engineer", "Japan", "JP"),
    ("Solutions Engineer AI", "Japan", "JP"),
    ("Technical Account Manager", "Tokyo", "JP"),
    ("AI Consultant", "Tokyo", "JP"),
    ("Solutions Architect AI", "Japan", "JP"),
    ("AI Implementation", "Japan", "JP"),
    # ── 德国 ────────────────────────────────────────────────
    ("AI Solutions Engineer", "Germany", "DE"),
    ("AI Consultant", "Berlin", "DE"),
    ("Technical Account Manager AI", "Germany", "DE"),
    # ── 荷兰 ────────────────────────────────────────────────
    ("AI Solutions Engineer", "Netherlands", "NL"),
    ("Technical Account Manager AI", "Amsterdam", "NL"),
    # ── 爱尔兰 ──────────────────────────────────────────────
    ("AI Solutions Engineer", "Ireland", "IE"),
    ("AI Consultant", "Dublin", "IE"),
    # ── 法国 ────────────────────────────────────────────────
    ("AI Solutions Engineer", "France", "FR"),
    ("AI Consultant", "Paris", "FR"),
    # ── 北欧 ────────────────────────────────────────────────
    ("AI Solutions Engineer", "Sweden", "SE"),
    ("AI Solutions Engineer", "Switzerland", "CH"),
]

PAGES_PER_SEARCH = 2   # 每个搜索翻 2 页 = 20条
DELAY_BETWEEN_PAGES = 1.5


class LinkedInCollector(BaseJobCollector):
    """采集 LinkedIn 访客 Jobs API（多搜索词、多国家、多页）"""

    def __init__(self):
        super().__init__({"id": "linkedin", "name": "LinkedIn Guest", "country": "MULTI"})
        self.timeout = aiohttp.ClientTimeout(total=30)
        self._seen_urls: set[str] = set()

    async def fetch(self) -> list[RawJob]:
        proxy = settings.http_proxy or None
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.linkedin.com/jobs/",
        }

        all_jobs: list[RawJob] = []
        sem = asyncio.Semaphore(3)

        async def _fetch_search(keywords: str, location: str, country: str):
            search_jobs = []
            for page in range(PAGES_PER_SEARCH):
                start = page * 10
                url = LI_BASE.format(
                    keywords=keywords.replace(" ", "+"),
                    location=location.replace(" ", "+"),
                    tpr=TPR,
                    start=start,
                )
                async with sem:
                    jobs = await self._fetch_page(url, country, headers, proxy)
                search_jobs.extend(jobs)
                if len(jobs) < 10:
                    break
                await asyncio.sleep(DELAY_BETWEEN_PAGES)
            return search_jobs

        tasks = [_fetch_search(kw, loc, country) for kw, loc, country in SEARCHES]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                for j in r:
                    if j.url not in self._seen_urls:
                        self._seen_urls.add(j.url)
                        all_jobs.append(j)

        logger.info(f"[linkedin] 共采集 {len(all_jobs)} 条（去重后）")
        return all_jobs

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=2, max=8))
    async def _fetch_page(
        self, url: str, country: str, headers: dict, proxy: str | None
    ) -> list[RawJob]:
        try:
            async with aiohttp.ClientSession(headers=headers, timeout=self.timeout) as session:
                async with session.get(url, proxy=proxy, ssl=False) as resp:
                    if resp.status != 200:
                        return []
                    html = await resp.text()
        except Exception as e:
            logger.debug(f"[linkedin] 请求失败: {e}")
            return []

        return self._parse_html(html, country)

    def _parse_html(self, html: str, country: str) -> list[RawJob]:
        soup = BeautifulSoup(html, "lxml")
        jobs = []
        for card in soup.find_all("li"):
            title_el = card.find("h3", class_=re.compile(r"base-search-card__title"))
            company_el = card.find("h4", class_=re.compile(r"base-search-card__subtitle"))
            loc_el = card.find("span", class_=re.compile(r"job-search-card__location"))
            link_el = card.find("a", class_=re.compile(r"base-card__full-link"))
            time_el = card.find("time")

            title = title_el.get_text(strip=True) if title_el else ""
            company = company_el.get_text(strip=True) if company_el else ""
            location = loc_el.get_text(strip=True) if loc_el else ""
            link = link_el.get("href", "").split("?")[0] if link_el else ""

            if not title or not link:
                continue

            pub_at = None
            if time_el and time_el.get("datetime"):
                try:
                    pub_at = datetime.fromisoformat(
                        time_el["datetime"].replace("Z", "+00:00")
                    ).astimezone(timezone.utc)
                except Exception:
                    pass

            jobs.append(RawJob(
                title=title,
                url=link,
                company=company,
                location=location,
                country=country,
                salary="",
                description="",
                source_id="linkedin",
                source_name="LinkedIn",
                published_at=pub_at,
            ))
        return jobs
