from __future__ import annotations
"""
Lever ATS 公开 API 采集器。
访问：https://api.lever.co/v0/postings/{slug}?mode=json
"""
import aiohttp
from datetime import datetime, timezone
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from collectors.base import BaseJobCollector, RawJob
from config.settings import settings

LEVER_API = "https://api.lever.co/v0/postings/{slug}?mode=json"


class LeverCollector(BaseJobCollector):
    def __init__(self, company_config: dict):
        super().__init__({
            "id": f"lever_{company_config['slug']}",
            "name": f"Lever - {company_config['name']}",
            "country": company_config.get("country", "US"),
        })
        self.slug = company_config["slug"]
        self.company_name = company_config["name"]
        self.timeout = aiohttp.ClientTimeout(total=20)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
    async def fetch(self) -> list[RawJob]:
        url = LEVER_API.format(slug=self.slug)
        proxy = settings.http_proxy or None
        headers = {"User-Agent": "Mozilla/5.0 JobRadar/1.0"}

        try:
            async with aiohttp.ClientSession(headers=headers, timeout=self.timeout) as session:
                async with session.get(url, proxy=proxy, ssl=False) as resp:
                    if resp.status == 404:
                        logger.warning(f"[lever/{self.slug}] 404")
                        return []
                    if resp.status != 200:
                        logger.warning(f"[lever/{self.slug}] HTTP {resp.status}")
                        return []
                    data = await resp.json()
        except Exception as e:
            logger.error(f"[lever/{self.slug}] 请求失败: {e}")
            return []

        jobs = []
        for j in data:
            title = j.get("text", "").strip()
            job_url = j.get("hostedUrl", "") or j.get("applyUrl", "")
            if not title or not job_url:
                continue

            cats = j.get("categories", {})
            location = cats.get("location", "") or cats.get("allLocations", [""])[0] if isinstance(cats.get("allLocations"), list) else ""
            team = cats.get("team", "")
            country_raw = j.get("country", self.country)

            # 国家代码标准化
            country_map = {"United States": "US", "United Kingdom": "UK",
                           "Canada": "CA", "Australia": "AU", "Singapore": "SG", "Japan": "JP"}
            country = country_map.get(country_raw, self.country)

            description = j.get("descriptionPlain", "") or self.clean_html(j.get("description", ""))

            created_ms = j.get("createdAt", 0)
            pub_at = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc) if created_ms else None

            jobs.append(RawJob(
                title=title,
                url=job_url,
                company=self.company_name,
                location=location,
                country=country,
                salary="",
                description=self.truncate(description, 2500),
                source_id=self.source_id,
                source_name=self.source_name,
                published_at=pub_at,
            ))

        logger.info(f"[lever/{self.slug}] 采集 {len(jobs)} 条")
        return jobs
