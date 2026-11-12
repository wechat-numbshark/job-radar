"""
求职雷达 Web 服务
运行: python3 api/app.py
访问: http://localhost:8077
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from sqlalchemy import select, func, and_, or_, desc
from storage.db import init_db, get_session
from storage.models import Job

app = FastAPI(title="求职雷达", docs_url=None, redoc_url=None)

_db_ready = False


async def ensure_db():
    global _db_ready
    if not _db_ready:
        await init_db()
        _db_ready = True


@app.on_event("startup")
async def startup():
    await ensure_db()


# ── 静态页面 ──────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


# ── 统计数据 ──────────────────────────────────────────────
@app.get("/api/stats")
async def get_stats():
    now_utc = datetime.now(timezone.utc)
    today_start = (now_utc - timedelta(hours=28)).replace(tzinfo=timezone.utc)

    async with get_session() as session:
        total = (await session.execute(select(func.count()).select_from(Job))).scalar()
        today_count = (await session.execute(
            select(func.count()).select_from(Job)
            .where(Job.collected_at >= today_start)
        )).scalar()
        recommended = (await session.execute(
            select(func.count()).select_from(Job)
            .where(Job.is_pushed == True)
        )).scalar()
        scored = (await session.execute(
            select(func.count()).select_from(Job)
            .where(Job.is_scored == True)
        )).scalar()
        high_score = (await session.execute(
            select(func.count()).select_from(Job)
            .where(Job.match_score >= 7, Job.is_filtered_out == False)
        )).scalar()
        filtered_out = (await session.execute(
            select(func.count()).select_from(Job)
            .where(Job.is_filtered_out == True)
        )).scalar()

        # 按国家统计
        country_rows = (await session.execute(
            select(Job.country, func.count()).group_by(Job.country)
            .where(Job.is_filtered_out == False)
            .order_by(func.count().desc())
        )).all()

    return {
        "total": total,
        "today": today_count,
        "recommended": recommended,
        "scored": scored,
        "high_score": high_score,
        "filtered_out": filtered_out,
        "by_country": [{"country": r[0] or "?", "count": r[1]} for r in country_rows],
    }


# ── 职位列表 ──────────────────────────────────────────────
@app.get("/api/jobs")
async def get_jobs(
    tab: str = Query("recommended"),   # recommended / today / all
    country: str = Query(""),
    min_score: float = Query(0.0),
    search: str = Query(""),
    limit: int = Query(100),
    offset: int = Query(0),
):
    now_utc = datetime.now(timezone.utc)
    today_start = now_utc - timedelta(hours=28)

    async with get_session() as session:
        q = select(Job)

        if tab == "recommended":
            q = q.where(Job.is_pushed == True)
        elif tab == "today":
            q = q.where(Job.collected_at >= today_start, Job.is_filtered_out == False)
        else:  # all
            pass  # show everything including filtered

        if country:
            q = q.where(Job.country == country)
        if min_score > 0:
            q = q.where(Job.match_score >= min_score)
        if search:
            kw = f"%{search}%"
            q = q.where(
                or_(
                    Job.title.ilike(kw),
                    Job.company.ilike(kw),
                    Job.location.ilike(kw),
                )
            )

        total_q = select(func.count()).select_from(q.subquery())
        total = (await session.execute(total_q)).scalar()

        q = q.order_by(desc(Job.match_score), desc(Job.collected_at)).limit(limit).offset(offset)
        rows = (await session.execute(q)).scalars().all()

    jobs = []
    for j in rows:
        analysis = {}
        try:
            analysis = json.loads(j.analysis_json or "{}")
        except Exception:
            pass

        jobs.append({
            "id": j.id,
            "title": j.title,
            "company": j.company or "",
            "location": j.location or "",
            "country": j.country or "",
            "salary": j.salary or "",
            "url": j.url,
            "source_id": j.source_id or "",
            "source_name": j.source_name or "",
            "visa_status": j.visa_status or "unknown",
            "match_score": j.match_score or 0,
            "score_reason": j.score_reason or "",
            "priority": j.priority or "",
            "is_scored": j.is_scored,
            "is_analyzed": j.is_analyzed,
            "is_pushed": j.is_pushed,
            "is_filtered_out": j.is_filtered_out,
            "filter_reason": j.filter_reason or "",
            "collected_at": j.collected_at.isoformat() if j.collected_at else "",
            "pushed_at": j.pushed_at.isoformat() if j.pushed_at else "",
            # 深度分析字段
            "match_analysis": analysis.get("match_analysis", ""),
            "winning_points": analysis.get("winning_points", []),
            "risk_points": analysis.get("risk_points", []),
            "apply_tips": analysis.get("apply_tips", ""),
            "visa_assessment": analysis.get("visa_assessment", ""),
        })

    return {"total": total, "jobs": jobs}


# ── 来源列表 ──────────────────────────────────────────────
@app.get("/api/sources")
async def get_sources():
    import yaml
    cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "sources.yaml")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    sources = []

    for r in cfg.get("rss_sources", []):
        sources.append({
            "id": r["id"],
            "name": r["name"],
            "type": "RSS",
            "country": r.get("country", "REMOTE"),
        })

    for g in cfg.get("greenhouse_companies", []):
        sources.append({
            "id": f"greenhouse_{g['slug']}",
            "name": g["name"],
            "type": "Greenhouse",
            "country": g.get("country", "US"),
        })

    for l in cfg.get("lever_companies", []):
        sources.append({
            "id": f"lever_{l['slug']}",
            "name": l["name"],
            "type": "Lever",
            "country": l.get("country", "US"),
        })

    # 内置API来源
    sources += [
        {"id": "remotive",  "name": "Remotive",      "type": "API", "country": "REMOTE"},
        {"id": "remoteok",  "name": "RemoteOK",       "type": "API", "country": "REMOTE"},
        {"id": "arbeitnow", "name": "Arbeitnow (EU)", "type": "API", "country": "DE"},
        {"id": "linkedin",  "name": "LinkedIn Guest", "type": "LinkedIn", "country": "MULTI"},
    ]

    # 附加 job 统计
    async with get_session() as session:
        rows = (await session.execute(
            select(Job.source_id, func.count())
            .group_by(Job.source_id)
        )).all()
        count_map = {r[0]: r[1] for r in rows}

        rows2 = (await session.execute(
            select(Job.source_id, func.count())
            .where(Job.is_filtered_out == False)
            .group_by(Job.source_id)
        )).all()
        active_map = {r[0]: r[1] for r in rows2}

    for s in sources:
        sid = s["id"]
        # Greenhouse/Lever sources may use slug as source_id in DB
        slug_id = sid.replace("greenhouse_", "").replace("lever_", "")
        total_c = count_map.get(sid, 0) or count_map.get(slug_id, 0)
        active_c = active_map.get(sid, 0) or active_map.get(slug_id, 0)
        s["total_jobs"] = total_c
        s["active_jobs"] = active_c

    return sources


# ── 触发手动采集 ──────────────────────────────────────────
@app.post("/api/collect")
async def trigger_collect():
    asyncio.create_task(_run_collect())
    return {"message": "采集任务已启动，请稍候刷新页面"}


async def _run_collect():
    await ensure_db()
    from collectors.runner import run_all
    from processors.filter import filter_unprocessed_jobs
    await run_all()
    await filter_unprocessed_jobs()


# ── 触发评分 ──────────────────────────────────────────────
@app.post("/api/score")
async def trigger_score():
    asyncio.create_task(_run_score())
    return {"message": "评分任务已启动（后台运行，约需20-30分钟）"}


async def _run_score():
    await ensure_db()
    from processors.scorer import score_jobs
    await score_jobs(batch_size=15, max_jobs=200)


# ── 静态资源 ──────────────────────────────────────────────
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


if __name__ == "__main__":
    uvicorn.run("api.app:app", host="127.0.0.1", port=8077, reload=False)
