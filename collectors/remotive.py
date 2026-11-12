from __future__ import annotations
"""
Remotive.com JSON API 采集器
无需 API Key，返回全球远程职位，含薪资字段
https://remotive.com/api/remote-jobs
"""
import aiohttp
from datetime import datetime, timezone
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from collectors.base import BaseJobCollector, RawJob
from config.settings import settings

REMOTIVE_API = "https://remotive.com/api/remote-jobs"

# 拉取的职位分类
CATEGORIES = [
    "software-dev",
    "product",
    "management-finance",
    "devops-sysadmin",
]


class RemotiveCollector(BaseJobCollector):
    def __init__(self):
        super().__init__({"id": "remotive", "name": "Remotive", "country": "REMOTE"})
        self.timeout = aiohttp.ClientTimeout(total=30)

    async def fetch(self) -> list[RawJob]:
        all_jobs: list[RawJob] = []
        seen_urls: set[str] = set()

        for cat in CATEGORIES:
            try:
                jobs = await self._fetch_category(cat)
                for j in jobs:
                    if j.url not in seen_urls:
                        seen_urls.add(j.url)
                        all_jobs.append(j)
            except Exception as e:
                logger.error(f"[remotive] 分类 {cat} 失败: {e}")

        logger.info(f"[remotive] 共采集 {len(all_jobs)} 条")
        return all_jobs

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def _fetch_category(self, category: str) -> list[RawJob]:
        proxy = settings.http_proxy or None
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; JobRadar/1.0)",
            "Accept": "application/json",
        }

        async with aiohttp.ClientSession(headers=headers, timeout=self.timeout) as session:
            async with session.get(
                REMOTIVE_API,
                params={"category": category, "limit": 100},
                proxy=proxy,
                ssl=False,
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[remotive] HTTP {resp.status} for {category}")
                    return []
                data = await resp.json()

        jobs_raw = data.get("jobs", [])
        jobs: list[RawJob] = []

        for item in jobs_raw:
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            if not title or not url:
                continue

            company = (item.get("company_name") or "").strip()
            location = (item.get("candidate_required_location") or "REMOTE").strip()
            salary = (item.get("salary") or "").strip()
            desc_html = item.get("description") or ""
            description = self.clean_html(desc_html)

            pub_str = item.get("publication_date") or ""
            pub_at = None
            if pub_str:
                try:
                    pub_at = datetime.fromisoformat(pub_str.replace("Z", "+00:00")).astimezone(timezone.utc)
                except Exception:
                    pass

            # 国家推断：location 字段有时包含国家名
            country = _infer_country(location)

            jobs.append(RawJob(
                title=title,
                url=url,
                company=company,
                location=location,
                country=country,
                salary=salary,
                description=self.truncate(description, 2500),
                source_id="remotive",
                source_name="Remotive",
                published_at=pub_at,
            ))

        return jobs


def _infer_country(location: str) -> str:
    loc = location.upper()
    if any(x in loc for x in ["USA", "UNITED STATES", "US ONLY", "US,"]):
        return "US"
    if any(x in loc for x in ["UK", "UNITED KINGDOM", "ENGLAND", "LONDON"]):
        return "UK"
    if "CANADA" in loc:
        return "CA"
    if any(x in loc for x in ["AUSTRALIA", "SYDNEY", "MELBOURNE"]):
        return "AU"
    if any(x in loc for x in ["SINGAPORE"]):
        return "SG"
    if any(x in loc for x in ["JAPAN", "TOKYO"]):
        return "JP"
    if any(x in loc for x in ["GERMANY", "BERLIN", "MUNICH", "DEUTSCHLAND"]):
        return "DE"
    if any(x in loc for x in ["NETHERLANDS", "AMSTERDAM"]):
        return "NL"
    if any(x in loc for x in ["FRANCE", "PARIS"]):
        return "FR"
    if any(x in loc for x in ["IRELAND", "DUBLIN"]):
        return "IE"
    if any(x in loc for x in ["SWEDEN", "STOCKHOLM"]):
        return "SE"
    if any(x in loc for x in ["NEW ZEALAND", "AUCKLAND"]):
        return "NZ"
    return "REMOTE"
