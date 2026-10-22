"""组合口径的共享纯函数（唯一实现）。

`routers/portfolios.py` 的 summary 与 `routers/goals.py` 的进度计算都必须调用这里，
避免同一个公式在两处各写一遍后悄悄漂移（例如「当前年收入」在两处算得不一样）。

约束：
- 纯计算，不访问数据库、不抛业务异常；
- 只依赖标的的 `quantity` / `price` / `dividend_per_share` / `ttm_yield` / `price_source`；
- **不使用成本口径** `cost_price`（成本与收入/目标无关）。
"""

from market import is_etf


def total_market_value(assets) -> float:
    """总持仓市值 = Σ(quantity × price)。"""
    return sum(a.quantity * a.price for a in assets)


def expected_annual_income(assets) -> float:
    """预计年收入 = Σ(quantity × dividend_per_share)。"""
    return sum(a.quantity * a.dividend_per_share for a in assets)


def weighted_ttm_yield(assets) -> float:
    """按持仓市值加权的 TTM 收益率（百分数）。

    与 summary 的既有行为逐字一致：市值为 0（或无标的）时返回 0.0，
    而不是 None —— 这个 0.0 会被 goal 进度当作「默认假设加仓收益率」，
    届时无法推算所需本金，由 calc_note 提示用户手填。
    """
    assets = list(assets)
    market_value = total_market_value(assets)
    if market_value > 0 and len(assets) > 0:
        return sum((a.quantity * a.price) * a.ttm_yield for a in assets) / market_value
    return 0.0


def has_mock_price(assets) -> bool:
    """是否含「模拟价格」的标的（网络拉取失败时的兜底价）。"""
    return any(
        (getattr(a, "price_source", None) or "stale") == "mock" for a in assets
    )


# ---------- 收入日历：状态与来源归类（纯函数，前后端口径一致） ----------

# 每笔分红的状态白名单（写库前必须校验）
DIVIDEND_STATUSES = ("predicted", "announced", "paid", "manual")

# 来源大类（顺序即图例 / 堆叠顺序）
DIVIDEND_SOURCES = ("stock", "etf", "bond", "otc", "reit", "custom", "self", "manual")


def classify_status(impl_status) -> str:
    """akshare 的 IMPLEMENT_STATUS（中文）→ 内部状态。

    • 含「已实施」→ paid
    • 含「已发布」/「实施中」→ announced
    • 其余（「董事会预案」/「股东大会通过」/缺失/未知）→ predicted

    ⚠ 正向判定只认「已实施」这个精确串，绝不用宽松的「含实施」：
    「未实施」「尚未实施」都含「实施」，但语义是**还没实施**，必须保守按 predicted。
    """
    text = "" if impl_status is None else str(impl_status)
    if "已实施" in text:
        return "paid"
    if "已发布" in text or "实施中" in text:
        return "announced"
    return "predicted"


def classify_source(asset, is_manual: bool = False) -> str:
    """把一笔分红归到来源大类（与前端 utils/calendar.js 的 classifySource 同口径）。

    manual → 补录；ETF 代码 → etf；再看 asset_type 关键词；兜底 stock。
    只依赖 code / asset_type，**不看任何成本字段**。
    """
    if is_manual:
        return "manual"
    code = getattr(asset, "code", None) or ""
    try:
        if is_etf(code):
            return "etf"
    except Exception:  # pragma: no cover - is_etf 对任意输入都安全，这里只兜底
        pass

    asset_type = str(getattr(asset, "asset_type", None) or "")
    if "债" in asset_type:
        return "bond"
    if "OTC" in asset_type.upper():
        return "otc"
    if "reit" in asset_type.lower():
        return "reit"
    if "自建" in asset_type:
        return "self"
    if "自定义" in asset_type:
        return "custom"
    return "stock"
