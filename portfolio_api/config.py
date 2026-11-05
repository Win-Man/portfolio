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
    # SQLite 备份目录（相对当前工作目录，即 portfolio_api/）。只对 sqlite 生效。
    backup_dir: str = "backups"

    # ---- 服务 ----
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_reload: bool = False
    cors_origins: str = "*"  # 多个用逗号分隔

    # ---- 缓存 ----
    dividend_cache_ttl_seconds: int = 6 * 3600  # 6h

    # ---- 外部数据源 ----
    tencent_quote_url: str = "https://qt.gtimg.cn/q"

    # ---- 网络 / 代理 ----
    # 行情类请求一律直连、忽略 HTTP(S)_PROXY：本机代理（Clash 等）会把
    # eastmoney 等域名代理坏，表现为「搜不到股票 / 拉不到分红」。
    # 需要走代理（例如海外网络）时设为 false，两层绕过同时关闭。
    net_bypass_proxy: bool = True
    # 追加要绕过代理的域名（逗号分隔），与内置行情域名清单合并
    no_proxy_hosts: str = ""

    # ---- 观察池每日快照（spec 003 D6）----
    # 定时任务开关：测试环境必须设为 false（否则会起后台任务并发真实网络请求）
    watchlist_snapshot_enabled: bool = True
    # 每日抓取时刻（Asia/Shanghai）。格式 "HH:MM"；解析失败时回退 15:30，不启动即崩
    watchlist_snapshot_time: str = "15:30"

    # ---- 投资组合价格每日刷新 ----
    # 收盘后把全部资产的最新价刷成当日价。测试环境同样要设为 false。
    asset_price_refresh_enabled: bool = True
    # 15:00 收盘 → 15:05 取到的是收盘价；比观察池快照早 25 分钟，避免同时打接口
    asset_price_refresh_time: str = "15:05"

    # ---- 每日日报（spec 004）----
    # 两份日报各一个定时任务，排在「价格刷新 15:05」「观察池快照 15:30」之后：
    # 日报要读的正是这两个任务刚刚写好的收盘价。
    # 测试环境必须设为 false（否则会起后台任务并发真实行情请求）。
    daily_report_portfolio_enabled: bool = True
    daily_report_portfolio_time: str = "15:40"
    daily_report_watchlist_enabled: bool = True
    daily_report_watchlist_time: str = "15:45"
    # 观察池没有真实持股数，「今日盈亏」按等权测算：假设每只标的投这么多钱。
    # 只是测算基准，不改任何库里的数据。
    daily_report_watchlist_notional: float = 10000.0

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
