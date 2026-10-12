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
    total_market_value = sum(a.quantity * a.price for a in assets)
    total_assets_count = len(assets)

    if total_market_value > 0 and total_assets_count > 0:
        weighted_yield = sum((a.quantity * a.price) * a.ttm_yield for a in assets) / total_market_value
    else:
        weighted_yield = 0.0

    expected_annual_income = sum(a.quantity * a.dividend_per_share for a in assets)

    return PortfolioSummary(
        total_market_value=total_market_value,
        asset_count=total_assets_count,
        weighted_ttm_yield=weighted_yield,
        expected_annual_income=expected_annual_income,
        description="基于当前持股计算的汇总数据",
    )
