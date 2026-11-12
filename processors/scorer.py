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

# 模型降级列表：依次尝试，直到成功
SCORE_MODELS = [settings.openrouter_score_model]

def score_system() -> str:
    return "你是职位匹配助手。根据以下候选人资料与偏好评分；不得臆测签证赞助或薪资。\n" + json.dumps(load_profile(), ensure_ascii=False)

SCORE_PROMPT = """对以下职位打匹配分（1-10）并注明签证评估。

仅输出JSON数组，不要其他内容：
[{{"id":"1","score":8,"visa":"confirmed/unknown/no_sponsor","reason":"一句话理由（中文，20字内）"}},...]

职位列表：
{jobs_json}"""


async def _call_openrouter_with_fallback(messages: list[dict], batch_idx: int) -> str:
    """尝试多个模型，带重试和延迟"""
    models = list(dict.fromkeys(SCORE_MODELS))  # 去重保序

    for model in models:
        for attempt in range(3):
            try:
                result = await _call_one(messages, model)
                return result
            except RateLimitError:
                wait = 30 * (attempt + 1)
                logger.warning(f"[scorer] {model} 限速，等待 {wait}s (attempt {attempt+1}/3)")
                await asyncio.sleep(wait)
            except ModelNotFound:
                logger.warning(f"[scorer] 模型不可用: {model}，尝试下一个")
                break
            except Exception as e:
                wait = 10 * (attempt + 1)
                logger.error(f"[scorer] {model} 错误: {e}，等待 {wait}s")
                await asyncio.sleep(wait)

    raise RuntimeError("所有模型均失败")


def _extract_scores(raw: str) -> list[dict]:
    """从模型输出中健壮地提取评分数组"""
    raw = raw.strip()

    # 去掉 markdown 代码块
    if raw.startswith("```"):
        raw = re.sub(r"^```[^\n]*\n?", "", raw)
        raw = raw.rstrip("`").strip()

    # 找到第一个 [ ... ] 块
    m = re.search(r'\[[\s\S]*\]', raw)
    if not m:
        raise ValueError("未找到JSON数组")
    chunk = m.group(0)

    # 修复常见格式问题
    chunk = re.sub(r',\s*([}\]])', r'\1', chunk)  # 尾随逗号

    # 尝试直接解析
    try:
        return json.loads(chunk)
    except json.JSONDecodeError:
        pass

    # 逐条提取：每行一个 JSON 对象
    scores = []
    for obj_m in re.finditer(r'\{[^\{\}]+\}', chunk):
        try:
            obj = json.loads(obj_m.group(0))
            scores.append(obj)
        except Exception:
            pass
    if scores:
        return scores

    raise ValueError(f"JSON解析失败，原文: {chunk[:200]}")


class RateLimitError(Exception):
    pass

class ModelNotFound(Exception):
    pass


async def _call_one(messages: list[dict], model: str) -> str:
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://job-radar.local",
        "X-Title": "JobRadar",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 4096,
    }
    timeout = aiohttp.ClientTimeout(total=90)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as resp:
            text = await resp.text()
            if resp.status == 429:
                raise RateLimitError(text[:100])
            if resp.status == 404:
                raise ModelNotFound(model)
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}: {text[:200]}")
            data = json.loads(text)
            content = data["choices"][0]["message"]["content"]
            if content is None:
                raise RuntimeError("模型返回空内容（content=null）")
            return content


async def score_jobs(batch_size: int = 20, max_jobs: int = 200) -> int:
    """
    Stage 1: 批量AI评分。
    每日最多处理 max_jobs 条（避免free模型限速），批次间等 15s。
    返回处理数量。
    """
    if not settings.openrouter_api_key:
        raise RuntimeError("设置 OPENROUTER_API_KEY 后才能进行 AI 评分")
    async with get_session() as session:
        result = await session.execute(
            select(Job).where(
                Job.is_scored == False,
                Job.is_filtered_out == False,
            ).order_by(Job.collected_at.desc()).limit(max_jobs)
        )
        jobs = result.scalars().all()

    if not jobs:
        logger.info("没有待评分职位")
        return 0

    logger.info(f"待评分职位: {len(jobs)} 条，分 {(len(jobs)+batch_size-1)//batch_size} 批")
    total_scored = 0

    for i in range(0, len(jobs), batch_size):
        batch = jobs[i : i + batch_size]
        job_list = []
        for idx, j in enumerate(batch):
            # 截断描述，去掉非ASCII防止JSON异常
            desc_raw = (j.description or "")[:300]
            desc_safe = desc_raw.encode("ascii", errors="ignore").decode("ascii")
            job_list.append({
                "id": str(idx),
                "title": j.title,
                "company": j.company or "",
                "location": j.location or "",
                "country": j.country or "",
                "desc": desc_safe,
            })

        try:
            raw = await _call_openrouter_with_fallback(
                messages=[
                    {"role": "system", "content": score_system()},
                    {"role": "user", "content": SCORE_PROMPT.format(
                        jobs_json=json.dumps(job_list, ensure_ascii=False)
                    )},
                ],
                batch_idx=i // batch_size,
            )

            scores = _extract_scores(raw)
        except Exception as e:
            logger.error(f"评分批次 {i//batch_size+1} 最终失败: {e}")
            continue

        score_map = {s["id"]: s for s in scores}
        async with get_session() as session:
            for idx, j in enumerate(batch):
                db_job = await session.get(Job, j.id)
                if not db_job:
                    continue
                s = score_map.get(str(idx), {})
                db_job.is_scored = True
                db_job.match_score = float(s.get("score", 5.0))
                db_job.score_reason = (s.get("reason", "") or "")[:200]

                visa = s.get("visa", "")
                if visa == "no_sponsor":
                    db_job.is_filtered_out = True
                    db_job.filter_reason = "AI判断：不提供签证赞助"
                elif visa == "confirmed" and db_job.visa_status != "confirmed":
                    db_job.visa_status = "confirmed"

                if db_job.match_score < 5.0:
                    db_job.is_filtered_out = True
                    db_job.filter_reason = f"AI匹配分过低: {db_job.match_score}"

                total_scored += 1

        logger.info(f"✓ 批次 {i//batch_size+1} 完成 ({len(batch)} 条)")

        # 批次间等待，避免 free tier 限速（45s 让模型冷却）
        if i + batch_size < len(jobs):
            await asyncio.sleep(45)

    logger.info(f"AI评分完成，共处理 {total_scored} 条")
    return total_scored
