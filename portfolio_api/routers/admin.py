"""后台管理 API：定时任务的配置、最后运行时间、历史运行记录、手动触发，
以及 SQLite 数据库的体积统计与备份。

任务这一部分刻意只做「读 + 触发」，不做「改配置」：
- 计划时刻与开关在 `.env`（`WATCHLIST_SNAPSHOT_TIME` / `ASSET_PRICE_REFRESH_TIME` 等），
  进程启动时读取。做成运行时可变就得额外落一张配置表、还要处理「改了之后
  哪些进程知道自己该重读」，对一个自己用的工具不值得；
- 手动触发是真正需要的：等不到 15:05 想立刻更新价格，或者失败后想重试。

数据库这一部分提供「看占了多大 + 备份 / 下载 / 删除」：备份是自建库最常见的运维需求，
但没有自动清理 —— 什么时候删由用户决定，接口只如实报告占用（见 `db_backup.py`）。

时间口径：`job_runs.started_at / finished_at` 与备份文件的 mtime 都是
Asia/Shanghai 的墙上时间（naive），返回给前端也不带时区 —— 直接按本地时间显示，
不做换算。
"""
import json
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

import db_backup
from database import get_db
from models import JobRun
from schemas import (
    BackupInfo,
    BackupListResponse,
    DatabaseInfoResponse,
    JobInfoResponse,
    JobRunListResponse,
    JobRunResponse,
)
from scheduler import (
    execute_job,
    get_job_spec,
    get_job_specs,
    next_run_at,
    now_local,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


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
    """两个定时任务的配置 + 最后运行时间 + 下次预计运行时间。"""
    now = now_local()
    result: List[JobInfoResponse] = []

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
                next_run_at=next_run_at(spec, now).replace(tzinfo=None) if spec.enabled else None,
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
