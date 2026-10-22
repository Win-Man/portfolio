"""收入日历（income calendar）。

把「当前组合」里各标的的**全部历史分红**按派息日（EX_DIVIDEND_DATE）归月，
再提供两种用户动作：
  • 点击确认：把 predicted/announced 强制标成 paid（幂等，只改状态不加金额）
  • 补录：手动新增一笔（akshare 漏收录 / 场外基金 / 历史补账）

口径要点（详见 docs/specs/002-income-calendar.md）：
  • 数据复用 routers/stocks.py 的 `_dividend_cache`（TTL 6h），**不另起缓存**
  • 金额 = quantity × DIVIDEND_PER_SHARE（原始每股分红，**不是** TTM 合计）
  • 超出 akshare 收录范围的未来月份 = ¥0，**不外推**
  • 状态机 predicted / announced / paid / manual；映射见 portfolio_math.classify_status
  • 同 (code, ex_date) 时补录记录**覆盖** akshare 记录
  • 成本字段（cost_price）与本功能完全无关，不参与任何计算
"""
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from market import normalize_code
from models import Asset, DividendConfirmation, ManualDividendRecord, Portfolio
from portfolio_math import (
    DIVIDEND_SOURCES,
    DIVIDEND_STATUSES,
    classify_source,
    classify_status,
)
from routers import stocks
# ex_date 的解析规则（date / 'YYYY-MM-DD' / 'YYYYMMDD' / 'YYYY/MM/DD'）与 TTM 口径
# 必须完全一致，否则同一笔分红在「历年分红 / TTM / 日历」三处会落到不同月份。
# 因此这里直接复用 assets.py 里的实现，不另写一份（assets.py 本身不改动）。
from routers.assets import _parse_dividend_date
from schemas import (
    CalendarMonth,
    CalendarResponse,
    DividendConfirmCreate,
    DividendConfirmResponse,
    ManualDividendCreate,
    ManualDividendResponse,
    MonthDividendItem,
    MonthDividendsResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["calendar"])

# 状态 → 中文标签（错误文案与前端展示共用一套说法）
NOT_FOUND_RECORD = "找不到该笔分红记录"
NOT_FOUND_MANUAL = "补录记录不存在"


# ---------- 内部数据结构 ----------

@dataclass
class _Draft:
    """聚合过程中的一笔分红（akshare 原始记录 / 补录记录 / 去年同期预估）。"""

    code: str
    name: str
    asset_id: int
    portfolio_id: int
    portfolio_name: str
    asset_type: str
    ex_date: date
    dividend_per_share: float
    quantity: float
    status: str
    source: str
    is_confirmed: bool
    is_manual: bool
    is_mock_price: bool
    note: str
    # 预估标记：由「去年同期」投影得到，不是 akshare 实际记录（也不是用户补录）
    is_estimated: bool = False

    @property
    def amount(self) -> float:
        """该笔分红金额 = 持股数 × 每股分红。"""
        return self.quantity * self.dividend_per_share

    def to_item(self) -> MonthDividendItem:
        return MonthDividendItem(
            code=self.code,
            name=self.name,
            asset_id=self.asset_id,
            portfolio_id=self.portfolio_id,
            portfolio_name=self.portfolio_name,
            asset_type=self.asset_type,
            ex_date=self.ex_date,
            dividend_per_share=self.dividend_per_share,
            quantity=self.quantity,
            amount=self.amount,
            status=self.status,  # type: ignore[arg-type]
            source=self.source,
            is_confirmed=self.is_confirmed,
            is_manual=self.is_manual,
            is_mock_price=self.is_mock_price,
            is_estimated=self.is_estimated,
            note=self.note,
        )


def _plus_one_year(day: date) -> date:
    """派息日 +1 年（2/29 → 2/28），用于「去年同期」预估。"""
    try:
        return day.replace(year=day.year + 1)
    except ValueError:
        return day.replace(year=day.year + 1, day=28)


def _apply_estimates(drafts: List[_Draft], *, today: Optional[date] = None) -> List[_Draft]:
    """去年同期预估：把最近 12 个月内的每一笔按 +1 年投影到未来月份。

    规则（刻意做得简单、可解释，前端会明确标「预估」）：
      • 只在「投影月份**严格晚于**当前年月」时生成（不覆盖过去，也不猜更远的未来年份之外的月份）
      • 同一标的若在投影月份已有真实记录（akshare / 补录），则**不生成**预估（真实优先）
      • 预估项 status 仍走 D5 的 predicted（预测），但 is_estimated=True，
        前端据此显示「预估」并且**不提供**「点击确认」（它不是一笔真实记录，无从确认）

    为什么要做：akshare 只收录已发生的分红，未来月份永远为空；用「去年同月实际收到的」
    做保守预估，比显示 ¥0.00 更符合用户对「预计年收入」的预期。
    """
    end = today or date.today()
    window_start = end - timedelta(days=365)
    current_key = (end.year, end.month)

    # 已有真实记录覆盖到的 (标的, 年, 月)
    real_months = {(d.code, d.ex_date.year, d.ex_date.month) for d in drafts}

    estimated: List[_Draft] = []
    seen = set()
    for draft in drafts:
        if not (window_start <= draft.ex_date <= end):
            continue
        projected = _plus_one_year(draft.ex_date)
        key = (projected.year, projected.month)
        if key <= current_key:
            continue                                   # 投影落在过去/当月 → 不预估
        if (draft.code, projected.year, projected.month) in real_months:
            continue                                   # 该标的该月已有真实记录
        dedup = (draft.code, projected)
        if dedup in seen:
            continue
        seen.add(dedup)
        estimated.append(_Draft(
            code=draft.code,
            name=draft.name,
            asset_id=draft.asset_id,
            portfolio_id=draft.portfolio_id,
            portfolio_name=draft.portfolio_name,
            asset_type=draft.asset_type,
            ex_date=projected,
            dividend_per_share=draft.dividend_per_share,
            quantity=draft.quantity,
            status="predicted",                        # 预测态；is_estimated 区分来源
            source=draft.source,
            is_confirmed=False,
            is_manual=draft.is_manual,
            is_mock_price=draft.is_mock_price,
            note="",
            is_estimated=True,
        ))

    return drafts + estimated


# ---------- 工具 ----------

def _get_portfolio_or_404(db: Session, portfolio_id: int) -> Portfolio:
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return portfolio


def _find_asset_by_code(assets: Sequence[Asset], code: str) -> Optional[Asset]:
    """按代码在组合内定位资产（兼容 'sh600900' / '600900' / 大小写差异）。"""
    target = normalize_code(code)
    for asset in assets:
        if normalize_code(asset.code) == target:
            return asset
    return None


def _empty_by_status() -> Dict[str, float]:
    """四个状态键恒定存在（无该状态为 0），前端可直接绘制。"""
    return {s: 0.0 for s in ("predicted", "announced", "paid", "manual")}


def _empty_by_source() -> Dict[str, float]:
    return {s: 0.0 for s in DIVIDEND_SOURCES}


def _aggregate(drafts: Sequence[_Draft], year: int, month: Optional[int] = None) -> Tuple[float, int, Dict[str, float], Dict[str, float], float]:
    """在内存里算 total_amount / count / by_status / by_source / 其中的预估部分。"""
    total_amount = 0.0
    estimated_amount = 0.0
    count = 0
    by_status = _empty_by_status()
    by_source = _empty_by_source()
    for draft in drafts:
        if draft.ex_date.year != year:
            continue
        if month is not None and draft.ex_date.month != month:
            continue
        amount = draft.amount
        total_amount += amount
        if draft.is_estimated:
            estimated_amount += amount
        count += 1
        by_status[draft.status] = by_status.get(draft.status, 0.0) + amount
        by_source[draft.source] = by_source.get(draft.source, 0.0) + amount
    return total_amount, count, by_status, by_source, estimated_amount


def _rolling_12m(drafts: Sequence[_Draft], *, today: Optional[date] = None) -> Tuple[float, int]:
    """近 12 个月已到账合计（口径 = 概览卡「预计年收入」的滚动 12 个月窗口）。

    assets.py 的 `_sum_trailing_12m` 用「今天往前 365 天 + 只算已实施」把每股 TTM 分红
    写回 asset.dividend_per_share；这里用日历自己的逐笔记录重算同一个窗口，
    因此正常情况下 `rolling_12m_total == summary.expected_annual_income`。
    """
    end = today or date.today()
    start = end - timedelta(days=365)
    total = 0.0
    count = 0
    for draft in drafts:
        if draft.status != "paid" or draft.is_estimated:   # 只算已到账的真实记录
            continue
        if start <= draft.ex_date <= end:
            total += draft.amount
            count += 1
    return total, count


# ---------- 聚合：组合 → 逐笔分红 ----------

async def _collect_drafts(db: Session, portfolio: Portfolio) -> List[_Draft]:
    """把组合内的 akshare 历史分红 + 用户补录合并成逐笔草稿（§6.3）。

    任何一只标的拉取失败都只跳过它自己，绝不把整个接口拖成 500。
    """
    assets = db.query(Asset).filter(Asset.portfolio_id == portfolio.id).all()

    confirmations = {
        (c.code, c.ex_date): c
        for c in db.query(DividendConfirmation)
        .filter(DividendConfirmation.portfolio_id == portfolio.id)
        .all()
    }
    manual_records = (
        db.query(ManualDividendRecord)
        .filter(ManualDividendRecord.portfolio_id == portfolio.id)
        .order_by(ManualDividendRecord.ex_date.asc(), ManualDividendRecord.id.asc())
        .all()
    )

    drafts: List[_Draft] = []

    # 1) akshare 原始记录（每只标的只按 code 拉一次，走现有 in-memory 缓存）
    for asset in assets:
        records = await stocks.fetch_all_dividend_records(asset.code)
        if not records:
            logger.warning(
                "标的 %s 无历史分红记录（拉取失败且无缓存，或确实没有分红），日历跳过",
                asset.code,
            )
            continue
        source = classify_source(asset)
        is_mock = (getattr(asset, "price_source", None) or "stale") == "mock"
        for record in records:
            ex_date = _parse_dividend_date(record.get("EX_DIVIDEND_DATE"))
            if ex_date is None:
                logger.warning(
                    "跳过派息日无法解析的分红记录 [%s]: %r",
                    asset.code, record.get("EX_DIVIDEND_DATE"),
                )
                continue
            try:
                per_share = float(record.get("DIVIDEND_PER_SHARE") or 0)
            except (TypeError, ValueError):
                per_share = 0.0

            confirmed = confirmations.get((asset.code, ex_date))
            status = "paid" if confirmed is not None else classify_status(record.get("IMPLEMENT_STATUS"))

            drafts.append(_Draft(
                code=asset.code,
                name=asset.name,
                asset_id=asset.id,
                portfolio_id=portfolio.id,
                portfolio_name=portfolio.name,
                asset_type=getattr(asset, "asset_type", "") or "",
                ex_date=ex_date,
                dividend_per_share=per_share,
                quantity=asset.quantity or 0.0,
                status=status,
                source=source,
                is_confirmed=confirmed is not None,
                is_manual=False,
                is_mock_price=is_mock,
                note="",
            ))

    # 2) 补录记录：同 (code, ex_date) 覆盖 akshare（§6.3.5）
    manual_keys = {(r.code, r.ex_date) for r in manual_records}
    if manual_keys:
        drafts = [d for d in drafts if (d.code, d.ex_date) not in manual_keys]

    assets_by_id = {a.id: a for a in assets}
    for record in manual_records:
        asset = assets_by_id.get(record.asset_id)
        if asset is None:
            # 资产已被删除（补录记录与资产生命周期解耦，历史保留）：
            # 持股数无从得知，按 0 计入，仅用于保留这条历史。
            logger.warning(
                "补录记录 %s(%s) 对应的资产 %s 已不存在，按 0 金额保留",
                record.code, record.ex_date, record.asset_id,
            )
            drafts.append(_Draft(
                code=record.code,
                name=record.code,
                asset_id=record.asset_id,
                portfolio_id=portfolio.id,
                portfolio_name=portfolio.name,
                asset_type="",
                ex_date=record.ex_date,
                dividend_per_share=record.amount_per_share or 0.0,
                quantity=0.0,
                status=record.status if record.status in DIVIDEND_STATUSES else "manual",
                source="manual",
                is_confirmed=False,
                is_manual=True,
                is_mock_price=False,
                note=record.note or "",
            ))
            continue

        status = record.status if record.status in DIVIDEND_STATUSES else "manual"
        # 用户 confirm 过的那一笔同样强制 paid（D6 对任何来源都成立，
        # 否则「补录成 announced → 点确认」会是一个按了没反应的死按钮）
        confirmed = confirmations.get((record.code, record.ex_date))
        if confirmed is not None:
            status = "paid"
        drafts.append(_Draft(
            code=record.code,
            name=asset.name,
            asset_id=asset.id,
            portfolio_id=portfolio.id,
            portfolio_name=portfolio.name,
            asset_type=getattr(asset, "asset_type", "") or "",
            ex_date=record.ex_date,
            dividend_per_share=record.amount_per_share or 0.0,
            quantity=asset.quantity or 0.0,
            status=status,
            source="manual",
            is_confirmed=confirmed is not None,
            is_manual=True,
            is_mock_price=(getattr(asset, "price_source", None) or "stale") == "mock",
            note=record.note or "",
        ))

    drafts.sort(key=lambda d: (d.ex_date, d.code, d.asset_id))
    # 去年同期预估：真实记录之后追加，供前端把「预估」与「已收录」分开标注
    return _apply_estimates(drafts)


# ---------- 端点 ----------

@router.get("/portfolios/{portfolio_id}/calendar", response_model=CalendarResponse)
async def get_calendar(
    portfolio_id: int,
    year: Optional[int] = Query(default=None, ge=1970, le=2100),
    db: Session = Depends(get_db),
):
    """年度日历：恒返回 12 个月（无数据的月份为 0），不做任何未来外推。"""
    portfolio = _get_portfolio_or_404(db, portfolio_id)
    target_year = year if year is not None else date.today().year

    assets = db.query(Asset).filter(Asset.portfolio_id == portfolio_id).all()
    drafts = await _collect_drafts(db, portfolio)

    months: List[CalendarMonth] = []
    for month in range(1, 13):
        total_amount, count, by_status, by_source, estimated = _aggregate(drafts, target_year, month)
        months.append(CalendarMonth(
            year=target_year,
            month=month,
            total_amount=total_amount,
            count=count,
            by_status=by_status,
            by_source=by_source,
            estimated_amount=estimated,
        ))

    total_year, total_count, _, _, estimated_total = _aggregate(drafts, target_year)
    rolling_total, rolling_count = _rolling_12m(drafts)

    return CalendarResponse(
        portfolio_id=portfolio_id,
        year=target_year,
        months=months,
        total_year=total_year,
        total_count=total_count,
        # 组合内资产总数（= 0 时前端显示「该组合暂无资产」空态）
        asset_count=len(assets),
        rolling_12m_total=rolling_total,
        rolling_12m_count=rolling_count,
        estimated_total=estimated_total,
        recorded_total=total_year - estimated_total,
    )


@router.get(
    "/portfolios/{portfolio_id}/calendar/{year}/{month}",
    response_model=MonthDividendsResponse,
)
async def get_month_dividends(
    portfolio_id: int,
    year: int,
    month: int,
    db: Session = Depends(get_db),
):
    """单月明细：列出该月每一笔分红（含 quantity=0 的资产，便于排查）。"""
    if not 1 <= month <= 12:
        raise HTTPException(status_code=422, detail="month 必须在 1~12 之间")
    if not 1970 <= year <= 2100:
        raise HTTPException(status_code=422, detail="year 必须在 1970~2100 之间")

    portfolio = _get_portfolio_or_404(db, portfolio_id)
    drafts = await _collect_drafts(db, portfolio)

    total_amount, count, _, _, _ = _aggregate(drafts, year, month)
    items = [
        d.to_item() for d in drafts
        if d.ex_date.year == year and d.ex_date.month == month
    ]

    return MonthDividendsResponse(
        portfolio_id=portfolio_id,
        year=year,
        month=month,
        total_amount=total_amount,
        count=count,
        items=items,
    )


@router.post(
    "/portfolios/{portfolio_id}/dividends/confirm",
    response_model=DividendConfirmResponse,
)
async def confirm_dividend(
    portfolio_id: int,
    payload: DividendConfirmCreate,
    db: Session = Depends(get_db),
):
    """「点击确认」：把该笔分红标成已到账。**幂等**——重复调用不改 confirmed_at，不重复加金额。"""
    portfolio = _get_portfolio_or_404(db, portfolio_id)

    assets = db.query(Asset).filter(Asset.portfolio_id == portfolio_id).all()
    asset = _find_asset_by_code(assets, payload.code)
    if asset is None:
        # 组合里没有这个标的 → 不可能有匹配的分红记录
        raise HTTPException(status_code=404, detail=NOT_FOUND_RECORD)

    confirmed = (
        db.query(DividendConfirmation)
        .filter(
            DividendConfirmation.portfolio_id == portfolio_id,
            DividendConfirmation.code == asset.code,
            DividendConfirmation.ex_date == payload.ex_date,
        )
        .first()
    )
    if confirmed is not None:
        # 幂等：直接回放已有确认（时间戳不变）
        return DividendConfirmResponse(
            code=confirmed.code,
            ex_date=confirmed.ex_date,
            status=confirmed.confirmed_status or "paid",
            confirmed_at=confirmed.confirmed_at,
        )

    # 必须是组合内真实存在的一笔记录：akshare 收录的，或用户自己补录的
    # （补录的日期天然不在 akshare 里，只查 akshare 会让「点击确认」变成死按钮）
    manual_exists = (
        db.query(ManualDividendRecord)
        .filter(
            ManualDividendRecord.portfolio_id == portfolio_id,
            ManualDividendRecord.code == asset.code,
            ManualDividendRecord.ex_date == payload.ex_date,
        )
        .first()
    )
    found = manual_exists is not None
    if not found:
        records = await stocks.fetch_all_dividend_records(asset.code)
        found = any(
            _parse_dividend_date(r.get("EX_DIVIDEND_DATE")) == payload.ex_date
            for r in records
        )
    if not found:
        raise HTTPException(status_code=404, detail=NOT_FOUND_RECORD)

    confirmed = DividendConfirmation(
        portfolio_id=portfolio_id,
        code=asset.code,
        ex_date=payload.ex_date,
        confirmed_status="paid",
    )
    db.add(confirmed)
    db.commit()
    db.refresh(confirmed)

    return DividendConfirmResponse(
        code=confirmed.code,
        ex_date=confirmed.ex_date,
        status=confirmed.confirmed_status or "paid",
        confirmed_at=confirmed.confirmed_at,
    )


@router.post(
    "/portfolios/{portfolio_id}/dividends/manual",
    response_model=ManualDividendResponse,
    status_code=201,
)
def create_manual_dividend(
    portfolio_id: int,
    payload: ManualDividendCreate,
    db: Session = Depends(get_db),
):
    """「补录」：手动新增一笔分红（必须属于组合内某资产，且不能补未来日期）。"""
    _get_portfolio_or_404(db, portfolio_id)

    assets = db.query(Asset).filter(Asset.portfolio_id == portfolio_id).all()
    asset = _find_asset_by_code(assets, payload.code)
    if asset is None:
        raise HTTPException(status_code=422, detail="组合内不存在该标的")

    if payload.ex_date > date.today():
        raise HTTPException(
            status_code=422, detail="不能补未来日期；预告请等待 akshare 收录"
        )

    status = payload.status or "manual"
    if status not in DIVIDEND_STATUSES:  # pragma: no cover - Pydantic 已挡住
        raise HTTPException(status_code=422, detail="非法的分红状态")

    record = ManualDividendRecord(
        portfolio_id=portfolio_id,
        asset_id=asset.id,
        # 存资产上的标准代码，保证与 akshare 记录的 (code, ex_date) 键一致
        code=asset.code,
        ex_date=payload.ex_date,
        amount_per_share=payload.amount_per_share,
        status=status,
        note=payload.note or "",
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get(
    "/portfolios/{portfolio_id}/dividends/manual",
    response_model=List[ManualDividendResponse],
)
def list_manual_dividends(portfolio_id: int, db: Session = Depends(get_db)):
    _get_portfolio_or_404(db, portfolio_id)
    return (
        db.query(ManualDividendRecord)
        .filter(ManualDividendRecord.portfolio_id == portfolio_id)
        .order_by(ManualDividendRecord.ex_date.asc(), ManualDividendRecord.id.asc())
        .all()
    )


@router.delete("/portfolios/{portfolio_id}/dividends/manual/{record_id}")
def delete_manual_dividend(
    portfolio_id: int, record_id: int, db: Session = Depends(get_db)
):
    """删除补录记录。跨组合的 record_id 同样返回 404（不返回 403，避免泄露存在性）。"""
    _get_portfolio_or_404(db, portfolio_id)
    record = (
        db.query(ManualDividendRecord)
        .filter(
            ManualDividendRecord.id == record_id,
            ManualDividendRecord.portfolio_id == portfolio_id,
        )
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail=NOT_FOUND_MANUAL)
    db.delete(record)
    db.commit()
    return {"message": "Manual dividend deleted"}
