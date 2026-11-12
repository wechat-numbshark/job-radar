from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import re
from bs4 import BeautifulSoup


@dataclass
class RawJob:
    title: str
    url: str
    company: str = ""
    location: str = ""
    country: str = ""
    salary: str = ""
    description: str = ""
    source_id: str = ""
    source_name: str = ""
    published_at: Optional[datetime] = None


class BaseJobCollector:
    def __init__(self, source_config: dict):
        self.source_id = source_config.get("id", "")
        self.source_name = source_config.get("name", "")
        self.country = source_config.get("country", "")

    async def fetch(self) -> list[RawJob]:
        raise NotImplementedError

    @staticmethod
    def clean_html(html: str) -> str:
        if not html:
            return ""
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r"\s{2,}", " ", text)
        return text[:3000]

    @staticmethod
    def truncate(text: str, max_len: int = 2000) -> str:
        return text[:max_len] if text else ""
