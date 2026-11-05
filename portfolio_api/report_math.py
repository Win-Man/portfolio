"""每日日报的纯计算（唯一实现，供 `reports.py` 组装 payload）。

约束与 `portfolio_math.py` / `watchlist_math.py` 一致：

- 纯计算，不访问数据库、不读外部接口、不抛业务异常；
- `None` = 「数据不足 / 不适用」，按链路传播，**绝不用 0 冒充**
  （0 是有效数值：今天真的一分没涨，和「不知道昨收是多少」是两件事）；
- 不做展示层四舍五入（那是前端的事），只在求和处 `round(..., 6)` 抹掉浮点尾巴
  （0.1+0.2=0.30000000000000004 这种尾巴会让 JSON 里出现一串噪声小数）。

口径（写死在这里，路由与前端都不再自己算）：

    今日盈亏   = Σ (现价 − 昨收) × 持股数
    今日涨跌幅 = 今日盈亏 ÷ 昨日市值 × 100     （昨日市值 = Σ 昨收 × 持股数）
    持仓盈亏   = Σ (现价 − 成本价) × 持股数     （只统计已填成本的标的）

「今日盈亏」与「持仓盈亏」是两个完全不同的东西：前者是相对**昨天收盘**的
账面变化（跟成本无关），后者是相对**建仓成本**的累计浮盈。日报里两者都要给，
否则用户会拿「今天赚了 200」去解释「持仓亏了 3000」。
"""

from typing import Dict, List, Optional

# 榜单 / 结构分析的取数个数（涨跌榜、收入贡献榜）
TOP_N = 3

# 浮点尾巴的处理位数：6 位足够覆盖「分」以下的精度，又不会留下 0.30000000000000004
_ROUND = 6


def _num(value) -> Optional[float]:
    """安全转 float；None / 非数字 / NaN → None。"""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:            # NaN
        return None
    return result


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, _ROUND)


# ---------- 单只标的 ----------

def change_amount(price, prev_close) -> Optional[float]:
    """每股涨跌额 = 现价 − 昨收。任一缺失或昨收 <= 0 → None。"""
    p = _num(price)
    prev = _num(prev_close)
    if p is None or prev is None or prev <= 0:
        return None
    return p - prev


def change_percent(price, prev_close) -> Optional[float]:
    """涨跌幅（%）= (现价 − 昨收) ÷ 昨收 × 100。昨收缺失/为 0 → None。"""
    p = _num(price)
    prev = _num(prev_close)
    if p is None or prev is None or prev <= 0:
        return None
    return (p - prev) / prev * 100


def day_pnl(quantity, price, prev_close) -> Optional[float]:
    """今日盈亏（元）= (现价 − 昨收) × 持股数。

    昨收缺失 → None（**不能**用 0 代替：那等于宣称「今天没涨没跌」）。
    """
    qty = _num(quantity)
    per_share = change_amount(price, prev_close)
    if qty is None or per_share is None:
        return None
    return per_share * qty


def notional_pnl(change_percent_value, notional) -> Optional[float]:
    """按「每只固定金额」测算的今日盈亏（观察池用，那里没有真实持股数）。

    等价于「昨收买入 ¥notional 的份额，今天值多少」，因此直接
    notional × 涨跌幅/100 即可。
    """
    pct = _num(change_percent_value)
    base = _num(notional)
    if pct is None or base is None:
        return None
    return base * pct / 100


# ---------- 组合汇总 ----------

def summarize_holdings(rows: List[dict]) -> dict:
    """组合口径汇总。rows 每项至少含 quantity / price / prev_close，
    可选 cost_price（缺失 = 未填成本，不参与成本类统计）。

    返回字段全部可能是 None —— 例如整个组合都没有昨收时，
    `day_pnl` 与 `day_pnl_percent` 都是 None，展示层据此显示「—」而不是 ¥0.00。
    """
    market_value = 0.0          # 今日市值
    prev_market_value = 0.0     # 昨日市值（只累加有昨收的标的）
    day_total = 0.0
    cost_amount = 0.0
    holding_pnl = 0.0
    day_covered = 0             # 能算今日盈亏的标的数
    cost_covered = 0            # 已填成本的标的数
    annual_income = 0.0

    for row in rows:
        qty = _num(row.get("quantity")) or 0.0
        price = _num(row.get("price")) or 0.0
        prev_close = _num(row.get("prev_close"))
        cost_price = _num(row.get("cost_price"))
        dividend = _num(row.get("dividend_per_share")) or 0.0

        market_value += qty * price
        annual_income += qty * dividend

        if prev_close is not None and prev_close > 0:
            prev_market_value += qty * prev_close
            pnl = day_pnl(qty, price, prev_close)
            if pnl is not None:
                day_total += pnl
                day_covered += 1

        if cost_price is not None:
            cost_amount += qty * cost_price
            holding_pnl += (price - cost_price) * qty
            cost_covered += 1

    # 分母用「有昨收那部分标的的昨日市值」，与分子的覆盖面保持一致，
    # 否则缺一只标的的昨收就会把涨跌幅算歪
    day_percent = (day_total / prev_market_value * 100) if prev_market_value > 0 else None
    # 成本 ≤ 0（分红摊薄成负成本）时百分比无意义，与 summary 的约定一致
    holding_percent = (holding_pnl / cost_amount * 100) if cost_amount > 0 else None

    return {
        "market_value": _round(market_value),
        "prev_market_value": _round(prev_market_value),
        "day_pnl": _round(day_total) if day_covered > 0 else None,
        "day_pnl_percent": _round(day_percent),
        "cost_amount": _round(cost_amount),
        "holding_pnl": _round(holding_pnl) if cost_covered > 0 else None,
        "holding_pnl_percent": _round(holding_percent),
        "annual_income": _round(annual_income),
        "asset_count": len(rows),
        "day_pnl_covered_count": day_covered,
        "cost_covered_count": cost_covered,
        "missing_prev_close_count": len(rows) - day_covered,
    }


def top_movers(rows: List[dict], n: int = TOP_N) -> tuple:
    """今日涨跌榜 → (涨幅榜, 跌幅榜)，按今日盈亏金额排序。

    用**金额**而不是百分比排：¥10 万涨 1% 和 ¥1 千涨 10% 对账户的影响完全不同，
    日报关心的是「谁让账户今天多/少多少钱」。
    没有昨日盈亏（None）的标的不参与排名。
    """
    scored = [r for r in rows if _num(r.get("day_pnl")) is not None]
    scored.sort(key=lambda r: float(r["day_pnl"]), reverse=True)
    gainers = [r for r in scored if float(r["day_pnl"]) > 0][:n]
    losers = [r for r in scored if float(r["day_pnl"]) < 0][-n:][::-1]
    return gainers, losers


def income_breakdown(rows: List[dict], n: int = TOP_N) -> List[dict]:
    """年化收入贡献榜：按 Σ(持股数 × 每股分红) 降序，带占比。

    占比的分母是**组合总收入**（不是市值）：这张表回答的是
    「我的股息主要靠哪几只撑着」。
    """
    total = sum(
        (_num(r.get("annual_income")) or 0.0) for r in rows
    )
    if total <= 0:
        return []
    ranked = sorted(
        rows,
        key=lambda r: _num(r.get("annual_income")) or 0.0,
        reverse=True,
    )[:n]
    return [
        {
            "code": r.get("code"),
            "name": r.get("name"),
            "annual_income": _round(_num(r.get("annual_income")) or 0.0),
            "share_percent": _round((_num(r.get("annual_income")) or 0.0) / total * 100),
        }
        for r in ranked
        if (_num(r.get("annual_income")) or 0.0) > 0
    ]


def concentration(rows: List[dict]) -> dict:
    """持仓集中度：前一大 / 前三大的市值占比（%，分母为组合总市值）。

    用于回答「这个组合是不是其实就压在一两只票上」。
    总市值 <= 0 → 三个字段都是 None（空组合不该显示 0%）。
    """
    values = sorted(
        ((_num(r.get("quantity")) or 0.0) * (_num(r.get("price")) or 0.0) for r in rows),
        reverse=True,
    )
    total = sum(values)
    if total <= 0:
        return {
            "top1_code": None,
            "top1_name": None,
            "top1_share_percent": None,
            "top3_share_percent": None,
        }

    # 第一名要带名字，所以单独按 (name, value) 排一次
    named = sorted(
        (
            (
                _num(r.get("quantity")) or 0.0,
                _num(r.get("price")) or 0.0,
                r.get("code"),
                r.get("name"),
            )
            for r in rows
        ),
        key=lambda t: t[0] * t[1],
        reverse=True,
    )
    top1 = named[0]
    top1_value = top1[0] * top1[1]
    return {
        "top1_code": top1[2],
        "top1_name": top1[3],
        "top1_share_percent": _round(top1_value / total * 100),
        "top3_share_percent": _round(sum(values[:3]) / total * 100),
    }


# ---------- 观察池汇总 ----------

def summarize_watchlist(rows: List[dict], notional: float) -> dict:
    """观察池等权口径汇总。

    观察池没有真实持股数，所以「今日盈亏」只能是**测算值**：
    假设每只标的都投入 ¥notional（昨收价买入），今天合计赚/亏多少。
    这是唯一诚实的口径 —— 硬编一个仓位数字反而会误导。

    `avg_change_percent` 是各标的涨跌幅的**算术平均**（等权），
    与 `day_pnl / (notional × n)` 在「每只等额」时完全等价，保留两个字段
    是因为一个用于展示「平均涨了百分之几」，一个用于展示「合计多少钱」。
    """
    up = down = flat = 0
    changes: List[float] = []
    total_pnl = 0.0
    priced = 0
    for row in rows:
        pct = _num(row.get("change_percent"))
        if pct is None:
            continue
        priced += 1
        changes.append(pct)
        total_pnl += notional_pnl(pct, notional) or 0.0
        if pct > 0:
            up += 1
        elif pct < 0:
            down += 1
        else:
            flat += 1

    avg = (sum(changes) / len(changes)) if changes else None
    return {
        "item_count": len(rows),
        "priced_count": priced,
        "missing_prev_close_count": len(rows) - priced,
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
        "avg_change_percent": _round(avg),
        "notional_per_item": _round(notional),
        "day_pnl": _round(total_pnl) if priced > 0 else None,
        "day_pnl_percent": _round(avg),
    }


def signal_buckets(rows: List[dict]) -> Dict[str, List[dict]]:
    """按两条独立信号把标的归类（不合并成「可买入」结论，D3）。

    返回 hit_yield / hit_price / near_yield / near_price 四个桶，
    顺序沿用传入顺序（调用方已按自己关心的维度排好）。
    """
    buckets: Dict[str, List[dict]] = {
        "hit_yield": [],
        "hit_price": [],
        "near_yield": [],
        "near_price": [],
    }
    for row in rows:
        y_signal = row.get("yield_signal")
        p_signal = row.get("price_signal")
        if y_signal == "hit":
            buckets["hit_yield"].append(row)
        elif y_signal == "near":
            buckets["near_yield"].append(row)
        if p_signal == "hit":
            buckets["hit_price"].append(row)
        elif p_signal == "near":
            buckets["near_price"].append(row)
    return buckets
