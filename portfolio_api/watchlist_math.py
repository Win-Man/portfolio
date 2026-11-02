"""观察池口径的共享纯函数（唯一实现，spec 003 §6.3）。

约束（与 `portfolio_math.py` 对齐）：

- 纯计算，不访问数据库、不抛业务异常；
- 只接受原始数值，**不依赖 ORM 对象** —— 便于单测直接喂 None / 边界值；
- `None` 一律表示「不适用」，按链路传播，**绝不用 0 冒充**（0 是有效数值，含义完全不同）；
- 后端计算不四舍五入（精度留给前端展示层）。

两条线互相独立（D3）：

    股息率线：现价越低 → 股息率越高 → 越接近「达标」
    价格线　：现价越低 → 越接近目标价 → 越接近「达标」

`near` 的两条阈值方向相反（股息率 ×0.97、价格 ×1.03），但语义都是
「相对差 3% 以内」。**不要互相推导、不要合并成一个「可买入」结论**。

`current_yield` 必须由 `price` 与 `ttm_dividend` **现算**，不读快照里的 `ttm_yield`
（那个字段只用于画曲线，历史快照的口径可能与当前代码不一致）。
"""

from datetime import date
from typing import Optional, Tuple

# near 阈值的相对容差（3%）
_YIELD_NEAR_RATIO = 0.97   # 股息率：达到目标的 97% 即「接近」
_PRICE_NEAR_RATIO = 1.03   # 价格：不高于目标的 103% 即「接近」

# 快照超过多少自然日算「陈旧」（覆盖周末 + 小长假）
STALE_DAYS_THRESHOLD = 5


def _num(value) -> Optional[float]:
    """把入参安全转成 float；None / 非数字 → None（不抛异常）。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def current_yield(ttm_dividend, price) -> Optional[float]:
    """当前股息率（%）= TTM 每股分红 ÷ 现价 × 100。

    价格或分红缺失、非正 → None（无分红时不能显示 0%，那会被误读成「股息率为 0」）。
    """
    ttm = _num(ttm_dividend)
    p = _num(price)
    if ttm is None or p is None or ttm <= 0 or p <= 0:
        return None
    return ttm / p * 100


def yield_trigger_price(ttm_dividend, target_yield) -> Optional[float]:
    """触发价 = TTM 每股分红 ÷ (目标股息率 / 100)（D4）。

    即「按你的目标股息率，现价要跌到这个数」。TTM 分红 <= 0 → None
    （不显示 0 元触发价）；target_yield <= 0 同样返回 None（schema 已拦，防御）。
    """
    ttm = _num(ttm_dividend)
    target = _num(target_yield)
    if ttm is None or target is None or ttm <= 0 or target <= 0:
        return None
    return ttm / (target / 100)


def yield_gap_points(current, target_yield) -> Optional[float]:
    """距目标（百分点）= 当前股息率 − 目标股息率。任一 None → None。

    正数表示已超过目标；这里是**百分点差**，不是相对百分比。
    """
    cur = _num(current)
    target = _num(target_yield)
    if cur is None or target is None:
        return None
    return cur - target


def yield_trigger_gap_percent(price, trigger_price) -> Optional[float]:
    """现价距触发价（%）= (现价 − 触发价) ÷ 触发价 × 100。负数 = 现价已低于触发价。"""
    p = _num(price)
    trigger = _num(trigger_price)
    if p is None or trigger is None or trigger <= 0:
        return None
    return (p - trigger) / trigger * 100


def price_gap_percent(price, target_price) -> Optional[float]:
    """现价距目标价（%）= (现价 − 目标价) ÷ 目标价 × 100。未设目标价 → None。"""
    p = _num(price)
    target = _num(target_price)
    if p is None or target is None or target <= 0:
        return None
    return (p - target) / target * 100


def yield_signal(current, target_yield) -> str:
    """股息率线信号：unknown / hit / near / watch（互不合并，D3）。"""
    cur = _num(current)
    target = _num(target_yield)
    if cur is None or target is None:
        return "unknown"
    if cur >= target:
        return "hit"
    if cur >= target * _YIELD_NEAR_RATIO:
        return "near"
    return "watch"


def price_signal(price, target_price) -> str:
    """价格线信号：not_set / unknown / hit / near / watch。

    未设目标价优先判 not_set（这是「没定锚」而不是「数据不足」）；
    再看现价是否缺失。
    """
    target = _num(target_price)
    if target is None or target <= 0:
        return "not_set"
    p = _num(price)
    if p is None:
        return "unknown"
    if p <= target:
        return "hit"
    if p <= target * _PRICE_NEAR_RATIO:
        return "near"
    return "watch"


def staleness(snapshot_date: Optional[date], today: date) -> Tuple[bool, Optional[int]]:
    """数据陈旧判定 → (is_stale, stale_days)。

    无快照时 (False, None)：**没数据 ≠ 数据陈旧**，前端用「待抓取」而不是黄条。
    相差 > 5 个自然日才算陈旧（周末 + 小长假不会误报）。
    """
    if snapshot_date is None or today is None:
        return False, None
    days = (today - snapshot_date).days
    if days < 0:
        # 快照日期在未来（时区/脏数据）：不判陈旧，也不给出负数天数
        return False, 0
    return days > STALE_DAYS_THRESHOLD, days


def derive_metrics(
    *,
    price,
    ttm_dividend,
    target_yield,
    target_price,
) -> dict:
    """一次性算出响应里所有派生字段与两条信号（口径唯一，路由只做组装）。"""
    cur = current_yield(ttm_dividend, price)
    trigger = yield_trigger_price(ttm_dividend, target_yield)
    return {
        "current_yield": cur,
        "yield_trigger_price": trigger,
        "yield_gap_points": yield_gap_points(cur, target_yield),
        "yield_trigger_gap_percent": yield_trigger_gap_percent(price, trigger),
        "price_gap_percent": price_gap_percent(price, target_price),
        "yield_signal": yield_signal(cur, target_yield),
        "price_signal": price_signal(price, target_price),
    }
