from __future__ import annotations
import json
import re
import aiohttp
import asyncio
from loguru import logger
from sqlalchemy import select

from storage.db import get_session
from storage.models import Job
from config.settings import settings, load_profile

def analyst_system() -> str:
    return "你是职位分析助手。依据以下候选人资料分析，无法证实的信息应标为未知。\n" + json.dumps(load_profile(), ensure_ascii=False)

ANALYZE_PROMPT = """基于候选人档案，对以下 {n} 个职位进行深度匹配分析。

输出格式为JSON数组，每个元素：
{{
  "job_id": "...",
  "match_analysis": "2-3句话，从候选人背景出发分析为什么合适或不合适",
  "winning_points": ["最能打动HR的优势点1", "优势点2"],
  "risk_points": ["潜在劣势或担忧1"],
  "apply_tips": "申请建议：简历重点/cover letter切入点（具体可操作，1-2句）",
  "visa_assessment": "签证情况分析：是否明确赞助/建议如何问/是否值得冒险",
  "priority": "must_apply 或 worth_trying 或 long_shot"
}}

priority 判断标准：
- must_apply：角色高度匹配 + 签证明确或高概率支持
- worth_trying：角色匹配 + 签证情况不明但值得尝试
- long_shot：角色基本匹配但签证不明/有较大短板

仅输出JSON数组，不要其他内容。

职位列表：
{jobs_json}"""


async def _call_openrouter(messages: list[dict]) -> str:
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://job-radar.local",
        "X-Title": "JobRadar",
    }
    payload = {
        "model": settings.openrouter_analyze_model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 8192,
    }
    timeout = aiohttp.ClientTimeout(total=180)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"OpenRouter HTTP {resp.status}: {text[:300]}")
            data = await resp.json()
            content = data["choices"][0]["message"]["content"]
            if content is None:
                raise RuntimeError("模型返回空内容（content=null）")
            return content


async def analyze_top_jobs(top_n: int = 15) -> int:
    """
    Stage 2: 深度分析 Top N 职位（按 match_score 降序）。
    返回分析数量。
    """
    if not settings.openrouter_api_key:
        raise RuntimeError("设置 OPENROUTER_API_KEY 后才能进行 AI 分析")
    async with get_session() as session:
        result = await session.execute(
            select(Job).where(
                Job.is_scored == True,
                Job.is_filtered_out == False,
                Job.is_analyzed == False,
                Job.match_score >= 5.5,
            ).order_by(Job.match_score.desc()).limit(top_n)
        )
        jobs = result.scalars().all()

    if not jobs:
        logger.info("没有待深度分析的职位")
        return 0

    job_list = []
    for j in jobs:
        job_list.append({
            "job_id": j.id,
            "title": j.title,
            "company": j.company or "",
            "location": j.location or "",
            "country": j.country or "",
            "salary": j.salary or "",
            "visa_status": j.visa_status or "unknown",
            "score": j.match_score,
            "score_reason": j.score_reason or "",
            "desc": j.description[:800] if j.description else "",
        })

    try:
        raw = await _call_openrouter([
            {"role": "system", "content": analyst_system()},
            {"role": "user", "content": ANALYZE_PROMPT.format(
                n=len(job_list),
                jobs_json=json.dumps(job_list, ensure_ascii=False, indent=2),
            )},
        ])

        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[^\n]*\n?", "", raw)
            raw = raw.rstrip("`").strip()
        # 提取JSON数组（模型可能有thinking前缀）
        m = re.search(r'\[[\s\S]*\]', raw)
        if m:
            raw = m.group(0)
        # 修复尾随逗号
        raw = re.sub(r',\s*([}\]])', r'\1', raw)

        analyses = json.loads(raw)
    except Exception as e:
        logger.error(f"深度分析失败: {e}")
        return 0

    analysis_map = {a["job_id"]: a for a in analyses}

    async with get_session() as session:
        for j in jobs:
            db_job = await session.get(Job, j.id)
            if not db_job:
                continue
            a = analysis_map.get(j.id, {})
            db_job.is_analyzed = True
            db_job.analysis_json = json.dumps(a, ensure_ascii=False)
            db_job.priority = a.get("priority", "worth_trying")

    logger.info(f"深度分析完成: {len(jobs)} 条")
    return len(jobs)
