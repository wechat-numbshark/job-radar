"""Load fictional jobs into the local demo database only."""
import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage.db import init_db, get_session, close_db
from storage.models import Job

async def main():
    await init_db()
    rows = json.loads((Path(__file__).resolve().parents[1] / "examples/demo-jobs.json").read_text(encoding="utf-8"))
    async with get_session() as session:
        for row in rows:
            job = await session.get(Job, Job.make_id(row["url"]))
            if job is None:
                job = Job(id=Job.make_id(row["url"]), url=row["url"])
                session.add(job)
            job.title = row["title"]
            job.company = row["company"]
            job.location = row["location"]
            job.country = row["country"]
            job.salary = row["salary"]
            job.description = row["description"]
            job.source_id = "demo"
            job.source_name = "Fictional demo"
            job.match_score = row["score"]
            job.score_reason = row["score_reason"]
            job.analysis_json = json.dumps(row["analysis"], ensure_ascii=False)
            job.visa_status = row["visa_status"]
            job.priority = row["priority"]
            job.is_scored = True
            job.is_analyzed = True
    await close_db()

if __name__ == "__main__":
    asyncio.run(main())
