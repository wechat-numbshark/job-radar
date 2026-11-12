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
            if await session.get(Job, Job.make_id(row["url"])):
                continue
            session.add(Job(id=Job.make_id(row["url"]), title=row["title"], company=row["company"], location=row["location"], country=row["country"], salary=row["salary"], description=row["description"], url=row["url"], source_id="demo", source_name="Fictional demo", match_score=row["score"], visa_status=row["visa_status"], priority=row["priority"], is_scored=True, is_analyzed=True))
    await close_db()

if __name__ == "__main__":
    asyncio.run(main())
