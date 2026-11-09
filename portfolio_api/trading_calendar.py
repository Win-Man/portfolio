"""A 股交易日历：回答「今天该不该跑日报和定时任务」。

## 为什么不能只判断「周一~周五」

两件事会让星期几失效：

- **调休上班的周末**：周六被调成工作日，但 A 股照常休市；
- **横跨工作日的连休**：春节、国庆经常连着放 5~8 天，中间一大片工作日不开市。

所以必须拿交易所的真实日历，而不是自己算规则。

## 数据来源

akshare 的 `tool_trade_date_hist_sina()`：1990-12-19 起约 8800 个交易日，
**包含当年已公布到年底的日期**（实测覆盖到 12-31），拉一次约 0.2 秒。
日历落进 `trading_days` 表缓存，进程内再按日期缓存判定结果，
调度循环每分钟问 5 次也不会压到数据库。

## 三条判定规则

1. 日期在日历里 → **交易日**
2. 日期落在 [最早, 最晚] 区间内但不在日历 → **休市**（确定结论，可放心跳过）
3. 日期超出最晚日期 → **日历过期**：先尝试联网刷新；刷新后仍没覆盖，
   退化成「周一~周五」并记 warning。

第 3 条是刻意留的兜底。若把「不在日历里」一律当成休市，到了 2027 年元旦
日历还没更新时，**所有任务会永久静默跳过**，而界面上只显示「非交易日」——
这种故障最难查。宁可多跑一次，也不能整条链路无声停摆。

## 刷新时机

只在「目标日期超出日历覆盖范围」时刷新，并用 `REFRESH_RETRY_SECONDS` 节流
失败的尝试：日历一年只需真正更新一次，没必要每次 tick 都打接口。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy import delete, func
from sqlalchemy.orm import Session

from models import TradingDay

logger = logging.getLogger(__name__)

# 刷新失败后多久再试（秒）。成功不节流；失败要节流，否则接口挂了会每分钟重试。
REFRESH_RETRY_SECONDS = 30 * 60

# (最小日期, 最大日期, 条数)，进程内缓存，避免 tick 里反复 count
_span_cache: Optional[Tuple[Optional[date], Optional[date], int]] = None
# 日期 -> 是否交易日，避免同一个日期一天问上千次
_day_cache: dict = {}
# 上一次联网尝试的时间与结果
_last_attempt_at: Optional[datetime] = None
_last_attempt_ok: bool = False

WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


@dataclass(frozen=True)
class DayStatus:
    """某一天「开不开市」的结论，带上判断依据便于写进运行记录与界面。"""

    day: date
    is_trading: bool
    reason: str          # "交易日" / "周六" / "A股休市" / "日历未覆盖，按工作日判断"
    source: str          # calendar / weekday-fallback

    @property
    def weekday_cn(self) -> str:
        return WEEKDAY_CN[self.day.weekday()]


def _to_date(value) -> Optional[date]:
    """把 akshare 返回的 pandas Timestamp / datetime / date / str 统一成 date。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def reset_cache() -> None:
    """清空进程内缓存。测试用；日历刷新后也会调用。"""
    global _span_cache
    _span_cache = None
    _day_cache.clear()


def calendar_span(db: Session, use_cache: bool = True) -> Tuple[Optional[date], Optional[date], int]:
    """(最早, 最晚, 条数)。空表返回 (None, None, 0)。"""
    global _span_cache
    if use_cache and _span_cache is not None:
        return _span_cache
    row = db.query(
        func.min(TradingDay.trade_date),
        func.max(TradingDay.trade_date),
        func.count(TradingDay.trade_date),
    ).one()
    first, last = _to_date(row[0]), _to_date(row[1])
    span = (first, last, int(row[2] or 0))
    _span_cache = span
    return span


def fetch_trade_dates() -> List[date]:
    """从 akshare 拉取全部交易日。网络/解析异常直接抛给调用方处理。"""
    import akshare as ak  # 延迟导入：akshare 会连带 pandas，启动时不必付这个成本

    df = ak.tool_trade_date_hist_sina()
    dates = [_to_date(v) for v in df.iloc[:, 0].tolist()]
    return sorted({d for d in dates if d is not None})


def refresh_calendar(db: Session, force: bool = False) -> Optional[int]:
    """联网拉取并**全量替换** `trading_days`。返回写入条数，失败返回 None。

    全量替换而不是增量追加：日历是幂等的全集，增量合并会留下旧数据里的错误日期。
    先删后插放在同一事务里，失败整体回滚，不会留下半张空表。
    """
    global _last_attempt_at, _last_attempt_ok
    now = datetime.now()
    if not force and _last_attempt_at is not None:
        elapsed = (now - _last_attempt_at).total_seconds()
        if not _last_attempt_ok and elapsed < REFRESH_RETRY_SECONDS:
            return None

    _last_attempt_at = now
    try:
        dates = fetch_trade_dates()
        if not dates:
            raise ValueError("交易日历为空")
        db.execute(delete(TradingDay))
        db.bulk_save_objects([TradingDay(trade_date=d) for d in dates])
        db.commit()
    except Exception as exc:
        db.rollback()
        _last_attempt_ok = False
        logger.warning(
            "交易日历刷新失败（%s: %s），本次按日历原有内容判断", type(exc).__name__, exc
        )
        return None

    _last_attempt_ok = True
    reset_cache()
    logger.info("交易日历已刷新：%s ~ %s，共 %d 个交易日", dates[0], dates[-1], len(dates))
    return len(dates)


def ensure_calendar(db: Session, target: date) -> None:
    """保证日历覆盖到 target。不覆盖就尝试刷新（受节流限制，绝不抛异常）。"""
    try:
        _, last, count = calendar_span(db)
        if count and last is not None and last >= target:
            return
        refresh_calendar(db)
    except Exception:                     # 建表失败 / DB 锁 / 任何意外都不能拖垮调度
        logger.exception("交易日历检查异常，按工作日规则判断")


def has_date(db: Session, day: date) -> bool:
    return db.query(TradingDay.trade_date).filter(TradingDay.trade_date == day).first() is not None


def day_status(day: date, db: Optional[Session] = None) -> DayStatus:
    """判断某一天是否开市。db 省略时自建会话（调用方已有会话请传进来复用）。"""
    cached = _day_cache.get(day)
    if cached is not None:
        return cached

    own_session = db is None
    session = db
    if own_session:
        from database import SessionLocal  # 延迟导入，避免与 database 循环引用

        session = SessionLocal()
    status: DayStatus
    try:
        first, last, count = calendar_span(session)
        if count == 0 or last is None:
            # 从来没成功拉过日历：先试一次，失败就按工作日判断
            ensure_calendar(session, day)
            first, last, count = calendar_span(session, use_cache=False)

        if count and first is not None and last is not None and first <= day <= last:
            if has_date(session, day):
                status = DayStatus(day, True, "交易日", "calendar")
            else:
                status = DayStatus(day, False, f"{WEEKDAY_CN[day.weekday()]}，A股休市", "calendar")
        else:
            # 超出日历覆盖（未来太远或历史太早）：退化成工作日判断
            ensure_calendar(session, day)
            first, last, count = calendar_span(session, use_cache=False)
            if count and first is not None and last is not None and first <= day <= last:
                status = day_status(day, session)
            else:
                is_workday = day.weekday() < 5
                status = DayStatus(
                    day,
                    is_workday,
                    f"{WEEKDAY_CN[day.weekday()]}，交易日历未覆盖，按工作日判断",
                    "weekday-fallback",
                )
    finally:
        if own_session and session is not None:
            session.close()

    _day_cache[day] = status
    return status


def is_trading_day(day: Optional[date] = None, db: Optional[Session] = None) -> bool:
    """今天（或指定日）是否开市。任何异常都返回 True —— 宁可多跑，不要静默停摆。"""
    target = day or date.today()
    try:
        return day_status(target, db).is_trading
    except Exception:
        logger.exception("交易日判断异常，按交易日处理")
        return True


def next_trading_day(day: date, db: Optional[Session] = None, limit: int = 60) -> Optional[date]:
    """`day` 之后（不含 day 本身）的第一个交易日。找不到返回 None。

    多放一天余量：日历可能只覆盖到本年度。函数内部会在越界时尝试刷新。
    """
    cursor = day + timedelta(days=1)
    for _ in range(limit):
        if is_trading_day(cursor, db):
            return cursor
        cursor += timedelta(days=1)
    return None


def consecutive_off_days(day: date, db: Optional[Session] = None, limit: int = 15) -> int:
    """从 day 往回数连续休市天数（day 本身若开市则为 0）。给界面补一句「连休 N 天」。"""
    own_session = db is None
    session = db
    if own_session:
        from database import SessionLocal

        session = SessionLocal()
    try:
        count = 0
        cursor = day
        while count < limit and not is_trading_day(cursor, session):
            count += 1
            cursor -= timedelta(days=1)
        return count
    finally:
        if own_session and session is not None:
            session.close()
