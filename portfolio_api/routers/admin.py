"""后台管理 API：定时任务的配置、最后运行时间、历史运行记录、手动触发，
SQLite 数据库的体积统计与备份，以及日报邮件的配置状态与手动推送。

任务这一部分刻意只做「读 + 触发」，不做「改配置」：
- 计划时刻与开关在 `.env`（`WATCHLIST_SNAPSHOT_TIME` / `ASSET_PRICE_REFRESH_TIME` 等），
  进程启动时读取。做成运行时可变就得额外落一张配置表、还要处理「改了之后
  哪些进程知道自己该重读」，对一个自己用的工具不值得；
- 手动触发是真正需要的：等不到 15:05 想立刻更新价格，或者失败后想重试。

数据库这一部分提供「看占了多大 + 备份 / 下载 / 删除」：备份是自建库最常见的运维需求，
但没有自动清理 —— 什么时候删由用户决定，接口只如实报告占用（见 `db_backup.py`）。

邮件这一部分同样是「读配置 + 触发发送」：密码只进不出，读接口返回打码后的账号，
写操作只有「发测试邮件 / 发某天的日报」，不提供改密码的接口（改 `.env` 重启更安全）。

时间口径：`job_runs.started_at / finished_at` 与备份文件的 mtime 都是
Asia/Shanghai 的墙上时间（naive），返回给前端也不带时区 —— 直接按本地时间显示，
不做换算。
"""
import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

import db_backup
import report_mail
import trading_calendar
from config import settings
from database import get_db
from models import JobRun
from schemas import (
    BackupInfo,
    BackupListResponse,
    DatabaseInfoResponse,
    JobInfoResponse,
    JobRunListResponse,
    JobRunResponse,
    MailConfigResponse,
    MailSendResponse,
    TradingCalendarResponse,
    TradingDayInfo,
)
from scheduler import (
    execute_job,
    get_job_spec,
    get_job_specs,
    next_run_at,
    now_local,
    parse_hhmm,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

logger = logging.getLogger(__name__)


def _parse_errors(raw: Optional[str]) -> List[str]:
    """errors 列是 JSON 数组文本；脏数据不能让整个列表 500。"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return [str(raw)]
    if isinstance(data, list):
        return [str(x) for x in data]
    return [str(data)]


def _to_response(run: Optional[JobRun]) -> Optional[JobRunResponse]:
    if run is None:
        return None
    # 秒级就够：微秒会让前端 / 日志都变长，且对「最后运行时间」没有任何信息增量
    started = run.started_at.replace(microsecond=0) if run.started_at else None
    finished = run.finished_at.replace(microsecond=0) if run.finished_at else None
    return JobRunResponse(
        id=run.id,
        job_key=run.job_key,
        trigger=run.trigger or "schedule",
        status=run.status or "success",
        started_at=started,
        finished_at=finished,
        duration_ms=run.duration_ms or 0,
        total=run.total or 0,
        succeeded=run.succeeded or 0,
        failed=run.failed or 0,
        message=run.message or "",
        errors=_parse_errors(run.errors),
    )


def _latest_run(db: Session, job_key: str, status: Optional[str] = None) -> Optional[JobRun]:
    query = db.query(JobRun).filter(JobRun.job_key == job_key)
    if status is not None:
        query = query.filter(JobRun.status == status)
    # started_at 相同时用 id 兜底：同一秒内连续触发（手动点了两次）也能稳定排序
    return query.order_by(JobRun.started_at.desc(), JobRun.id.desc()).first()


@router.get("/jobs", response_model=List[JobInfoResponse])
def list_jobs(db: Session = Depends(get_db)):
    """定时任务的配置 + 最后运行时间 + 下次预计运行时间。"""
    now = now_local()
    result: List[JobInfoResponse] = []
    # 复用同一个会话做交易日判断：一次请求里 5 个任务各查一遍日历，
    # 每次新建 session 在 SQLite 上没意义（还会多占文件句柄）。
    judge = lambda day: trading_calendar.is_trading_day(day, db)  # noqa: E731

    for spec in get_job_specs().values():
        counts = dict(
            db.query(JobRun.status, func.count(JobRun.id))
            .filter(JobRun.job_key == spec.key)
            .group_by(JobRun.status)
            .all()
        )
        last = _latest_run(db, spec.key)
        last_success = _latest_run(db, spec.key, status="success")
        last_errors = _parse_errors(last.errors) if last is not None else []

        result.append(
            JobInfoResponse(
                key=spec.key,
                name=spec.name,
                description=spec.description,
                enabled=spec.enabled,
                schedule_time=spec.schedule_time.strftime("%H:%M"),
                schedule_label=spec.schedule_label,
                timezone="Asia/Shanghai",
                # 去掉 tzinfo：整页统一「Asia/Shanghai 墙上时间」，前端不做换算
                next_run_at=(
                    next_run_at(spec, now, judge).replace(tzinfo=None) if spec.enabled else None
                ),
                last_run=_to_response(last),
                last_success_at=(
                    last_success.started_at.replace(microsecond=0) if last_success else None
                ),
                total_run_count=sum(counts.values()),
                status_counts={str(k): int(v) for k, v in counts.items()},
                errors_hint=last_errors[0] if last_errors else None,
            )
        )

    return result


@router.get("/jobs/{job_key}/runs", response_model=JobRunListResponse)
def list_job_runs(
    job_key: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """运行历史，新的在前。"""
    if get_job_spec(job_key) is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    base = db.query(JobRun).filter(JobRun.job_key == job_key)
    total = base.count()
    rows = (
        base.order_by(JobRun.started_at.desc(), JobRun.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return JobRunListResponse(
        items=[_to_response(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/jobs/{job_key}/run", response_model=JobRunResponse)
async def run_job_now(job_key: str, db: Session = Depends(get_db)):
    """立即执行一次（不受计划时刻与开关限制，但仍然如实记一条 manual 记录）。"""
    if get_job_spec(job_key) is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    run = await execute_job(job_key, trigger="manual", db=db)
    if run is None:  # 手动触发的 skipped 也会落库，正常不会走到这里
        raise HTTPException(status_code=500, detail="任务未产生运行记录")
    return _to_response(run)


# ---------- SQLite：体积与备份 ----------

@router.get("/database", response_model=DatabaseInfoResponse)
def get_database_info():
    """数据库文件大小、页数、各表占用、备份目录概况。

    不是 sqlite（或文件缺失）时也返回 200：`supported` / `exists` 为 false，
    前端据此显示「不可用」，而不是让整页崩掉。
    """
    return db_backup.database_overview()


@router.get("/database/backups", response_model=BackupListResponse)
def list_database_backups():
    """备份列表，新的在前。只返回本工具生成的文件（命名规则见 db_backup.py）。"""
    items: List[dict] = db_backup.list_backups()
    return BackupListResponse(
        items=items,
        total=len(items),
        total_size_bytes=sum(int(i["size_bytes"]) for i in items),
    )


@router.post("/database/backups", response_model=BackupInfo, status_code=201)
def create_database_backup():
    """立即备份一份（在线备份，不影响后台任务写入）。"""
    try:
        return db_backup.create_backup()
    except db_backup.DatabaseUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except db_backup.BackupFailed as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/database/backups/{name}")
def download_database_backup(name: str):
    """下载一份备份。文件名不合法 → 400，不存在 → 404。"""
    try:
        path: Path = db_backup.resolve_backup(name)
    except db_backup.InvalidBackupName:
        raise HTTPException(status_code=400, detail="备份文件名不合法")
    except db_backup.BackupNotFound:
        raise HTTPException(status_code=404, detail="备份不存在")
    return FileResponse(
        path,
        media_type="application/vnd.sqlite3",
        filename=path.name,
    )


@router.delete("/database/backups/{name}")
def delete_database_backup(name: str):
    """删除一份备份。不做自动清理 —— 删哪一份由用户决定。"""
    try:
        db_backup.delete_backup(name)
    except db_backup.InvalidBackupName:
        raise HTTPException(status_code=400, detail="备份文件名不合法")
    except db_backup.BackupNotFound:
        raise HTTPException(status_code=404, detail="备份不存在")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"删除失败：{exc}")
    return {"deleted": name}


# ---------- 日报邮件 ----------

def _mask_address(address: str) -> str:
    """825895587@qq.com → 825***@qq.com。

    读接口会出现在页面上、也可能被截图分享，完整账号没必要露出来；
    但保留前后几位，足够让用户确认「就是那个邮箱」。
    """
    text = (address or "").strip()
    if "@" not in text:
        return text
    local, _, domain = text.partition("@")
    if len(local) <= 3:
        masked = local[:1] + "***"
    else:
        masked = local[:3] + "***"
    return f"{masked}@{domain}"


@router.get("/mail", response_model=MailConfigResponse)
def get_mail_config():
    """邮件通道配置状态（不含密码）。前端据此决定按钮可不可点、提示缺什么。"""
    missing = settings.mail_missing_config
    # 时刻解析失败要回退而不是 500 —— 与调度器同一套容错
    schedule = parse_hhmm(
        settings.daily_report_mail_time, "15:50", "DAILY_REPORT_MAIL_TIME"
    ).strftime("%H:%M")
    # 只在交易日跑时不能说「每天」：用户周六看到「每天 15:50 自动发送」会白等一天
    when = "交易日" if settings.schedule_trading_day_only else "每天"

    if missing:
        # 只讲这张卡片「是干什么的」；缺什么、怎么配由页面上的提示框负责，
        # 两边都写一遍会让同一句话出现两次。
        note = (
            f"{when} {schedule} 把当天的组合日报与观察池日报合成一封 HTML 邮件发出；"
            "也可以在下面手动发送。"
        )
    else:
        note = f"已配置。{when} {schedule} 自动发送当日日报，也可在此手动发送。"

    return MailConfigResponse(
        configured=not missing,
        enabled=bool(settings.daily_report_mail_enabled),
        schedule_time=schedule,
        smtp_host=settings.mail_smtp_host,
        smtp_port=int(settings.mail_smtp_port),
        smtp_ssl=bool(settings.mail_smtp_ssl),
        username=_mask_address(settings.mail_username),
        from_name=settings.mail_from_name,
        recipients=settings.mail_recipient_list,
        subject_prefix=settings.mail_subject_prefix,
        missing=missing,
        note=note,
    )


@router.post("/mail/test", response_model=MailSendResponse)
async def send_mail_test():
    """发一封测试邮件：只验证 SMTP 通不通，不带任何持仓数据。"""
    try:
        info = await report_mail.send_test_mail()
    except report_mail.MailNotConfigured as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except report_mail.MailSendError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return MailSendResponse(
        success=True,
        message="测试邮件已发送，请查收（可能在垃圾箱）",
        recipients=list(info["recipients"]),  # type: ignore[arg-type]
    )


@router.post("/mail/send", response_model=MailSendResponse)
async def send_mail_now(
    report_date: Optional[date] = Query(default=None, description="默认今天"),
    db: Session = Depends(get_db),
):
    """立即把某一天的日报发出去。

    手动发送不受「定时任务开关」限制 —— 用户点按钮就是要发，
    但**未配置 SMTP** 时仍然 409（否则只会得到一个更晦涩的连接错误）。
    """
    try:
        info = await report_mail.send_daily_reports(db, report_date=report_date)
    except report_mail.MailNotConfigured as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except report_mail.NoReportsError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except report_mail.MailSendError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return MailSendResponse(
        success=True,
        message=f"已发送 {info['report_count']} 份日报",
        recipients=list(info["recipients"]),  # type: ignore[arg-type]
        report_count=int(info["report_count"]),  # type: ignore[arg-type]
        report_date=info["report_date"],  # type: ignore[arg-type]
        subject=str(info["subject"]),
    )


# ========== A 股交易日历 ==========

def _day_info(status: trading_calendar.DayStatus) -> TradingDayInfo:
    return TradingDayInfo(
        day=status.day,
        is_trading=status.is_trading,
        weekday=status.weekday_cn,
        reason=status.reason,
        source=status.source,
    )


def _off_day_phrase(status: trading_calendar.DayStatus) -> str:
    """「周六，A股休市」/「周四（法定节假日），A股休市」。

    工作日休市必然是节假日，补一句说明；周末就不用解释为什么不开市。
    刻意不让调用方再拼「今天休市」——和这里拼出来会变成「今天休市（周六，A股休市）」，
    同一句话里出现两次「休市」。
    """
    if status.day.weekday() >= 5:
        return f"{status.weekday_cn}，A股休市"
    return f"{status.weekday_cn}（法定节假日），A股休市"


def _calendar_note(today_status: trading_calendar.DayStatus, next_day: Optional[date]) -> str:
    """一句人话，说明「今天跑不跑、什么时候跑」。"""
    if not settings.schedule_trading_day_only:
        return "已关闭「仅交易日运行」：任务每天照常执行，节假日也跑。"
    if today_status.is_trading:
        return "今天是交易日，任务按计划时刻执行。"
    if next_day is None:
        return f"今天{_off_day_phrase(today_status)}，且未来交易日历不可用，请检查网络后刷新日历。"
    weekday = trading_calendar.WEEKDAY_CN[next_day.weekday()]
    return (
        f"今天{_off_day_phrase(today_status)}，定时任务不执行；"
        f"下一个交易日为 {next_day}（{weekday}）。"
    )


@router.get("/trading-calendar", response_model=TradingCalendarResponse)
def get_trading_calendar(
    days: int = Query(default=10, ge=0, le=30, description="往后展示多少天"),
    db: Session = Depends(get_db),
):
    """A 股交易日历状态：今天开不开市、日历覆盖范围、未来若干天。

    日历是缓存表，**不会**在这个读接口里联网刷新（否则页面一打开就等网络）。
    真过期了会返回 `calendar_stale=true`，用户点「刷新日历」或等服务启动时自动拉。
    """
    today = now_local().date()
    status = trading_calendar.day_status(today, db)
    first, last, count = trading_calendar.calendar_span(db)
    stale = count == 0 or (last is not None and last < today)

    upcoming: List[TradingDayInfo] = []
    cursor = today
    for _ in range(days):
        upcoming.append(_day_info(trading_calendar.day_status(cursor, db)))
        cursor += timedelta(days=1)

    next_day = None if status.is_trading else trading_calendar.next_trading_day(today, db)

    return TradingCalendarResponse(
        today=_day_info(status),
        trading_day_only=bool(settings.schedule_trading_day_only),
        calendar_start=first,
        calendar_end=last,
        calendar_count=count,
        calendar_stale=stale,
        note=_calendar_note(status, next_day),
        upcoming=upcoming,
    )


@router.post("/trading-calendar/refresh", response_model=TradingCalendarResponse)
def refresh_trading_calendar(
    days: int = Query(default=10, ge=0, le=30),
    db: Session = Depends(get_db),
):
    """强制联网刷新日历。刷新失败（网络不通）不会 500 —— 仍旧返回当前状态，
    并把 `calendar_stale` 置为 true，让界面告诉用户「没拉到，用的还是旧日历」。"""
    count = trading_calendar.refresh_calendar(db, force=True)
    if count is None:
        logger.warning("交易日历手动刷新失败，返回现有缓存")
    return get_trading_calendar(days=days, db=db)
