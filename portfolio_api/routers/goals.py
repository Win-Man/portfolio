"""年收入目标（income goal）：CRUD + 进度计算。

口径要点（详见 docs/specs/001-income-goal.md）：
- 目标绑定组合；切换组合互不可见；删除组合级联删除目标（SQLAlchemy cascade）。
- 「当前收入」= Σ(quantity × dividend_per_share)，与 summary.expected_annual_income 完全一致。
- 收益率路径只用「收入 ÷ 持仓市值」；加权 TTM 收益率仅作为「默认假设加仓收益率」的来源，
  不参与进度 / 缺口计算。
- 所有计算在后端（口径唯一、可测），前端只做展示与格式化。
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models import Asset, Goal, Portfolio
from portfolio_math import (
    expected_annual_income,
    has_mock_price,
    total_market_value,
    weighted_ttm_yield,
)
from schemas import GoalCreate, GoalProgressResponse, GoalResponse, GoalUpdate

router = APIRouter(prefix="/api", tags=["goals"])


# ---------- 内部工具 ----------

def _get_portfolio_or_404(db: Session, portfolio_id: int) -> Portfolio:
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return portfolio


def _normalize_name(raw: str) -> str:
    """去首尾空白；strip 后为空 → 422（不依赖 Pydantic 的空白校验）。"""
    name = (raw or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="目标名称不能为空")
    return name


def _ensure_name_unique(
    db: Session, portfolio_id: int, name: str, exclude_id: Optional[int] = None
) -> None:
    """同一组合内目标名不允许重复（去首尾空白后比较）。不同组合之间允许同名。"""
    query = db.query(Goal).filter(Goal.portfolio_id == portfolio_id)
    if exclude_id is not None:
        query = query.filter(Goal.id != exclude_id)
    for goal in query.all():
        if (goal.name or "").strip() == name:
            raise HTTPException(status_code=409, detail="同名目标已存在")


def _get_goal_or_404(db: Session, goal_id: int) -> Goal:
    goal = db.query(Goal).filter(Goal.id == goal_id).first()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    return goal


# ---------- CRUD ----------

@router.get("/portfolios/{portfolio_id}/goals", response_model=List[GoalResponse])
def get_goals(portfolio_id: int, db: Session = Depends(get_db)):
    _get_portfolio_or_404(db, portfolio_id)
    return (
        db.query(Goal)
        .filter(Goal.portfolio_id == portfolio_id)
        .order_by(Goal.created_at.asc(), Goal.id.asc())
        .all()
    )


@router.post(
    "/portfolios/{portfolio_id}/goals", response_model=GoalResponse, status_code=201
)
def create_goal(
    portfolio_id: int, goal: GoalCreate, db: Session = Depends(get_db)
):
    _get_portfolio_or_404(db, portfolio_id)
    name = _normalize_name(goal.name)
    _ensure_name_unique(db, portfolio_id, name)

    db_goal = Goal(
        portfolio_id=portfolio_id,
        name=name,
        annual_income_target=goal.annual_income_target,
    )
    db.add(db_goal)
    db.commit()
    db.refresh(db_goal)
    return db_goal


@router.put("/goals/{goal_id}", response_model=GoalResponse)
def update_goal(goal_id: int, goal: GoalUpdate, db: Session = Depends(get_db)):
    db_goal = _get_goal_or_404(db, goal_id)

    # 部分更新语义：字段为 None 则不改
    if goal.name is not None:
        name = _normalize_name(goal.name)
        _ensure_name_unique(
            db, db_goal.portfolio_id, name, exclude_id=db_goal.id
        )
        db_goal.name = name
    if goal.annual_income_target is not None:
        db_goal.annual_income_target = goal.annual_income_target

    db.commit()
    db.refresh(db_goal)
    return db_goal


@router.delete("/goals/{goal_id}")
def delete_goal(goal_id: int, db: Session = Depends(get_db)):
    db_goal = _get_goal_or_404(db, goal_id)
    db.delete(db_goal)
    db.commit()
    return {"message": "Goal deleted"}


# ---------- 进度 ----------

@router.get(
    "/portfolios/{portfolio_id}/goals/{goal_id}/progress",
    response_model=GoalProgressResponse,
)
def get_goal_progress(
    portfolio_id: int,
    goal_id: int,
    assumed_yield: Optional[float] = Query(default=None, ge=0, le=100),
    db: Session = Depends(get_db),
):
    _get_portfolio_or_404(db, portfolio_id)

    # gid 不属于 pid 时同样返回 404（不返回 400，避免泄露跨组合存在性）
    goal = (
        db.query(Goal)
        .filter(Goal.id == goal_id, Goal.portfolio_id == portfolio_id)
        .first()
    )
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    assets = db.query(Asset).filter(Asset.portfolio_id == portfolio_id).all()

    # ── 当前（D2 口径）：必须与 summary.expected_annual_income 完全一致
    current_annual = expected_annual_income(assets)
    current_monthly = current_annual / 12

    target_annual = goal.annual_income_target
    target_monthly = target_annual / 12

    gap_annual = max(0.0, target_annual - current_annual)
    gap_monthly = gap_annual / 12
    # target > 0 由 schema 校验保证，无除零
    progress_percent = current_annual / target_annual * 100
    is_achieved = current_annual >= target_annual
    surplus_annual = max(0.0, current_annual - target_annual)
    surplus_monthly = surplus_annual / 12

    market_value = total_market_value(assets)
    portfolio_ttm = weighted_ttm_yield(assets)

    # 假设加仓收益率：用户手填优先，否则取组合加权 TTM（不持久化）
    assumed_yield_is_default = assumed_yield is None
    assumed_yield_percent = portfolio_ttm if assumed_yield_is_default else assumed_yield

    # 路径 ①：收益率不变 → 还需再投入本金 = 缺口 ÷ 假设收益率
    additional_capital = (
        gap_annual / (assumed_yield_percent / 100)
        if assumed_yield_percent > 0
        else None
    )

    # 路径 ②：本金不变 → 收益率需达到多少（只用「收入 ÷ 市值」口径，与加权 TTM 无关）
    if market_value > 0:
        required_yield_percent = target_annual / market_value * 100
        current_yield_percent = current_annual / market_value * 100
        yield_gap_points = required_yield_percent - current_yield_percent
    else:
        # 无持仓（市值 0）时收益率类字段全部 null（沿用 total_profit_percent 的约定）
        required_yield_percent = None
        current_yield_percent = None
        yield_gap_points = None

    # calc_note：按顺序取第一条命中
    if is_achieved:
        calc_note = "目标已达成"
    elif market_value <= 0:
        calc_note = (
            "当前持仓市值为 0，无法推算所需收益率；"
            "填写「假设加仓收益率」可估算所需本金"
        )
    elif assumed_yield_percent <= 0:
        calc_note = "假设加仓收益率为 0，无法推算所需本金；填写一个大于 0 的收益率"
    else:
        calc_note = ""

    return GoalProgressResponse(
        goal_id=goal.id,
        goal_name=goal.name,
        portfolio_id=portfolio_id,
        current_annual_income=current_annual,
        current_monthly_income=current_monthly,
        target_annual_income=target_annual,
        target_monthly_income=target_monthly,
        gap_annual=gap_annual,
        gap_monthly=gap_monthly,
        progress_percent=progress_percent,
        is_achieved=is_achieved,
        surplus_annual=surplus_annual,
        surplus_monthly=surplus_monthly,
        additional_capital=additional_capital,
        assumed_yield_percent=assumed_yield_percent,
        assumed_yield_is_default=assumed_yield_is_default,
        required_yield_percent=required_yield_percent,
        current_yield_percent=current_yield_percent,
        yield_gap_points=yield_gap_points,
        total_market_value=market_value,
        weighted_ttm_yield=portfolio_ttm,
        asset_count=len(assets),
        has_mock_price=has_mock_price(assets),
        calc_note=calc_note,
    )
