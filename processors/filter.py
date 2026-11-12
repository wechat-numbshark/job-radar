from __future__ import annotations
import re
import yaml
import os
from loguru import logger
from sqlalchemy import select

from storage.db import get_session
from storage.models import Job


def _load_cfg() -> dict:
    p = os.path.join(os.path.dirname(__file__), "..", "config", "sources.yaml")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


_cfg = None

def _get_cfg():
    global _cfg
    if _cfg is None:
        _cfg = _load_cfg()
    return _cfg


def _check_visa(text: str) -> str:
    """返回 confirmed / no_sponsor / unknown"""
    cfg = _get_cfg()
    lower = text.lower()
    for kw in cfg.get("visa_blacklist", []):
        if kw.lower() in lower:
            return "no_sponsor"
    for kw in cfg.get("visa_whitelist", []):
        if kw.lower() in lower:
            return "confirmed"
    return "unknown"


def _check_role(title: str) -> tuple[bool, str]:
    """
    返回 (pass, reason)
    先检查黑名单，再检查白名单
    """
    cfg = _get_cfg()
    lower_title = title.lower()

    for kw in cfg.get("role_blacklist", []):
        if kw.lower() in lower_title:
            return False, f"黑名单词: {kw}"

    whitelist = cfg.get("role_keywords", [])
    for kw in whitelist:
        if kw.lower() in lower_title:
            return True, ""

    return False, "标题不在目标职位范围"


def _check_japan_salary(country: str, salary: str, description: str) -> tuple[bool, str]:
    """日本职位：按配置中的最低年薪过滤。"""
    minimum = int(_get_cfg().get("japan_min_salary_jpy", 0))
    if country != "JP" or minimum <= 0:
        return True, ""

    combined = (salary + " " + description).lower()

    # 提取日元薪资（万円）
    m = re.search(r"(\d+)\s*万円", combined)
    if m:
        val = int(m.group(1))
        if val * 10_000 < minimum:
            return False, f"日本薪资过低: {val}万円 < {minimum:,}円"

    # 提取JPY数字
    m = re.search(r"jpy\s*(\d[\d,]*)|(\d[\d,]*)\s*(?:jpy|円)", combined)
    if m:
        val = int((m.group(1) or m.group(2)).replace(",", ""))
        if val < minimum:
            return False, f"日本薪资过低: ¥{val:,} < ¥{minimum:,}"

    return True, ""


async def filter_unprocessed_jobs() -> int:
    """
    对所有未打分、未过滤的职位进行规则过滤。
    - 设置 visa_status
    - 过滤不符合角色/签证/薪资的职位
    返回通过过滤的数量。
    """
    async with get_session() as session:
        result = await session.execute(
            select(Job).where(Job.is_scored == False, Job.is_filtered_out == False)
        )
        jobs = result.scalars().all()

    if not jobs:
        logger.info("没有待过滤职位")
        return 0

    passed = 0
    filtered = 0

    async with get_session() as session:
        for job in jobs:
            db_job = await session.get(Job, job.id)
            if not db_job:
                continue

            # 1. 角色关键词检查
            role_ok, role_reason = _check_role(db_job.title)
            if not role_ok:
                db_job.is_filtered_out = True
                db_job.filter_reason = role_reason
                filtered += 1
                continue

            # 2. 签证状态分析
            full_text = f"{db_job.title} {db_job.description}"
            db_job.visa_status = _check_visa(full_text)

            # 明确拒绝赞助 → 过滤
            if db_job.visa_status == "no_sponsor":
                db_job.is_filtered_out = True
                db_job.filter_reason = "明确不提供签证赞助"
                filtered += 1
                continue

            # 3. 日本薪资检查
            jp_ok, jp_reason = _check_japan_salary(
                db_job.country, db_job.salary, db_job.description
            )
            if not jp_ok:
                db_job.is_filtered_out = True
                db_job.filter_reason = jp_reason
                filtered += 1
                continue

            passed += 1

    logger.info(f"规则过滤: 通过 {passed} 条，过滤 {filtered} 条")
    return passed
