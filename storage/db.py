from __future__ import annotations
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from contextlib import asynccontextmanager
from loguru import logger
from config.settings import settings
from storage.models import Base
import os


def _get_engine():
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    return create_async_engine(settings.database_url, echo=False)


engine = _get_engine()

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_session():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库初始化完成")


async def close_db():
    await engine.dispose()
