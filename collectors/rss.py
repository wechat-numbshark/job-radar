from __future__ import annotations
import re
import aiohttp
import feedparser
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from collectors.base import BaseJobCollector, RawJob
from config.settings import settings


# 从 Indeed 职位URL中提取公司/地点的简单正则
_SALARY_RE = re.compile(
    r"(\$[\d,]+(?:\s*[-–]\s*\$[\d,]+)?(?:\s*[KkMm])?(?:/\s*(?:yr|year|mo|month|hr|hour))?|"
    r"£[\d,]+(?:\s*[-–]\s*£[\d,]+)?(?:\s*[KkMm])?|"
    r"€[\d,]+(?:\s*[-–]\s*€[\d,]+)?(?:\s*[KkMm])?|"
    r"[\d,]+\s*万円|¥[\d,]+)",
    re.IGNORECASE,
)


class RSSJobCollector(BaseJobCollector):
    def __init__(self, source_config: dict):
        super().__init__(source_config)
        self.url = source_config["url"]
        self.timeout = aiohttp.ClientTimeout(total=30)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def fetch(self) -> list[RawJob]:
        proxy = settings.http_proxy or None
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }

        try:
            async with aiohttp.ClientSession(headers=headers, timeout=self.timeout) as session:
                async with session.get(self.url, proxy=proxy, ssl=False) as resp:
                    if resp.status != 200:
                        logger.warning(f"[{self.source_id}] HTTP {resp.status}: {self.url}")
                        return []
                    content = await resp.read()
        except Exception as e:
            logger.error(f"[{self.source_id}] 请求失败: {e}")
            return []

        feed = feedparser.parse(content)
        if feed.bozo and not feed.entries:
            logger.warning(f"[{self.source_id}] RSS解析失败")
            return []

        jobs = []
        for entry in feed.entries:
            title = self._clean_entry_title(getattr(entry, "title", ""))
            link  = getattr(entry, "link", "") or getattr(entry, "id", "")
            if not title or not link:
                continue

            description_html = (
                getattr(entry, "summary", "")
                or getattr(entry, "description", "")
                or getattr(entry, "content", [{}])[0].get("value", "") if hasattr(entry, "content") else ""
            )
            description = self.clean_html(description_html)

            company, location = self._extract_company_location(entry, description_html)
            salary = self._extract_salary(description)
            pub_at = self._parse_date(entry)

            jobs.append(RawJob(
                title=title,
                url=link,
                company=company,
                location=location,
                country=self.country,
                salary=salary,
                description=self.truncate(description, 2500),
                source_id=self.source_id,
                source_name=self.source_name,
                published_at=pub_at,
            ))

        logger.info(f"[{self.source_id}] 采集 {len(jobs)} 条")
        return jobs

    def _clean_entry_title(self, title: str) -> str:
        title = re.sub(r"<[^>]+>", "", title)
        title = re.sub(r"\s+", " ", title).strip()
        return title

    def _extract_company_location(self, entry, desc_html: str) -> tuple[str, str]:
        company = ""
        location = ""

        # feedparser 有时在 author 或 tags 里有公司名
        if hasattr(entry, "author") and entry.author:
            company = entry.author

        # Indeed 格式: "Job Title - Company - City, State"
        title = getattr(entry, "title", "")
        parts = [p.strip() for p in title.split(" - ")]
        if len(parts) >= 3:
            company = company or parts[1]
            location = parts[-1]
        elif len(parts) == 2:
            company = company or parts[1]

        # 从HTML提取location标签
        if not location:
            m = re.search(r"location[:\s]+([^<\n]{3,50})", desc_html, re.IGNORECASE)
            if m:
                location = m.group(1).strip()

        return company[:100], location[:100]

    def _extract_salary(self, text: str) -> str:
        m = _SALARY_RE.search(text)
        return m.group(0).strip() if m else ""

    def _parse_date(self, entry) -> datetime | None:
        for attr in ("published", "updated", "created"):
            val = getattr(entry, attr, None)
            if val:
                try:
                    return parsedate_to_datetime(val).astimezone(timezone.utc).replace(tzinfo=timezone.utc)
                except Exception:
                    pass
        return None
