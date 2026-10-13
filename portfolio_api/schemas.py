from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from typing import Optional, Literal


# Portfolio schemas
class PortfolioBase(BaseModel):
    name: str


class PortfolioCreate(PortfolioBase):
    pass


class PortfolioUpdate(BaseModel):
    name: Optional[str] = None


class PortfolioResponse(PortfolioBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AssetBase(BaseModel):
    name: str
    code: str
    asset_type: str = "股票"
    quantity: float = 0
    price: float = 0
    # 每股成本（建仓成本）。None = 未填；新增时后端会用 price 兜底
    cost_price: Optional[float] = None
    # 这两个字段前端表单允许空着：null 让 Pydantic 用 0 兜底（接口契约更宽松）
    dividend_per_share: Optional[float] = 0
    ttm_yield: Optional[float] = 0
    static_yield: Optional[float] = 0
    note: str = ""


class AssetCreate(AssetBase):
    pass


class AssetUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    asset_type: Optional[str] = None
    quantity: Optional[float] = None
    price: Optional[float] = None
    cost_price: Optional[float] = None
    dividend_per_share: Optional[float] = None
    ttm_yield: Optional[float] = None
    static_yield: Optional[float] = None
    note: Optional[str] = None


PriceSource = Literal["real", "mock", "stale"]


class AssetResponse(AssetBase):
    id: int
    portfolio_id: int
    created_at: datetime
    holding_value: float = 0
    expected_annual_income: float = 0
    # 成本与盈亏（后端算，不落库）
    cost_value: float = 0          # 持仓成本 = quantity × cost_price（可为负）
    profit_amount: float = 0       # 浮动盈亏 = (price − cost_price) × quantity
    # 盈亏比例 = (price − cost_price) / cost_price × 100
    # ⚠ cost_price ≤ 0 时为 None：负成本算出来是负数（明明赚钱却显示负百分比），
    #   0 成本会除零。此时只展示金额，前端显示「—」
    profit_percent: Optional[float] = None
    has_cost: bool = False         # 是否已填成本（NULL = 未填）
    # 最近三年分红（从真实数据获取）
    y2026_dividend: float = 0  # 2026年每股分红
    y2025_dividend: float = 0  # 2025年每股分红
    y2024_dividend: float = 0  # 2024年每股分红
    # 价格真实性标记 + 抓取时间
    price_source: PriceSource = "stale"
    price_fetched_at: Optional[datetime] = None
    # 给前端的便捷提示文案（前端无需自己拼）
    price_source_label: str = "未刷新"
    price_source_hint: str = "该价格尚未从行情接口刷新过"

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_orm_with_calc(cls, asset):
        holding_value = asset.quantity * asset.price
        expected_annual_income = asset.quantity * asset.dividend_per_share
        source = getattr(asset, "price_source", "stale") or "stale"
        fetched_at = getattr(asset, "price_fetched_at", None)
        label, hint = _source_to_label_hint(source, fetched_at)

        cost_price = getattr(asset, "cost_price", None)
        # 只把 NULL 当「未填」——负成本是合法值（分红/高抛低吸后摊薄成本转负），
        # 0 也是用户显式填的值
        has_cost = cost_price is not None
        cost_value = (asset.quantity * cost_price) if has_cost else 0.0
        profit_amount = ((asset.price - cost_price) * asset.quantity) if has_cost else 0.0
        # cost_price ≤ 0 时百分比无意义（负数会给出误导性的负百分比，0 会除零）
        profit_percent = (
            (asset.price - cost_price) / cost_price * 100
            if has_cost and cost_price > 0 else None
        )

        return cls(
            id=asset.id,
            portfolio_id=asset.portfolio_id,
            name=asset.name,
            code=asset.code,
            asset_type=asset.asset_type,
            quantity=asset.quantity,
            price=asset.price,
            cost_price=cost_price,
            dividend_per_share=asset.dividend_per_share,
            ttm_yield=asset.ttm_yield,
            static_yield=asset.static_yield,
            note=getattr(asset, "note", "") or "",
            created_at=asset.created_at,
            holding_value=holding_value,
            expected_annual_income=expected_annual_income,
            cost_value=cost_value,
            profit_amount=profit_amount,
            profit_percent=profit_percent,
            has_cost=has_cost,
            price_source=source,
            price_fetched_at=fetched_at,
            price_source_label=label,
            price_source_hint=hint,
        )


def _source_to_label_hint(source: str, fetched_at: Optional[datetime]) -> tuple[str, str]:
    """根据价格来源返回给前端的简短标签和悬浮提示。"""
    if source == "real":
        ts = fetched_at.strftime("%Y-%m-%d %H:%M") if fetched_at else "时间未知"
        return "实时", f"腾讯行情接口返回的真实价（{ts}）"
    if source == "mock":
        return "模拟", "网络拉取失败，使用 ±3% 随机波动价，不可用于决策"
    return "未刷新", "该价格尚未从行情接口刷新过"


# Summary schemas
class PortfolioSummary(BaseModel):
    total_market_value: float
    asset_count: int
    weighted_ttm_yield: float
    expected_annual_income: float
    # 成本与盈亏汇总（只统计已填成本的标的）
    total_cost: float = 0
    total_profit: float = 0
    # 总成本 ≤ 0（例如整体成本被分红摊成负）时为 None，此时百分比无意义
    total_profit_percent: Optional[float] = None
    costed_asset_count: int = 0
    description: str = "基于当前持股计算的汇总数据"