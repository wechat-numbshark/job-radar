"""一次性全量运行：评分所有剩余职位 → 分析 Top 25 → 飞书推送"""
import asyncio
import sys
import json
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from loguru import logger
from storage.db import init_db, get_session, close_db
from sqlalchemy import select, func
from storage.models import Job


async def main():
    await init_db()

    # ── Step 1: 评分所有剩余职位 ───────────────────────────
    from processors.scorer import score_jobs
    logger.info("=" * 55)
    logger.info("▶ Step 1: AI评分剩余职位（全量）")
    scored = await score_jobs(batch_size=15, max_jobs=600)
    logger.info(f"✓ 本次评分: {scored} 条")

    async with get_session() as session:
        hi = (await session.execute(
            select(func.count()).select_from(Job)
            .where(Job.match_score >= 7, Job.is_filtered_out == False)
        )).scalar()
        total_scored = (await session.execute(
            select(func.count()).select_from(Job)
            .where(Job.is_scored == True, Job.is_filtered_out == False)
        )).scalar()
    logger.info(f"  累计已评分: {total_scored} 条，其中 ≥7分: {hi} 条")

    # ── Step 2: 深度分析 Top 25 ────────────────────────────
    from processors.analyzer import analyze_top_jobs
    logger.info("=" * 55)
    logger.info("▶ Step 2: 深度分析 Top 25 职位")
    n = await analyze_top_jobs(top_n=25)
    logger.info(f"✓ 深度分析完成: {n} 条")

    # 打印职位列表
    async with get_session() as session:
        result = await session.execute(
            select(Job).where(Job.is_analyzed == True)
            .order_by(Job.match_score.desc()).limit(25)
        )
        jobs = result.scalars().all()

    logger.info("\n─── 即将推送的职位列表 ───")
    for j in jobs:
        a = json.loads(j.analysis_json or '{}')
        logger.info(
            f"  [{j.match_score:.0f}/10][{(j.priority or '')[:4]}][{j.country}] "
            f"{j.title[:38]:<38} @ {(j.company or '')[:15]}"
        )

    # ── Step 3: 飞书推送 ───────────────────────────────────
    from publishers.feishu import push_daily_report
    logger.info("=" * 55)
    logger.info("▶ Step 3: 推送飞书")
    pushed = await push_daily_report()
    logger.info(f"✓ 推送完成: {pushed} 张卡片 → 飞书！")

    await close_db()
    logger.info("=" * 55)
    logger.info("🎉 全量运行完成！")


if __name__ == "__main__":
    asyncio.run(main())
