"""Collect public listings once without AI scoring or push delivery."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage.db import init_db, close_db
from collectors.runner import run_all
from processors.filter import filter_unprocessed_jobs

async def main():
    await init_db()
    try:
        stats = await run_all()
        passed = await filter_unprocessed_jobs()
        print({"collection": stats, "passed_filters": passed})
    finally:
        await close_db()

if __name__ == "__main__":
    asyncio.run(main())
