"""观察池每日快照定时任务（标准库 asyncio + zoneinfo，零新依赖，spec 003 §6.5）。

工作方式：

- 由 `main.py` 的 lifespan 用 `asyncio.create_task` 启动，关闭时 cancel 并 await 收尾；
- 循环体**先 tick 一次，再 sleep 60 秒**（服务启动即补抓，不等一分钟）；
- 每次 tick：到了配置时刻（默认 15:30 Asia/Shanghai）才干活；
  只处理「今天还没有快照」的标的，已写过的一次都不会重复抓取（D16 周末照常写）；
- 同一 trade_date 最多尝试 3 轮：外部源整体挂掉时，不至于每分钟重试一整天；
- 配置的时间串解析失败时回退 15:30 并记 warning —— **绝不允许启动即崩**。

单 worker 假设：`--workers N > 1` 时每个进程都会跑这个循环，同日 UPSERT 幂等
但会重复抓取（README 已注明）。本期不额外处理。
"""
import asyncio
import logging
from datetime import date, datetime, time as time_of_day
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from config import settings
from database import SessionLocal
from models import WatchlistItem, WatchlistSnapshot
from routers.watchlist import _refresh_one

logger = logging.getLogger(__name__)

SHANGHAI = ZoneInfo("Asia/Shanghai")

DEFAULT_SNAPSHOT_TIME = "15:30"
MAX_ATTEMPTS_PER_DAY = 3
POLL_INTERVAL_SECONDS = 60

# 每个 trade_date 已经尝试过几轮（模块级，避免外部源挂掉时每分钟重试）
_attempt_state: Dict[date, int] = {}


def parse_snapshot_time(raw: str) -> time_of_day:
    """解析 "HH:MM"。任何非法输入都回退 15:30（并记 warning），不抛异常。"""
    text = (raw or "").strip()
    try:
        hour_text, minute_text = text.split(":")
        hour, minute = int(hour_text), int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("out of range")
        return time_of_day(hour, minute)
    except Exception:
        logger.warning(
            "WATCHLIST_SNAPSHOT_TIME=%r 解析失败，回退 %s", raw, DEFAULT_SNAPSHOT_TIME
        )
        hour_text, minute_text = DEFAULT_SNAPSHOT_TIME.split(":")
        return time_of_day(int(hour_text), int(minute_text))


def _prune_attempt_state(today: date) -> None:
    """只保留今天的计数，避免跨天累积导致内存慢慢长大。"""
    for key in [d for d in _attempt_state if d != today]:
        _attempt_state.pop(key, None)


def items_missing_snapshot(db, today: date) -> List[WatchlistItem]:
    """今天还没有快照的标的 = 所有标的 − 已有 today 快照的 item_id。"""
    done = {
        row[0]
        for row in db.query(WatchlistSnapshot.item_id)
        .filter(WatchlistSnapshot.trade_date == today)
        .all()
    }
    return [
        item
        for item in db.query(WatchlistItem).order_by(WatchlistItem.id.asc()).all()
        if item.id not in done
    ]


async def tick(now: Optional[datetime] = None) -> int:
    """执行一轮；返回本轮实际尝试刷新的标的数（0 表示没到点 / 没缺失 / 已放弃）。"""
    if not settings.watchlist_snapshot_enabled:
        return 0

    current = now or datetime.now(SHANGHAI)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI)

    if current.time() < parse_snapshot_time(settings.watchlist_snapshot_time):
        return 0

    today = current.date()
    _prune_attempt_state(today)
    attempts = _attempt_state.get(today, 0)
    if attempts >= MAX_ATTEMPTS_PER_DAY:
        return 0

    db = SessionLocal()
    try:
        missing = items_missing_snapshot(db, today)
        if not missing:
            return 0

        _attempt_state[today] = attempts + 1
        logger.info(
            "watchlist scheduler: %d item(s) missing snapshot for %s (attempt %d/%d)",
            len(missing),
            today,
            attempts + 1,
            MAX_ATTEMPTS_PER_DAY,
        )
        for item in missing:
            try:
                error = await _refresh_one(db, item)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("watchlist scheduler refresh failed: code=%s", item.code)
                db.rollback()
                continue
            if error:
                logger.warning(
                    "watchlist scheduler: %s 未写入快照（%s）", item.code, error
                )
        return len(missing)
    finally:
        db.close()


async def run_scheduler() -> None:
    """无限循环：启动立刻 tick 一次，之后每 60 秒一次。"""
    logger.info(
        "watchlist scheduler started (time=%s, enabled=%s)",
        settings.watchlist_snapshot_time,
        settings.watchlist_snapshot_enabled,
    )
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # 任何异常都不能让后台任务静默死掉
            logger.exception("watchlist scheduler tick failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
