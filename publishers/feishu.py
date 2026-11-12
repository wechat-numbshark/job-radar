from __future__ import annotations
"""
每日求职简报推送到飞书。
20:30 触发，推送顺序：
  1. 汇总卡（今日找到几个，按优先级分类）
  2. must_apply 职位卡（每张一个职位）
  3. worth_trying 职位卡
  (long_shot 仅在汇总卡统计，不单独推送以免刷屏)
"""
import json
import asyncio
import aiohttp
from datetime import datetime, date, timedelta, timezone
from loguru import logger
from sqlalchemy import select, and_

from config.settings import settings
from storage.db import get_session
from storage.models import Job, PushLog


COUNTRY_FLAG = {
    "US": "🇺🇸", "UK": "🇬🇧", "CA": "🇨🇦",
    "AU": "🇦🇺", "SG": "🇸🇬", "JP": "🇯🇵",
    "REMOTE": "🌍",
}

VISA_BADGE = {
    "confirmed": "✅ 签证赞助已确认",
    "unknown": "❓ 签证情况待确认",
    "no_sponsor": "❌ 不提供签证赞助",
}

PRIORITY_HEADER_COLOR = {
    "must_apply":   "red",
    "worth_trying": "orange",
    "long_shot":    "blue",
}

PRIORITY_LABEL = {
    "must_apply":   "⭐⭐⭐ 必投",
    "worth_trying": "⭐⭐ 值得尝试",
    "long_shot":    "⭐ 有挑战",
}


async def _send(payload: dict) -> tuple[bool, str]:
    webhook = settings.feishu_webhook_jobs
    if not webhook:
        logger.warning("FEISHU_WEBHOOK_JOBS 未配置")
        return False, "no webhook"
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(webhook, json=payload) as resp:
                text = await resp.text()
                ok = resp.status == 200 and '"StatusCode":0' in text
                return ok, text[:200]
    except Exception as e:
        return False, str(e)


def _build_summary_card(jobs: list[Job], push_date: str) -> dict:
    must = [j for j in jobs if j.priority == "must_apply"]
    worth = [j for j in jobs if j.priority == "worth_trying"]
    long_ = [j for j in jobs if j.priority == "long_shot"]

    country_counts: dict[str, int] = {}
    for j in jobs:
        c = j.country or "?"
        country_counts[c] = country_counts.get(c, 0) + 1

    country_str = "  ".join(
        f"{COUNTRY_FLAG.get(c, c)} {n}个"
        for c, n in sorted(country_counts.items())
    )

    body_md = (
        f"📅 **{push_date}** · 今日全球求职雷达\n\n"
        f"| 优先级 | 数量 |\n"
        f"|--------|------|\n"
        f"| ⭐⭐⭐ 必投 | **{len(must)}** 个 |\n"
        f"| ⭐⭐ 值得尝试 | **{len(worth)}** 个 |\n"
        f"| ⭐ 有挑战 | **{len(long_)}** 个 |\n\n"
        f"🌍 **覆盖地区：** {country_str or '无'}\n\n"
        f"↓ 以下为每个岗位详情卡片，优先级由高到低 ↓"
    )

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": f"🌐 求职雷达 · {push_date} · 共 {len(jobs)} 个岗位"},
                "template": "green",
            },
            "body": {
                "elements": [
                    {"tag": "markdown", "content": body_md},
                ]
            },
        },
    }


def _build_job_card(job: Job) -> dict:
    flag = COUNTRY_FLAG.get(job.country or "", "🌐")
    visa_badge = VISA_BADGE.get(job.visa_status or "unknown", "❓ 签证情况待确认")
    priority_label = PRIORITY_LABEL.get(job.priority or "worth_trying", "⭐⭐")
    color = PRIORITY_HEADER_COLOR.get(job.priority or "worth_trying", "blue")

    salary_str = f"💰 {job.salary}" if job.salary else ""

    # 解析分析JSON
    analysis = {}
    if job.analysis_json:
        try:
            analysis = json.loads(job.analysis_json)
        except Exception:
            pass

    match_analysis = analysis.get("match_analysis", job.score_reason or "")
    winning_pts = analysis.get("winning_points", [])
    risk_pts = analysis.get("risk_points", [])
    apply_tips = analysis.get("apply_tips", "")
    visa_assessment = analysis.get("visa_assessment", "")

    # 构建正文 markdown
    lines = [
        f"{flag} **{job.company or '未知公司'}** | 📍 {job.location or '地点未知'} {salary_str}",
        f"🛂 {visa_badge}",
        "",
        f"**匹配分析：** {match_analysis}" if match_analysis else "",
    ]

    if winning_pts:
        lines.append("")
        lines.append("**优势亮点：**")
        for pt in winning_pts[:2]:
            lines.append(f"✅ {pt}")

    if risk_pts:
        lines.append("")
        lines.append("**注意事项：**")
        for pt in risk_pts[:1]:
            lines.append(f"⚠️ {pt}")

    if apply_tips:
        lines.append("")
        lines.append(f"**申请建议：** {apply_tips}")

    if visa_assessment:
        lines.append("")
        lines.append(f"**签证评估：** {visa_assessment}")

    body_md = "\n".join(l for l in lines if l is not None)

    # 在 markdown 末尾加申请链接（schema V2 不支持 action tag）
    body_md += f"\n\n**[🔗 立即申请]({job.url})**"

    card = {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"{priority_label} | {job.title}",
                },
                "template": color,
            },
            "body": {
                "elements": [
                    {"tag": "markdown", "content": body_md},
                ]
            },
        },
    }
    return card


async def push_daily_report() -> int:
    """
    推送今日求职简报。
    只推送今天采集到、已分析、未推送的职位。
    返回推送数量。
    """
    if settings.dry_run:
        logger.info("[dry_run] 跳过飞书推送")
        return 0

    # 取今日（过去24小时）已分析、未推送的职位
    cutoff = datetime.now(timezone.utc) - timedelta(hours=28)

    async with get_session() as session:
        result = await session.execute(
            select(Job).where(
                and_(
                    Job.is_analyzed == True,
                    Job.is_filtered_out == False,
                    Job.is_pushed == False,
                    Job.match_score >= 5.5,
                    Job.collected_at >= cutoff,
                )
            ).order_by(
                # must_apply 最前，其次 worth_trying，再 long_shot，同级按分排
                Job.priority.asc(),  # must < worth < long（字典序）
                Job.match_score.desc(),
            )
        )
        jobs = result.scalars().all()

    if not jobs:
        logger.info("今日没有待推送岗位")
        # 发一条空提示
        ok, _ = await _send({
            "msg_type": "text",
            "content": {"text": f"📭 求职雷达 {date.today()} · 今日暂无新匹配岗位"},
        })
        return 0

    push_date = date.today().strftime("%Y-%m-%d")

    # 排序：must_apply → worth_trying → long_shot
    priority_order = {"must_apply": 0, "worth_trying": 1, "long_shot": 2}
    sorted_jobs = sorted(jobs, key=lambda j: (priority_order.get(j.priority or "long_shot", 2), -j.match_score))

    pushed = 0

    # 1. 汇总卡
    summary_card = _build_summary_card(sorted_jobs, push_date)
    ok, resp = await _send(summary_card)
    if ok:
        logger.info("汇总卡推送成功")
    else:
        logger.warning(f"汇总卡推送失败: {resp}")
    await asyncio.sleep(0.5)

    # 2. 按优先级推送职位卡（long_shot 超过3个则只推前3）
    must = [j for j in sorted_jobs if j.priority == "must_apply"]
    worth = [j for j in sorted_jobs if j.priority == "worth_trying"]
    long_ = [j for j in sorted_jobs if j.priority == "long_shot"][:3]

    to_push = must + worth + long_

    async with get_session() as session:
        for job in to_push:
            card = _build_job_card(job)
            ok, resp_text = await _send(card)

            db_job = await session.get(Job, job.id)
            if db_job:
                db_job.is_pushed = True
                db_job.pushed_at = datetime.now(timezone.utc)
            session.add(PushLog(job_id=job.id, success=ok, response=resp_text))

            if ok:
                pushed += 1
                logger.info(f"推送: [{job.priority}] {job.title} @ {job.company}")
            else:
                logger.warning(f"推送失败: {job.title}, {resp_text}")

            await asyncio.sleep(0.3)  # 避免飞书限速

    logger.info(f"今日推送完成: {pushed}/{len(to_push)} 条")
    return pushed
