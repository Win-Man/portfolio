"""组合口径的共享纯函数（唯一实现）。

`routers/portfolios.py` 的 summary 与 `routers/goals.py` 的进度计算都必须调用这里，
避免同一个公式在两处各写一遍后悄悄漂移（例如「当前年收入」在两处算得不一样）。

约束：
- 纯计算，不访问数据库、不抛业务异常；
- 只依赖标的的 `quantity` / `price` / `dividend_per_share` / `ttm_yield` / `price_source`；
- **不使用成本口径** `cost_price`（成本与收入/目标无关）。
"""


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
