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

    # ---- 日报邮件推送 ----
    # 把当天已生成的日报（全部组合 + 观察池）合成一封邮件发出。
    # 排在 15:45 观察池日报之后，否则邮件里会缺一份日报。
    daily_report_mail_enabled: bool = True
    daily_report_mail_time: str = "15:50"

    # SMTP：默认按 QQ 邮箱的 SSL 端口。换其它服务商一般只需改 host / port / ssl。
    mail_smtp_host: str = "smtp.qq.com"
    mail_smtp_port: int = 465
    # true = 直接建 SSL 连接（465）；false = 明文连接后 STARTTLS（587）
    mail_smtp_ssl: bool = True
    # 登录账号（同时是默认发件人）。QQ 邮箱必须与「From」一致，否则被服务端拒收。
    mail_username: str = ""
    # ⚠ QQ/163 等邮箱这里是**授权码**，不是登录密码：邮箱设置 → 账户 →
    # 开启 POP3/SMTP 服务 → 生成授权码。填登录密码会报 535 认证失败。
    mail_password: str = ""
    # 发件人显示名
    mail_from_name: str = "投资组合日报"
    # 收件人，逗号/分号/空格分隔；留空则发给自己（mail_username）
    mail_recipients: str = ""
    # 主题前缀
    mail_subject_prefix: str = "投资日报"
    # SMTP 超时（秒）。卡住不如快速失败，留给调度重试
    mail_timeout_seconds: int = 20

    # ---- 交易日历（A 股开市日）----
    # 五个定时任务（价格刷新 / 观察池快照 / 组合日报 / 观察池日报 / 邮件）默认只在
    # **A 股交易日**执行：周末与法定节假日没有新行情，跑出来的快照是重复数据，
    # 日报的「今日盈亏」也恒为 0，除了污染走势图没有别的用处。
    # 交易日取自 akshare 的真实日历（能处理调休与连休），不是简单的「周一~周五」。
    # 需要周末也跑（调试、补数据）时设为 false。
    schedule_trading_day_only: bool = True
    # 行情日历缓存多久检查一次是否过期（秒）。日历一年只需真正更新一次，
    # 这里控制的是「目标日期超出日历覆盖时」的重试节流。
    trading_calendar_retry_seconds: int = 30 * 60

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

    @property
    def mail_recipient_list(self) -> List[str]:
        """收件人列表。留空 → 发给登录账号自己（这是最不容易配错的默认值）。

        分隔符统一成逗号再切：`a@x.com;b@x.com` 和 `a@x.com, b@x.com` 都认，
        但不去重（同一个人写两遍仍只发一封，由邮件服务端处理）。
        """
        raw = (self.mail_recipients or "").strip() or (self.mail_username or "").strip()
        for sep in (";", "，", "、", "\n", "\t", " "):
            raw = raw.replace(sep, ",")
        return [part.strip() for part in raw.split(",") if part.strip()]

    @property
    def mail_missing_config(self) -> List[str]:
        """缺哪些必填项（给前端显示「还差什么」，不抛异常）。"""
        missing = []
        if not (self.mail_username or "").strip():
            missing.append("MAIL_USERNAME")
        if not (self.mail_password or "").strip():
            missing.append("MAIL_PASSWORD")
        if not self.mail_recipient_list:
            missing.append("MAIL_RECIPIENTS")
        return missing

    @property
    def mail_configured(self) -> bool:
        return not self.mail_missing_config


settings = Settings()
