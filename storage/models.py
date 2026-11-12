from __future__ import annotations
from sqlalchemy import Column, String, Integer, Boolean, DateTime, Text, Float
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime, timezone
import hashlib


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id           = Column(String, primary_key=True)   # sha256(url)[:16]
    title        = Column(String, nullable=False)
    company      = Column(String, default="")
    location     = Column(String, default="")
    country      = Column(String, default="")         # US/UK/CA/AU/SG/JP/REMOTE
    salary       = Column(String, default="")
    description  = Column(Text, default="")
    url          = Column(String, unique=True, nullable=False)
    source_id    = Column(String, default="")         # rss source id or greenhouse slug
    source_name  = Column(String, default="")

    # 签证
    visa_status  = Column(String, default="unknown")  # confirmed / unknown / no_sponsor

    # AI 分析结果
    match_score    = Column(Float, default=0.0)        # Stage1: 1-10
    score_reason   = Column(Text, default="")          # Stage1: 一句话理由
    analysis_json  = Column(Text, default="")          # Stage2: 完整JSON分析
    priority       = Column(String, default="")        # must_apply/worth_trying/long_shot

    # 状态
    is_scored      = Column(Boolean, default=False)
    is_analyzed    = Column(Boolean, default=False)
    is_pushed      = Column(Boolean, default=False)
    is_filtered_out = Column(Boolean, default=False)
    filter_reason  = Column(String, default="")

    # 时间
    published_at   = Column(DateTime)
    collected_at   = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    pushed_at      = Column(DateTime)

    @staticmethod
    def make_id(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()[:16]


class PushLog(Base):
    __tablename__ = "push_logs"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    job_id     = Column(String)
    success    = Column(Boolean)
    response   = Column(String, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
