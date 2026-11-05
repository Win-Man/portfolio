"""每日日报 API：列表 / 详情 / 立即生成 / 删除。

设计取舍：

- **列表不带 payload**：一页 30 份报告，每份 payload 几十 KB（含几十行明细），
  全量下发会让首屏白等；列表只需要日期 + 今日盈亏 + 信号数，这些是落库时
  冗余存好的列；
- **详情按 id 取一份**：payload 交给前端按自身字段渲染，旧报告缺新字段时
  前端兜底显示「—」，不需要为加字段迁移历史数据；
- **只支持「今天」生成**：历史日期算不出昨收（那需要当日行情的历史快照），
  硬生成只会得到一份「今天价格冒充历史价格」的假报告，不如不提供。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models import DailyReport, Portfolio
from daily_reports import (
    REPORT_TYPES,
    WATCHLIST_SCOPE_ID,
    generate_portfolio_reports,
    generate_watchlist_report,
    report_payload,
)
from schemas import (
    DailyReportBrief,
    DailyReportDetail,
    DailyReportGenerateRequest,
    DailyReportListResponse,
)

router = APIRouter(prefix="/api/reports", tags=["reports"])

MAX_LIMIT = 100


def _validate_type(report_type: str) -> str:
    if report_type not in REPORT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"report_type 只能是 {' / '.join(REPORT_TYPES)}",
        )
    return report_type


def _resolve_scope(report_type: str, portfolio_id: Optional[int]) -> int:
    """把查询参数翻译成 scope_id，并校验组合是否存在。"""
    if report_type == "watchlist":
        return WATCHLIST_SCOPE_ID
    if portfolio_id is None:
        raise HTTPException(status_code=422, detail="组合日报需要指定 portfolio_id")
    return int(portfolio_id)


def _to_brief(row: DailyReport) -> DailyReportBrief:
    return DailyReportBrief(
        id=row.id,
        report_type=row.report_type,
        scope_id=row.scope_id,
        report_date=row.report_date,
        title=row.title or "",
        total_pnl=row.total_pnl or 0,
        total_pnl_percent=row.total_pnl_percent,
        item_count=row.item_count or 0,
        signal_count=row.signal_count or 0,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_detail(row: DailyReport) -> DailyReportDetail:
    return DailyReportDetail(
        **_to_brief(row).model_dump(),
        payload=report_payload(row),
    )


@router.get("", response_model=DailyReportListResponse)
def list_reports(
    report_type: str = Query(default="portfolio"),
    portfolio_id: Optional[int] = Query(default=None),
    limit: int = Query(default=30, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """某类日报的历史列表，新的在前。"""
    report_type = _validate_type(report_type)
    scope_id = _resolve_scope(report_type, portfolio_id)

    base = db.query(DailyReport).filter(
        DailyReport.report_type == report_type,
        DailyReport.scope_id == scope_id,
    )
    total = base.count()
    rows = (
        base.order_by(DailyReport.report_date.desc(), DailyReport.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return DailyReportListResponse(
        items=[_to_brief(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# ⚠ 必须排在 /{report_id} 之前：虽然 report_id 是 int（"latest" 会被类型校验挡掉），
# 但路由匹配顺序仍按声明顺序，先声明可读性更好、也不会因为将来改类型而踩坑。
@router.get("/latest", response_model=DailyReportDetail)
def latest_report(
    report_type: str = Query(default="portfolio"),
    portfolio_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    """最近一份日报。没有生成过 → 404（而不是空对象，避免前端误以为「今天没亏没赚」）。"""
    report_type = _validate_type(report_type)
    scope_id = _resolve_scope(report_type, portfolio_id)

    row = (
        db.query(DailyReport)
        .filter(
            DailyReport.report_type == report_type,
            DailyReport.scope_id == scope_id,
        )
        .order_by(DailyReport.report_date.desc(), DailyReport.id.desc())
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="还没有生成过日报")
    return _to_detail(row)


@router.get("/{report_id}", response_model=DailyReportDetail)
def get_report(report_id: int, db: Session = Depends(get_db)):
    row = db.query(DailyReport).filter(DailyReport.id == report_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="日报不存在")
    return _to_detail(row)


@router.post("/generate", response_model=DailyReportListResponse)
async def generate_report(
    payload: DailyReportGenerateRequest, db: Session = Depends(get_db)
):
    """立即生成今天的日报（重复生成 = 覆盖当天那一份）。

    组合日报不给 `portfolio_id` 时生成**全部组合**各一份：
    定时任务就是这么用的（一次跑完所有组合）。
    """
    _validate_type(payload.report_type)

    if payload.report_type == "watchlist":
        row = await generate_watchlist_report(db)
        rows = [row] if row is not None else []
    else:
        if payload.portfolio_id is not None:
            exists = (
                db.query(Portfolio.id)
                .filter(Portfolio.id == payload.portfolio_id)
                .first()
            )
            if exists is None:
                raise HTTPException(status_code=404, detail="Portfolio not found")
        rows = await generate_portfolio_reports(db, portfolio_id=payload.portfolio_id)

    return DailyReportListResponse(
        items=[_to_brief(r) for r in rows],
        total=len(rows),
        limit=len(rows),
        offset=0,
    )


@router.delete("/{report_id}")
def delete_report(report_id: int, db: Session = Depends(get_db)):
    """删一份日报（生成参数写错后清理用，不影响任何资产/观察池数据）。"""
    row = db.query(DailyReport).filter(DailyReport.id == report_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="日报不存在")
    db.delete(row)
    db.commit()
    return {"message": "Daily report deleted"}
