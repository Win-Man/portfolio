"""A 股交易日历 + 「只在交易日运行」的调度规则。

本文件打 `real_trading_calendar` 标记：conftest 里「一律视为交易日」的夹具会跳过，
用例自己往 `trading_days` 表塞数据、调用真实实现。

**绝不联网**：需要刷新日历的用例都 patch 掉 `fetch_trade_dates`，
其余用例 patch `refresh_calendar` 为 no-op（否则「日历没覆盖」的分支会真的去打 akshare）。
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import scheduler as sched
import trading_calendar as tc
from config import settings
from database import SessionLocal
from models import JobRun, TradingDay

pytestmark = pytest.mark.real_trading_calendar

SHANGHAI = ZoneInfo("Asia/Shanghai")


# ---------- 工具 ----------

def seed(db, days):
    """把日历表替换成给定的日期集合。"""
    db.query(TradingDay).delete()
    for day in days:
        db.add(TradingDay(trade_date=day))
    db.commit()
    tc.reset_cache()


def seed_workdays(db, start: date, end: date):
    """填一段「周一~周五」的日期，用于需要覆盖真实今天的场景（如 lifespan 预热）。"""
    days, cursor = [], start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    seed(db, days)


def run_rows():
    db = SessionLocal()
    try:
        return db.query(JobRun).order_by(JobRun.id.asc()).all()
    finally:
        db.close()


@pytest.fixture
def offline(monkeypatch):
    """任何联网刷新都变成 no-op —— 纯判断逻辑的用例不需要日历真的更新。"""
    monkeypatch.setattr(tc, "refresh_calendar", lambda db, force=False: None)


@pytest.fixture
def only_watchlist_job(monkeypatch):
    """只留观察池快照一个任务，且时刻早于测试用时间点。"""
    monkeypatch.setattr(settings, "asset_price_refresh_enabled", False)
    monkeypatch.setattr(settings, "daily_report_portfolio_enabled", False)
    monkeypatch.setattr(settings, "daily_report_watchlist_enabled", False)
    monkeypatch.setattr(settings, "daily_report_mail_enabled", False)
    monkeypatch.setattr(settings, "watchlist_snapshot_enabled", True)
    monkeypatch.setattr(settings, "watchlist_snapshot_time", "00:00")


# ---------- 日历判断 ----------

def test_weekday_inside_calendar_is_trading(db_session):
    seed(db_session, [date(2026, 11, 6), date(2026, 11, 9)])   # 周五、下周一

    status = tc.day_status(date(2026, 11, 6), db_session)

    assert status.is_trading is True
    assert status.source == "calendar"
    assert status.reason == "交易日"
    assert status.weekday_cn == "周五"


def test_weekend_inside_calendar_is_closed(db_session):
    """周六落在日历覆盖区间内、但不在交易日集合里 → 确定休市。"""
    seed(db_session, [date(2026, 11, 6), date(2026, 11, 9)])   # 不含 11-07 周六

    status = tc.day_status(date(2026, 11, 7), db_session)

    assert status.is_trading is False
    assert status.source == "calendar"
    assert "周六" in status.reason and "A股休市" in status.reason


def test_holiday_inside_calendar_is_closed(db_session):
    """国庆连休：10-01 是周四，但不在日历里 → 休市而不是「周四当然开市」。"""
    seed(db_session, [date(2026, 9, 30), date(2026, 10, 8)])

    status = tc.day_status(date(2026, 10, 1), db_session)

    assert status.is_trading is False
    assert "周四" in status.reason
    assert status.source == "calendar"


def test_beyond_calendar_falls_back_to_weekday(db_session, offline):
    """超出日历最晚日期：不能当成休市（那是 2027 年静默停摆的根源），退化成工作日。"""
    seed(db_session, [date(2026, 11, 6)])

    friday = tc.day_status(date(2026, 11, 13), db_session)
    saturday = tc.day_status(date(2026, 11, 14), db_session)

    assert friday.is_trading is True
    assert friday.source == "weekday-fallback"
    assert "未覆盖" in friday.reason
    assert saturday.is_trading is False
    assert saturday.source == "weekday-fallback"


def test_empty_calendar_falls_back_to_weekday(db_session, offline):
    """日历一条都没有（首次启动、刷新失败）：按工作日判断，不能一律休市。"""
    tc.reset_cache()

    assert tc.day_status(date(2026, 11, 9), db_session).is_trading is True    # 周一
    assert tc.day_status(date(2026, 11, 8), db_session).is_trading is False   # 周日


def test_day_status_is_cached(db_session, offline):
    """同一个日期判上千次也只算一次（调度每分钟问 5 个任务）。"""
    seed(db_session, [date(2026, 11, 6)])
    first = tc.day_status(date(2026, 11, 6), db_session)

    # 清表但**不**清进程内缓存：若缓存没生效，会重新查询并翻转成 fallback 结论
    db_session.query(TradingDay).delete()
    db_session.commit()
    second = tc.day_status(date(2026, 11, 6), db_session)

    assert first == second
    assert second.source == "calendar"


def test_is_trading_day_defaults_to_today(db_session, offline):
    seed(db_session, [date.today()])

    assert tc.is_trading_day(db=db_session) is True


def test_is_trading_day_swallows_errors(monkeypatch):
    """判断本身出问题（表没建、库锁）时按「要跑」处理：宁可多跑，不可静默停摆。"""
    monkeypatch.setattr(
        tc, "day_status", lambda day=None, db=None: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    assert tc.is_trading_day(date(2026, 11, 7)) is True


# ---------- 相邻日期推算 ----------

def test_next_trading_day_skips_weekend(db_session, offline):
    seed(db_session, [date(2026, 11, 6), date(2026, 11, 9)])

    assert tc.next_trading_day(date(2026, 11, 7), db_session) == date(2026, 11, 9)


def test_next_trading_day_skips_holiday_stretch(db_session, offline):
    """国庆 10-01~10-07 连休 → 下一个交易日是 10-08。"""
    seed(db_session, [date(2026, 9, 30), date(2026, 10, 8)])

    assert tc.next_trading_day(date(2026, 10, 1), db_session) == date(2026, 10, 8)


def test_consecutive_off_days(db_session, offline):
    """10-01~10-07 连休：从 10-07 往回数应得 7 天。"""
    seed(db_session, [date(2026, 9, 30), date(2026, 10, 8)])

    assert tc.consecutive_off_days(date(2026, 10, 7), db_session) == 7
    assert tc.consecutive_off_days(date(2026, 9, 30), db_session) == 0


# ---------- 日历刷新 ----------

def test_refresh_replaces_whole_calendar(db_session, monkeypatch):
    """全量替换而不是追加：旧数据里的错误日期必须被清掉。"""
    seed(db_session, [date(2020, 1, 1)])                      # 一条「脏」旧数据
    monkeypatch.setattr(
        tc, "fetch_trade_dates", lambda: [date(2026, 11, 6), date(2026, 11, 9)]
    )

    assert tc.refresh_calendar(db_session, force=True) == 2

    rows = {r.trade_date for r in db_session.query(TradingDay).all()}
    assert rows == {date(2026, 11, 6), date(2026, 11, 9)}


def test_refresh_failure_keeps_existing_calendar(db_session, monkeypatch):
    """网络挂掉时不能把已有日历删成空表 —— 那会让今天变成「日历没覆盖」。"""
    seed(db_session, [date(2026, 11, 6)])

    def boom():
        raise RuntimeError("网络不通")

    monkeypatch.setattr(tc, "fetch_trade_dates", boom)

    assert tc.refresh_calendar(db_session, force=True) is None
    rows = {r.trade_date for r in db_session.query(TradingDay).all()}
    assert rows == {date(2026, 11, 6)}


def test_refresh_rejects_empty_result(db_session, monkeypatch):
    """拉到空列表（接口改了字段名）不算成功，否则日历会被清空。"""
    seed(db_session, [date(2026, 11, 6)])
    monkeypatch.setattr(tc, "fetch_trade_dates", lambda: [])

    assert tc.refresh_calendar(db_session, force=True) is None
    assert db_session.query(TradingDay).count() == 1


def test_refresh_is_throttled_after_failure(db_session, monkeypatch):
    """失败后短时间内不再重试：调度每 60 秒一次，不能每次都打接口。"""
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("网络不通")

    monkeypatch.setattr(tc, "fetch_trade_dates", boom)
    monkeypatch.setattr(tc, "_last_attempt_at", None)
    monkeypatch.setattr(tc, "_last_attempt_ok", False)

    tc.refresh_calendar(db_session, force=False)
    tc.refresh_calendar(db_session, force=False)

    assert len(calls) == 1


# ---------- 调度：非交易日跳过 ----------

async def test_tick_records_skip_on_off_day(only_watchlist_job):
    db = SessionLocal()
    try:
        seed(db, [date(2026, 11, 6), date(2026, 11, 9)])       # 不含 11-07 周六
    finally:
        db.close()

    runs = await sched.tick(datetime(2026, 11, 7, 16, 0, tzinfo=SHANGHAI))

    assert len(runs) == 1
    rows = run_rows()
    assert len(rows) == 1
    assert rows[0].status == "skipped"
    assert "非交易日" in rows[0].message
    assert "周六" in rows[0].message


async def test_tick_runs_on_trading_day(only_watchlist_job, monkeypatch):
    """交易日不该被拦：观察池为空时任务自己会报 skipped，但原因不是「非交易日」。"""
    db = SessionLocal()
    try:
        seed(db, [date(2026, 11, 6)])
    finally:
        db.close()

    await sched.tick(datetime(2026, 11, 6, 16, 0, tzinfo=SHANGHAI))

    rows = run_rows()
    assert len(rows) == 1
    assert "非交易日" not in rows[0].message


async def test_skip_is_recorded_once_per_day(only_watchlist_job):
    """非交易日也只留一条记录：否则周六周日各攒 1400 条垃圾。"""
    db = SessionLocal()
    try:
        seed(db, [date(2026, 11, 6), date(2026, 11, 9)])
    finally:
        db.close()

    await sched.tick(datetime(2026, 11, 7, 16, 0, tzinfo=SHANGHAI))
    again = await sched.tick(datetime(2026, 11, 7, 16, 1, tzinfo=SHANGHAI))

    assert again == []
    assert len(run_rows()) == 1


async def test_manual_trigger_ignores_trading_day(only_watchlist_job):
    """手动触发不按日历拦：用户点了按钮就是想现在跑（周末补数据也很常见）。"""
    db = SessionLocal()
    try:
        seed(db, [date(2026, 11, 6), date(2026, 11, 9)])       # 11-07 休市
    finally:
        db.close()

    session = SessionLocal()
    try:
        run = await sched.execute_job(
            "watchlist_snapshot",
            trigger="manual",
            db=session,
            today=date(2026, 11, 7),
        )
        message = run.message
    finally:
        session.close()

    assert "非交易日" not in message


async def test_trading_day_only_can_be_disabled(only_watchlist_job, monkeypatch):
    """SCHEDULE_TRADING_DAY_ONLY=false 时，周末照常跑。"""
    monkeypatch.setattr(settings, "schedule_trading_day_only", False)
    db = SessionLocal()
    try:
        seed(db, [date(2026, 11, 6), date(2026, 11, 9)])
    finally:
        db.close()

    await sched.tick(datetime(2026, 11, 7, 16, 0, tzinfo=SHANGHAI))

    rows = run_rows()
    assert len(rows) == 1
    assert "非交易日" not in rows[0].message


# ---------- 下次运行时刻 ----------

def test_next_run_at_skips_weekend(db_session, offline):
    seed(db_session, [date(2026, 11, 6), date(2026, 11, 9)])
    judge = lambda day: tc.is_trading_day(day, db_session)     # noqa: E731
    spec = sched.get_job_spec("watchlist_snapshot")

    # 周五 16:00，今天已过 15:30 → 不是周六，而是下周一
    assert sched.next_run_at(spec, datetime(2026, 11, 6, 16, 0, tzinfo=SHANGHAI), judge) == (
        datetime(2026, 11, 9, 15, 30, tzinfo=SHANGHAI)
    )


def test_next_run_at_skips_holiday_stretch(db_session, offline):
    seed(db_session, [date(2026, 9, 30), date(2026, 10, 8)])
    judge = lambda day: tc.is_trading_day(day, db_session)     # noqa: E731
    spec = sched.get_job_spec("watchlist_snapshot")

    assert sched.next_run_at(spec, datetime(2026, 10, 1, 12, 0, tzinfo=SHANGHAI), judge) == (
        datetime(2026, 10, 8, 15, 30, tzinfo=SHANGHAI)
    )


def test_next_run_at_falls_back_when_no_trading_day_found(monkeypatch):
    """日历与规则都给不出交易日时返回窗口末尾，不抛异常、不死循环。"""
    spec = sched.get_job_spec("watchlist_snapshot")

    result = sched.next_run_at(
        spec,
        datetime(2026, 11, 6, 16, 0, tzinfo=SHANGHAI),
        lambda day: False,
    )

    # 周五 16:00 已过 15:30 → 从周六起找，找满 60 天仍无交易日
    assert result == datetime(2026, 11, 7, 15, 30, tzinfo=SHANGHAI) + timedelta(
        days=sched.NEXT_RUN_LOOKAHEAD_DAYS
    )


def test_schedule_label_mentions_trading_day():
    spec = sched.get_job_spec("daily_report_mail")

    assert spec.schedule_label == "交易日 15:50（Asia/Shanghai）"


# ---------- 接口 ----------

def test_trading_calendar_endpoint(client, db_session):
    """注意：client 会触发 lifespan 预热，所以先塞一段覆盖今天的数据，避免联网。"""
    seed_workdays(db_session, date(2026, 10, 1), date(2026, 12, 31))

    body = client.get("/api/admin/trading-calendar?days=5").json()

    assert body["trading_day_only"] is True
    assert body["calendar_count"] > 0
    assert body["calendar_stale"] is False
    assert body["today"]["day"] == date.today().isoformat()
    assert len(body["upcoming"]) == 5
    assert body["upcoming"][0]["weekday"].startswith("周")


def test_trading_calendar_reports_off_day(client, db_session):
    """今天若休市，note 要说清「下一次什么时候跑」。"""
    seed_workdays(db_session, date(2026, 10, 1), date(2026, 12, 31))

    body = client.get("/api/admin/trading-calendar").json()

    if body["today"]["is_trading"]:
        assert "交易日" in body["note"]
    else:
        assert "休市" in body["note"]
        assert "下一个交易日" in body["note"]


def test_trading_calendar_stale_flag(client):
    """日历一条都没有、又刷不到 → stale=true，界面提示用户去刷新。"""
    body = client.get("/api/admin/trading-calendar").json()

    assert body["calendar_stale"] is True
    assert body["calendar_count"] == 0
