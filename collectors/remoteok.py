from __future__ import annotations
"""
RemoteOK JSON API 采集器
无需 API Key，返回远程职位，含薪资范围
https://remoteok.com/api
注意：数组第一个元素是法律声明，需要跳过
"""
import aiohttp
from datetime import datetime, timezone
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from collectors.base import BaseJobCollector, RawJob
from config.settings import settings

REMOTEOK_API = "https://remoteok.com/api"

# 拉取多个标签，每个标签一次请求
TAGS = ["ai", "machine-learning", "technical-program-manager", "solutions-engineer", "consulting"]


class RemoteOKCollector(BaseJobCollector):
    def __init__(self):
        super().__init__({"id": "remoteok", "name": "RemoteOK", "country": "REMOTE"})
        self.timeout = aiohttp.ClientTimeout(total=30)

    async def fetch(self) -> list[RawJob]:
        all_jobs: list[RawJob] = []
        seen_ids: set[str] = set()

        for tag in TAGS:
            try:
                jobs = await self._fetch_tag(tag)
                for j in jobs:
                    if j.url not in seen_ids:
                        seen_ids.add(j.url)
                        all_jobs.append(j)
            except Exception as e:
                logger.error(f"[remoteok] tag={tag} 失败: {e}")

        logger.info(f"[remoteok] 共采集 {len(all_jobs)} 条")
        return all_jobs

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=15))
    async def _fetch_tag(self, tag: str) -> list[RawJob]:
        proxy = settings.http_proxy or None
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; JobRadar/1.0)",
            "Accept": "application/json",
        }

        async with aiohttp.ClientSession(headers=headers, timeout=self.timeout) as session:
            async with session.get(
                REMOTEOK_API,
                params={"tag": tag},
                proxy=proxy,
                ssl=False,
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[remoteok] HTTP {resp.status} tag={tag}")
                    return []
                data = await resp.json(content_type=None)

        # 跳过第一个元素（法律声明）
        if not isinstance(data, list) or len(data) < 2:
            return []
        items = data[1:]

        jobs: list[RawJob] = []
        for item in items:
            if not isinstance(item, dict):
                continue

            title = (item.get("position") or "").strip()
            url = (item.get("apply_url") or item.get("url") or "").strip()
            if not url:
                slug = item.get("slug", "")
                url = f"https://remoteok.com/l/{slug}" if slug else ""
            if not title or not url:
                continue

            company = (item.get("company") or "").strip()
            location = (item.get("location") or "REMOTE").strip()

            sal_min = item.get("salary_min")
            sal_max = item.get("salary_max")
            salary = ""
            if sal_min and sal_max:
                salary = f"${sal_min:,} - ${sal_max:,}"
            elif sal_min:
                salary = f"${sal_min:,}+"

            desc_html = item.get("description") or ""
            description = self.clean_html(desc_html)

            pub_at = None
            epoch = item.get("epoch")
            if epoch:
                try:
                    pub_at = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
                except Exception:
                    pass

            jobs.append(RawJob(
                title=title,
                url=url,
                company=company,
                location=location,
                country="REMOTE",
                salary=salary,
                description=self.truncate(description, 2500),
                source_id="remoteok",
                source_name="RemoteOK",
                published_at=pub_at,
            ))

        return jobs
