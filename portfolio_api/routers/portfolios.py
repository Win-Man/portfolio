from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from models import Portfolio, Asset
from schemas import (
    PortfolioCreate,
    PortfolioUpdate,
    PortfolioResponse,
    PortfolioSummary,
)
from portfolio_math import total_market_value, expected_annual_income, weighted_ttm_yield

router = APIRouter(prefix="/api/portfolios", tags=["portfolios"])


@router.get("", response_model=List[PortfolioResponse])
def get_portfolios(db: Session = Depends(get_db)):
    return db.query(Portfolio).all()


@router.post("", response_model=PortfolioResponse)
def create_portfolio(portfolio: PortfolioCreate, db: Session = Depends(get_db)):
    db_portfolio = Portfolio(name=portfolio.name)
    db.add(db_portfolio)
    db.commit()
    db.refresh(db_portfolio)
    return db_portfolio


@router.get("/{portfolio_id}", response_model=PortfolioResponse)
def get_portfolio(portfolio_id: int, db: Session = Depends(get_db)):
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return portfolio


@router.put("/{portfolio_id}", response_model=PortfolioResponse)
def update_portfolio(
    portfolio_id: int, portfolio: PortfolioUpdate, db: Session = Depends(get_db)
):
    db_portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if not db_portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    if portfolio.name is not None:
        db_portfolio.name = portfolio.name
    db.commit()
    db.refresh(db_portfolio)
    return db_portfolio


@router.delete("/{portfolio_id}")
def delete_portfolio(portfolio_id: int, db: Session = Depends(get_db)):
    db_portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if not db_portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    db.delete(db_portfolio)
    db.commit()
    return {"message": "Portfolio deleted"}


@router.get("/{portfolio_id}/summary", response_model=PortfolioSummary)
def get_portfolio_summary(portfolio_id: int, db: Session = Depends(get_db)):
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    assets = db.query(Asset).filter(Asset.portfolio_id == portfolio_id).all()
    # 口径统一放在 portfolio_math：目标进度计算复用同一组函数，避免两处公式漂移
    total_market_value_sum = total_market_value(assets)
    total_assets_count = len(assets)
    weighted_yield = weighted_ttm_yield(assets)
    expected_annual_income_sum = expected_annual_income(assets)

    # 成本与盈亏：只统计**已填成本**的标的（cost_price 为 NULL 的跳过），
    # 否则未填成本的标的会被当成零成本，把总盈亏算得虚高。
    # 注意用 `is not None`：负成本（分红/高抛低吸后摊薄成本转负）是合法值，不能被排除。
    costed = [a for a in assets if a.cost_price is not None]
    total_cost = sum(a.quantity * a.cost_price for a in costed)
    costed_market_value = sum(a.quantity * a.price for a in costed)
    total_profit = costed_market_value - total_cost
    # 总成本 ≤ 0 时百分比无意义（负数会给出误导性的负百分比，0 会除零）
    total_profit_percent = (
        (total_profit / total_cost * 100) if total_cost > 0 else None
    )

    return PortfolioSummary(
        total_market_value=total_market_value_sum,
        asset_count=total_assets_count,
        weighted_ttm_yield=weighted_yield,
        expected_annual_income=expected_annual_income_sum,
        total_cost=total_cost,
        total_profit=total_profit,
        total_profit_percent=total_profit_percent,
        costed_asset_count=len(costed),
        description="基于当前持股计算的汇总数据",
    )
