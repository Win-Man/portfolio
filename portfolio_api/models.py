from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class Portfolio(Base):
    __tablename__ = "portfolios"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    assets = relationship("Asset", back_populates="portfolio", cascade="all, delete-orphan")


class Asset(Base):
    __tablename__ = "assets"

    id = Column(Integer, primary_key=True, index=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False)
    name = Column(String, nullable=False)
    code = Column(String, nullable=False)
    asset_type = Column(String, default="股票")
    quantity = Column(Float, default=0)
    price = Column(Float, default=0)
    # 每股成本（建仓成本，人为填写）。NULL = 尚未填写
    # 注意：成本是历史事实，刷新价格时**不会**被改写
    cost_price = Column(Float, nullable=True)
    dividend_per_share = Column(Float, default=0)
    ttm_yield = Column(Float, default=0)
    static_yield = Column(Float, default=0)
    note = Column(String, default="")
    # 价格来源标识：real=腾讯行情真实价 / mock=网络失败模拟价 / stale=尚未刷新
    price_source = Column(String, default="stale")
    price_fetched_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    portfolio = relationship("Portfolio", back_populates="assets")