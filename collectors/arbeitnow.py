from __future__ import annotations
"""
Arbeitnow Job Board API 采集器
欧洲（主要德国）科技职位，无需 API Key
https://www.arbeitnow.com/api/job-board-api
"""
import aiohttp
from datetime import datetime, timezone
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from collectors.base import BaseJobCollector, RawJob
from config.settings import settings

ARBEITNOW_API = "https://www.arbeitnow.com/api/job-board-api"
MAX_PAGES = 5


class ArbeitnowCollector(BaseJobCollector):
    def __init__(self):
        super().__init__({"id": "arbeitnow", "name": "Arbeitnow (EU)", "country": "DE"})
        self.timeout = aiohttp.ClientTimeout(total=30)

    async def fetch(self) -> list[RawJob]:
        all_jobs: list[RawJob] = []
        seen_urls: set[str] = set()

        for page in range(1, MAX_PAGES + 1):
            try:
                jobs, has_more = await self._fetch_page(page)
                for j in jobs:
                    if j.url not in seen_urls:
                        seen_urls.add(j.url)
                        all_jobs.append(j)
                if not has_more:
                    break
            except Exception as e:
                logger.error(f"[arbeitnow] page={page} 失败: {e}")
                break

        logger.info(f"[arbeitnow] 共采集 {len(all_jobs)} 条")
        return all_jobs

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def _fetch_page(self, page: int) -> tuple[list[RawJob], bool]:
        proxy = settings.http_proxy or None
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; JobRadar/1.0)",
            "Accept": "application/json",
        }

        async with aiohttp.ClientSession(headers=headers, timeout=self.timeout) as session:
            async with session.get(
                ARBEITNOW_API,
                params={"page": page},
                proxy=proxy,
                ssl=False,
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[arbeitnow] HTTP {resp.status} page={page}")
                    return [], False
                data = await resp.json()

        items = data.get("data", [])
        if not items:
            return [], False

        jobs: list[RawJob] = []
        for item in items:
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            if not title or not url:
                continue

            company = (item.get("company_name") or "").strip()
            location = (item.get("location") or "").strip()
            desc_html = item.get("description") or ""
            description = self.clean_html(desc_html)

            pub_at = None
            created = item.get("created_at")
            if created:
                try:
                    pub_at = datetime.fromtimestamp(int(created), tz=timezone.utc)
                except Exception:
                    pass

            # 国家判断：Arbeitnow 以德国为主，也有部分欧洲其他国家
            country = _parse_eu_country(location)

            jobs.append(RawJob(
                title=title,
                url=url,
                company=company,
                location=location,
                country=country,
                salary="",
                description=self.truncate(description, 2500),
                source_id="arbeitnow",
                source_name="Arbeitnow (EU)",
                published_at=pub_at,
            ))

        has_more = bool(data.get("links", {}).get("next"))
        return jobs, has_more


def _parse_eu_country(location: str) -> str:
    loc = location.upper()
    if any(x in loc for x in ["BERLIN", "MUNICH", "MÜNCHEN", "HAMBURG", "FRANKFURT", "GERMANY", "DEUTSCHLAND"]):
        return "DE"
    if any(x in loc for x in ["AMSTERDAM", "NETHERLANDS", "ROTTERDAM"]):
        return "NL"
    if any(x in loc for x in ["PARIS", "FRANCE", "LYON"]):
        return "FR"
    if any(x in loc for x in ["DUBLIN", "IRELAND"]):
        return "IE"
    if any(x in loc for x in ["STOCKHOLM", "SWEDEN", "GOTHENBURG"]):
        return "SE"
    if any(x in loc for x in ["VIENNA", "AUSTRIA", "WIEN"]):
        return "AT"
    if any(x in loc for x in ["ZURICH", "ZÜRICH", "SWITZERLAND", "GENEVA"]):
        return "CH"
    if any(x in loc for x in ["SPAIN", "MADRID", "BARCELONA"]):
        return "ES"
    if any(x in loc for x in ["PORTUGAL", "LISBON", "PORTO"]):
        return "PT"
    return "EU"
