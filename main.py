"""
全球求职雷达 · 主调度器
每日时间表：
  17:00  采集各平台职位
  19:00  规则过滤 + AI评分（Stage 1）
  20:15  深度分析 Top 15（Stage 2）
  20:30  飞书推送每日简报
"""
import asyncio
import signal
import sys
import os

# 确保项目根目录在 path 里
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config.settings import settings
from storage.db import init_db, close_db
from collectors.runner import run_all
from processors.filter import filter_unprocessed_jobs
from processors.scorer import score_jobs
from processors.analyzer import analyze_top_jobs
from publishers.feishu import push_daily_report


def setup_logging():
    logger.remove()
    logger.add(
        sys.stdout,
        level=settings.log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
    )
    os.makedirs("logs", exist_ok=True)
    logger.add(
        "logs/job_radar_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="30 days",
        level="INFO",
        encoding="utf-8",
    )


async def job_collect():
    logger.info("=" * 40)
    logger.info("▶ 开始采集职位")
    stats = await run_all(concurrency=5)
    logger.info(f"✓ 采集完成: 新增 {stats['new']} 条 / 共获取 {stats['total']} 条")


async def job_process():
    logger.info("=" * 40)
    logger.info("▶ 开始规则过滤 + AI评分")
    passed = await filter_unprocessed_jobs()
    logger.info(f"  规则过滤通过: {passed} 条")
    scored = await score_jobs(batch_size=30)
    logger.info(f"✓ AI评分完成: {scored} 条")


async def job_analyze():
    logger.info("=" * 40)
    logger.info("▶ 开始深度分析 Top 15")
    n = await analyze_top_jobs(top_n=15)
    logger.info(f"✓ 深度分析完成: {n} 条")


async def job_push():
    logger.info("=" * 40)
    logger.info("▶ 开始推送每日求职简报")
    n = await push_daily_report()
    logger.info(f"✓ 推送完成: {n} 条")


async def run_full_pipeline():
    """一次性跑完完整流程（首次启动 or 手动调试用）"""
    await job_collect()
    await job_process()
    await job_analyze()
    await job_push()


async def main():
    setup_logging()
    logger.info("=" * 50)
    logger.info("🌐 全球求职雷达启动")
    logger.info(f"评分模型: {settings.openrouter_score_model}")
    logger.info(f"分析模型: {settings.openrouter_analyze_model}")
    logger.info(f"Dry Run: {settings.dry_run}")
    logger.info("=" * 50)

    await init_db()

    # 首次启动只初始化本地数据；采集与推送由显式命令或定时任务触发。

    # 启动定时调度（北京时间，系统时区需设为 Asia/Shanghai 或 UTC+8）
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    scheduler.add_job(
        job_collect, CronTrigger(hour=17, minute=0, timezone="Asia/Shanghai"),
        id="collect", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_process, CronTrigger(hour=19, minute=0, timezone="Asia/Shanghai"),
        id="process", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_analyze, CronTrigger(hour=20, minute=15, timezone="Asia/Shanghai"),
        id="analyze", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        job_push, CronTrigger(hour=20, minute=30, timezone="Asia/Shanghai"),
        id="push", max_instances=1, coalesce=True,
    )

    scheduler.start()
    logger.info("调度器已启动:")
    logger.info("  17:00  采集职位")
    logger.info("  19:00  规则过滤 + AI评分")
    logger.info("  20:15  深度分析 Top 15")
    logger.info("  20:30  飞书推送 ✉️")

    stop_event = asyncio.Event()

    def _shutdown(*_):
        logger.info("收到退出信号，正在关闭...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _shutdown)

    await stop_event.wait()
    scheduler.shutdown(wait=False)
    await close_db()
    logger.info("求职雷达已关闭")


if __name__ == "__main__":
    asyncio.run(main())
