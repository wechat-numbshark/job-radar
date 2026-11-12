from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional
import os
import json
from pathlib import Path


class Settings(BaseSettings):
    # 数据库
    db_path: str = "data/jobs.db"

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_score_model: str = "qwen/qwen3-235b-a22b:free"
    openrouter_analyze_model: str = "google/gemini-2.0-flash-exp:free"

    # 飞书
    feishu_webhook_jobs: str = ""

    # 代理
    http_proxy: Optional[str] = None
    https_proxy: Optional[str] = None

    # 运行控制
    log_level: str = "INFO"
    dry_run: bool = True
    enable_linkedin: bool = False

    @property
    def database_url(self) -> str:
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        return f"sqlite+aiosqlite:///{self.db_path}"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


settings = Settings()



def load_profile() -> dict:
    path = Path(os.getenv("JOB_RADAR_PROFILE", "config/profile.example.json"))
    return json.loads(path.read_text(encoding="utf-8"))
