from __future__ import annotations
import aiohttp
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential
from datetime import datetime, timezone

from collectors.base import BaseJobCollector, RawJob
from config.settings import settings

GH_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


class GreenhouseCollector(BaseJobCollector):
    """采集指定公司的 Greenhouse 公开职位"""

    def __init__(self, company_config: dict):
        super().__init__({
            "id": f"greenhouse_{company_config['slug']}",
            "name": f"Greenhouse - {company_config['name']}",
            "country": company_config.get("country", "US"),
        })
        self.slug = company_config["slug"]
        self.company_name = company_config["name"]
        self.timeout = aiohttp.ClientTimeout(total=20)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def fetch(self) -> list[RawJob]:
        url = GH_API.format(slug=self.slug)
        proxy = settings.http_proxy or None
        headers = {"User-Agent": "Mozilla/5.0 JobRadar/1.0"}

        try:
            async with aiohttp.ClientSession(headers=headers, timeout=self.timeout) as session:
                async with session.get(url, proxy=proxy, ssl=False) as resp:
                    if resp.status == 404:
                        logger.warning(f"[greenhouse/{self.slug}] 404 - 公司不在Greenhouse")
                        return []
                    if resp.status != 200:
                        logger.warning(f"[greenhouse/{self.slug}] HTTP {resp.status}")
                        return []
                    data = await resp.json()
        except Exception as e:
            logger.error(f"[greenhouse/{self.slug}] 请求失败: {e}")
            return []

        raw_jobs = data.get("jobs", [])
        jobs = []
        for j in raw_jobs:
            title = j.get("title", "").strip()
            job_url = j.get("absolute_url", "") or j.get("url", "")
            if not title or not job_url:
                continue

            location = ""
            offices = j.get("offices", [])
            if offices:
                location = offices[0].get("name", "")
            if not location:
                loc = j.get("location", {})
                location = loc.get("name", "") if isinstance(loc, dict) else str(loc)

            # 正文（content字段含HTML）
            content_html = j.get("content", "") or ""
            description = self.clean_html(content_html)

            # 发布时间
            updated_at = j.get("updated_at") or j.get("first_published") or ""
            pub_at = None
            if updated_at:
                try:
                    pub_at = datetime.fromisoformat(
                        updated_at.replace("Z", "+00:00")
                    ).astimezone(timezone.utc)
                except Exception:
                    pass

            jobs.append(RawJob(
                title=title,
                url=job_url,
                company=self.company_name,
                location=location,
                country=self.country,
                salary="",
                description=self.truncate(description, 2500),
                source_id=self.source_id,
                source_name=self.source_name,
                published_at=pub_at,
            ))

        logger.info(f"[greenhouse/{self.slug}] 采集 {len(jobs)} 条")
        return jobs
