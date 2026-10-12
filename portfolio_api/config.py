"""应用配置：集中读取 .env / 环境变量"""
import os
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置
    优先级：环境变量 > .env > 代码默认值
    复制 .env.example 为 .env 即可自定义。
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- 数据库 ----
    # 默认用 sqlite 在 backend 根目录下；可改为 mysql/postgres URL
    database_url: str = "sqlite:///./portfolio.db"

    # ---- 服务 ----
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_reload: bool = False
    cors_origins: str = "*"  # 多个用逗号分隔

    # ---- 缓存 ----
    dividend_cache_ttl_seconds: int = 6 * 3600  # 6h

    # ---- 外部数据源 ----
    tencent_quote_url: str = "https://qt.gtimg.cn/q"

    @property
    def cors_origins_list(self) -> List[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
