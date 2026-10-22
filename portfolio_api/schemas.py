from pydantic import BaseModel, ConfigDict, Field
from datetime import date, datetime
from typing import List, Literal, Optional


# Portfolio schemas
class PortfolioBase(BaseModel):
    name: str


class PortfolioCreate(PortfolioBase):
    # 只在**写入**时收紧：PortfolioResponse 继承 PortfolioBase，
    # 若把约束加在基类上，历史库里超长/异常的名称会让 GET 列表直接响应校验失败
    name: str = Field(min_length=1, max_length=50)


class PortfolioUpdate(BaseModel):
    # 部分更新语义：None 表示不改；给了值就必须是 1–50 字（纯空白由路由 strip 后再判）
    name: Optional[str] = Field(default=None, min_length=1, max_length=50)


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


# Income goal schemas（年收入目标）
class GoalBase(BaseModel):
    # 名称先去首尾空白（路由里 strip 后判空）
    name: str = Field(min_length=1, max_length=50)
    # 目标年收入（元）
    annual_income_target: float = Field(gt=0, le=1_000_000_000)


class GoalCreate(GoalBase):
    pass


class GoalUpdate(BaseModel):
    """部分更新语义：字段为 None 则不改（与 PortfolioUpdate 一致）。"""

    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    annual_income_target: Optional[float] = Field(default=None, gt=0, le=1_000_000_000)


class GoalResponse(GoalBase):
    id: int
    portfolio_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GoalProgressResponse(BaseModel):
    goal_id: int
    goal_name: str
    portfolio_id: int

    # ── 当前（口径 = summary.expected_annual_income = Σ(quantity × dividend_per_share)）
    current_annual_income: float
    current_monthly_income: float

    # ── 目标
    target_annual_income: float
    target_monthly_income: float

    # ── 缺口 / 进度
    gap_annual: float            # max(0, 目标 − 当前)
    gap_monthly: float
    progress_percent: float      # 当前 / 目标 × 100，>100 时保留原值不做截断
    is_achieved: bool
    surplus_annual: float        # max(0, 当前 − 目标)
    surplus_monthly: float

    # ── 路径 ①：收益率不变，还需多少本金
    additional_capital: Optional[float]       # 缺口 ÷ (假设收益率/100)；假设收益率<=0 → null
    assumed_yield_percent: float              # 本次实际采用的假设收益率（百分数）
    assumed_yield_is_default: bool            # true = 未传 assumed_yield，用的组合加权 TTM

    # ── 路径 ②：本金不变，收益率需达到多少
    # 口径只用「收入 ÷ 持仓市值」；与加权 TTM 收益率数值不一致是正常的（D3）
    required_yield_percent: Optional[float]    # 目标年收入 ÷ 市值 × 100；市值<=0 → null
    current_yield_percent: Optional[float]     # 当前年收入 ÷ 市值 × 100；市值<=0 → null
    yield_gap_points: Optional[float]          # required − current；任一为 null → null

    # ── 参考与提示
    total_market_value: float
    weighted_ttm_yield: float                  # 仅供对照 / 说明默认值来源
    asset_count: int
    has_mock_price: bool
    calc_note: str                             # 无异常时为空串


# ---------- 收入日历（income calendar） ----------

DividendStatus = Literal["predicted", "announced", "paid", "manual"]


class CalendarMonth(BaseModel):
    year: int
    month: int                                 # 1..12
    total_amount: float                        # 该月合计 = Σ(quantity × dividend_per_share)
    count: int                                 # 该月笔数
    # 四个状态键恒定存在（无该状态则为 0），前端可直接画图 / 上色
    by_status: dict[str, float]
    # 八个来源大类键恒定存在："stock"/"etf"/"bond"/"otc"/"reit"/"custom"/"self"/"manual"
    by_source: dict[str, float]
    # 该月合计里「去年同期预估」占的部分（total_amount 已包含它）
    estimated_amount: float = 0


class CalendarResponse(BaseModel):
    portfolio_id: int
    year: int
    months: List[CalendarMonth]                # 恒为 12 个月（1..12）
    total_year: float
    total_count: int
    # 组合内资产数（= 0 时前端显示「该组合暂无资产」空态）
    asset_count: int
    # 近 12 个月（今天往前 365 天，含今天）**已到账**分红合计。
    # 口径与概览卡「预计年收入」（= Σ 持股数 × 每股 TTM 分红）一致：
    # 自然年合计与之不同是正常的（窗口不同 + 未来月份 akshare 尚未收录），
    # 两个数字同屏展示，用户才能自己核对，而不是以为哪里算错了。
    rolling_12m_total: float = 0
    rolling_12m_count: int = 0
    # 「去年同期预估」合计（akshare 尚未收录的未来月份，用去年同月实际分红投影得到）。
    # 它**包含在** total_year 里；recorded_total = total_year − estimated_total 是纯已收录部分。
    # 预估只影响展示，不影响 rolling_12m_total（那个只看已到账）。
    estimated_total: float = 0
    recorded_total: float = 0


class MonthDividendItem(BaseModel):
    code: str
    name: str
    asset_id: int
    portfolio_id: int
    portfolio_name: str
    asset_type: str
    ex_date: date
    dividend_per_share: float                  # 原始每股分红（不是 TTM 合计）
    quantity: float
    amount: float                              # = quantity × dividend_per_share
    status: DividendStatus
    source: str                                # 与 by_source 同分类
    is_confirmed: bool                         # 是否已被用户 confirm 过
    is_manual: bool                            # 是否手动补录
    is_mock_price: bool = False                # 该标的的 price_source == "mock"（月 Sheet 提示）
    is_estimated: bool = False                 # 「去年同期预估」项（不是 akshare / 补录的真实记录）
    note: str = ""


class MonthDividendsResponse(BaseModel):
    portfolio_id: int
    year: int
    month: int
    total_amount: float
    count: int
    items: List[MonthDividendItem]


class DividendConfirmCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    ex_date: date


class DividendConfirmResponse(BaseModel):
    code: str
    ex_date: date
    status: DividendStatus
    confirmed_at: datetime


class ManualDividendCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    ex_date: date
    amount_per_share: float = Field(gt=0)
    note: Optional[str] = Field(default=None, max_length=200)
    # 白名单外 → 422；缺省即「补录」语义
    status: Optional[DividendStatus] = None


class ManualDividendResponse(BaseModel):
    id: int
    portfolio_id: int
    asset_id: int
    code: str
    ex_date: date
    amount_per_share: float
    status: DividendStatus
    note: str = ""
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
