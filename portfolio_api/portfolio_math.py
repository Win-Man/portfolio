"""组合口径的共享纯函数（唯一实现）。

`routers/portfolios.py` 的 summary 与 `routers/goals.py` 的进度计算都必须调用这里，
避免同一个公式在两处各写一遍后悄悄漂移（例如「当前年收入」在两处算得不一样）。

约束：
- 纯计算，不访问数据库、不抛业务异常；
- 只依赖标的的 `quantity` / `price` / `dividend_per_share` / `ttm_yield` / `price_source`；
- **不使用成本口径** `cost_price`（成本与收入/目标无关）。
"""

import math

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


def goal_gap(current_annual: float, target_annual: float) -> dict:
    """目标缺口与完成度（**唯一实现**）。

    `routers/goals.py` 的进度卡片与「每日日报」的目标板块都从这里取数：
    同一条公式写在两处迟早会漂移（历史上就出现过一处拿市值当分母、
    另一处拿目标当分母，两个页面上的完成度对不上）。

    target <= 0 时（schema 已拦住，纯防御）完成度返回 0 而不是除零。
    """
    current = float(current_annual or 0)
    target = float(target_annual or 0)
    gap = max(0.0, target - current)
    surplus = max(0.0, current - target)
    return {
        "gap_annual": gap,
        "gap_monthly": gap / 12,
        "surplus_annual": surplus,
        "surplus_monthly": surplus / 12,
        "progress_percent": (current / target * 100) if target > 0 else 0.0,
        "is_achieved": target > 0 and current >= target,
    }


def reinvest_years(
    current_annual: float, target_annual: float, yield_percent: float
) -> dict:
    """只靠「股息复投」，多少年能达到目标（纯函数，**唯一实现**）。

    模型：每年把当年全部股息按 `yield_percent` 再买入，买到的份额下一年同样派息，
    于是年收入按 (1 + y) 复利增长：

        D(n) = D0 × (1 + y)^n      →      n = ln(目标 / D0) / ln(1 + y)

    为什么不「先算本金、再逐年累加」：TTM 收益率本身口径就是「股息 ÷ 市值」，
    而 `Σ(qty × dps) = Σ(市值 × ttm_yield)`（两处是同一个数），所以
    「用 D0 直接复利」与「本金复利后再乘收益率」在数学上完全等价。
    直接对收入复利少一次除法，也就不会因为市值为 0 而除零。

    边界（都返回 `years=None` + 一句可直接展示的 note）：
    - 当前收入 <= 0：没有股息可投，复投无从开始；
    - 收益率 <= 0：收入恒定，永远到不了；
    - 已达成：`years=0.0`、`years_ceil=0`，note 为空（由界面决定要不要展示此路径）。

    返回值：
        years       精确年数（1 位小数，未达成时至少 0.1）；不可达 → None
        years_ceil  向上取整，即「第几年」达标；不可达 → None
        note        不可达的短原因；正常为空串
    """
    current = float(current_annual or 0)
    target = float(target_annual or 0)
    y = float(yield_percent or 0)

    if target <= 0:
        return {"years": None, "years_ceil": None, "note": "目标金额为 0"}
    if current >= target:
        return {"years": 0.0, "years_ceil": 0, "note": ""}
    if current <= 0:
        return {"years": None, "years_ceil": None, "note": "当前无股息收入"}
    if not math.isfinite(y) or y <= 0:
        return {"years": None, "years_ceil": None, "note": "复投收益率为 0"}

    raw_years = math.log(target / current) / math.log1p(y / 100)

    # 1 位小数用于展示；0.04 → 0.0 会被读成「马上就达成」，抬到 0.1 更诚实
    years = max(round(raw_years, 1), 0.1)
    # 减 1e-9 再取整：浮点误差会让 8.0000000001 变成「第 9 年」
    years_ceil = max(int(math.ceil(raw_years - 1e-9)), 1)

    return {"years": years, "years_ceil": years_ceil, "note": ""}


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
