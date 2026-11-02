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

    # ---- 观察池每日快照（spec 003 D6）----
    # 定时任务开关：测试环境必须设为 false（否则会起后台任务并发真实网络请求）
    watchlist_snapshot_enabled: bool = True
    # 每日抓取时刻（Asia/Shanghai）。格式 "HH:MM"；解析失败时回退 15:30，不启动即崩
    watchlist_snapshot_time: str = "15:30"

    # ---- 观察池历史回填 ----
    # 新加入的标的首次刷新时，用真实日K + 历史分红补一段快照，走势图才有历史可看。
    # 测试环境必须设为 false（否则首次刷新会发真实日K请求）。
    watchlist_backfill_enabled: bool = True
    # 回填范围：
    #   true  → 从**上市日**补到至今（走势图可选「全部」/任意自定义区间）
    #   false → 只补最近 watchlist_backfill_days 天（旧行为，省流量少写库）
    watchlist_backfill_full: bool = True
    # 仅在 watchlist_backfill_full=false 时生效的回填窗口（自然日）
    watchlist_backfill_days: int = 365

    @property
    def cors_origins_list(self) -> List[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
