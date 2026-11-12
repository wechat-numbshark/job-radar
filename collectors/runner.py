from __future__ import annotations
import asyncio
import yaml
import os
from loguru import logger

from collectors.base import RawJob
from collectors.rss import RSSJobCollector
from collectors.greenhouse import GreenhouseCollector
from collectors.lever import LeverCollector
from collectors.linkedin import LinkedInCollector
from collectors.remotive import RemotiveCollector
from collectors.remoteok import RemoteOKCollector
from collectors.arbeitnow import ArbeitnowCollector
from storage.db import get_session
from storage.models import Job
from config.settings import settings


def _load_sources() -> dict:
    p = os.path.join(os.path.dirname(__file__), "..", "config", "sources.yaml")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


async def _save_jobs(jobs: list[RawJob]) -> int:
    if not jobs:
        return 0
    new_count = 0
    async with get_session() as session:
        for raw in jobs:
            job_id = Job.make_id(raw.url)
            existing = await session.get(Job, job_id)
            if existing:
                continue
            session.add(Job(
                id=job_id,
                title=raw.title,
                company=raw.company,
                location=raw.location,
                country=raw.country,
                salary=raw.salary,
                description=raw.description,
                url=raw.url,
                source_id=raw.source_id,
                source_name=raw.source_name,
                published_at=raw.published_at,
            ))
            new_count += 1
    return new_count


async def run_all(concurrency: int = 5) -> dict:
    cfg = _load_sources()
    collectors = []

    # RSS
    for src in cfg.get("rss_sources", []):
        collectors.append(RSSJobCollector(src))

    # Greenhouse
    for company in cfg.get("greenhouse_companies", []):
        collectors.append(GreenhouseCollector(company))

    # Lever
    for company in cfg.get("lever_companies", []):
        collectors.append(LeverCollector(company))

    # 新 API 来源
    collectors.append(RemotiveCollector())
    collectors.append(RemoteOKCollector())
    collectors.append(ArbeitnowCollector())

    # LinkedIn（独立，内部自带并发控制）
    linkedin = LinkedInCollector() if settings.enable_linkedin else None

    logger.info(f"共 {len(collectors)+(1 if linkedin else 0)} 个采集源，开始采集...")

    sem = asyncio.Semaphore(concurrency)

    async def _fetch_one(col):
        async with sem:
            try:
                return await col.fetch()
            except Exception as e:
                logger.error(f"[{col.source_id}] 采集异常: {e}")
                return []

    # 先跑 LinkedIn（慢，先启动），再并发其他
    li_task = asyncio.create_task(_fetch_one(linkedin)) if linkedin else None
    other_results = await asyncio.gather(*[_fetch_one(c) for c in collectors])
    li_jobs = await li_task if li_task else []

    all_jobs: list[RawJob] = list(li_jobs)
    for r in other_results:
        all_jobs.extend(r)

    total_fetched = len(all_jobs)
    new_count = await _save_jobs(all_jobs)
    logger.info(f"采集完成: 共 {total_fetched} 条，新增 {new_count} 条入库")
    return {"total": total_fetched, "new": new_count}
