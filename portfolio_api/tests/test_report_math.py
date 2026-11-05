"""`report_math` 纯函数单测：日报的口径都写死在这里，错了整份日报都是错的。

重点覆盖三个易错点：
1. **昨收缺失**：必须是 None（「不知道」），不能被算成 0（「没涨没跌」）；
2. **覆盖面**：缺一只标的的昨收，涨跌幅的分母只能算那部分标的的昨日市值，
   否则一只停牌股就能把整组合的涨跌幅拉偏；
3. **两条信号的归类**：股息率线 / 价格线互不合并（spec 003 D3），
   `near` 不能算进 `hit`。
"""

import pytest

from report_math import (
    change_amount,
    change_percent,
    concentration,
    day_pnl,
    income_breakdown,
    notional_pnl,
    signal_buckets,
    summarize_holdings,
    summarize_watchlist,
    top_movers,
)


# ---------- 单只标的 ----------

def test_change_amount_and_percent():
    assert change_amount(11.0, 10.0) == 1.0
    assert change_percent(11.0, 10.0) == 10.0
    assert change_amount(9.5, 10.0) == -0.5
    assert change_percent(9.5, 10.0) == -5.0


def test_change_returns_none_without_prev_close():
    """没有昨收 → None，而不是把昨收当 0 算成「涨了 100%」。"""
    assert change_amount(10.0, None) is None
    assert change_percent(10.0, None) is None
    assert change_percent(10.0, 0) is None
    assert change_percent(None, 10.0) is None


def test_day_pnl_scales_by_quantity():
    assert day_pnl(1000, 10.5, 10.0) == 500.0
    assert day_pnl(1000, 10.0, None) is None
    # 数量为 0 是合法输入：0 股就是 0 元
    assert day_pnl(0, 10.5, 10.0) == 0.0


def test_notional_pnl_for_watchlist():
    """观察池没有持股数：按「每只固定金额」测算 = 金额 × 涨跌幅。"""
    assert notional_pnl(2.5, 10000) == 250.0
    assert notional_pnl(-1.0, 10000) == -100.0
    assert notional_pnl(None, 10000) is None


# ---------- 组合汇总 ----------

def _row(code="600900", quantity=1000, price=11.0, prev_close=10.0, cost_price=8.0, dividend=0.5):
    return {
        "code": code,
        "name": code,
        "quantity": quantity,
        "price": price,
        "prev_close": prev_close,
        "cost_price": cost_price,
        "dividend_per_share": dividend,
        "annual_income": quantity * dividend,
    }


def test_summarize_holdings_basic():
    summary = summarize_holdings([_row()])

    assert summary["market_value"] == 11000.0
    assert summary["prev_market_value"] == 10000.0
    assert summary["day_pnl"] == 1000.0
    assert summary["day_pnl_percent"] == 10.0
    assert summary["cost_amount"] == 8000.0
    assert summary["holding_pnl"] == 3000.0
    assert summary["holding_pnl_percent"] == 37.5
    assert summary["annual_income"] == 500.0
    assert summary["asset_count"] == 1
    assert summary["day_pnl_covered_count"] == 1
    assert summary["missing_prev_close_count"] == 0


def test_summarize_holdings_excludes_missing_prev_close_from_both_sides():
    """缺昨收的标的：既不进分子也不进分母 —— 否则涨跌幅会被单只票带偏。"""
    rows = [
        _row(code="A", quantity=1000, price=11.0, prev_close=10.0),   # +1000
        _row(code="B", quantity=1000, price=50.0, prev_close=None),   # 算不出今日盈亏
    ]
    summary = summarize_holdings(rows)

    assert summary["day_pnl"] == 1000.0
    # 分母只含 A 的昨日市值 10,000（若把 B 的 50,000 算进去，涨跌幅会变成 1.67%）
    assert summary["prev_market_value"] == 10000.0
    assert summary["day_pnl_percent"] == 10.0
    assert summary["missing_prev_close_count"] == 1
    # 市值仍然包含 B：它确实是我的持仓
    assert summary["market_value"] == 11000.0 + 50000.0


def test_summarize_holdings_all_missing_prev_close_gives_none():
    summary = summarize_holdings([_row(prev_close=None), _row(code="B", prev_close=None)])
    assert summary["day_pnl"] is None
    assert summary["day_pnl_percent"] is None
    assert summary["missing_prev_close_count"] == 2


def test_summarize_holdings_cost_coverage_and_negative_cost():
    rows = [_row(code="A", cost_price=8.0), _row(code="B", cost_price=None)]
    summary = summarize_holdings(rows)
    assert summary["cost_covered_count"] == 1
    assert summary["cost_amount"] == 8000.0

    # 负成本（分红摊薄）：金额仍算，百分比无意义 → None
    summary = summarize_holdings([_row(cost_price=-2.0)])
    assert summary["holding_pnl"] == (11.0 - (-2.0)) * 1000
    assert summary["holding_pnl_percent"] is None


def test_summarize_holdings_empty():
    summary = summarize_holdings([])
    assert summary["asset_count"] == 0
    assert summary["market_value"] == 0.0
    assert summary["day_pnl"] is None
    assert summary["day_pnl_percent"] is None


def test_top_movers_sorts_by_money_not_percent():
    """榜单按金额排：¥10 万涨 1%（+1000）排在 ¥1 千涨 10%（+100）前面。"""
    rows = [
        {**_row(code="BIG"), "day_pnl": 1000.0},
        {**_row(code="SMALL"), "day_pnl": 100.0},
        {**_row(code="LOSS_SMALL"), "day_pnl": -50.0},
        {**_row(code="LOSS_BIG"), "day_pnl": -800.0},
        {**_row(code="UNKNOWN"), "day_pnl": None},
    ]
    gainers, losers = top_movers(rows, n=2)

    assert [r["code"] for r in gainers] == ["BIG", "SMALL"]
    # 跌得最狠的排最前
    assert [r["code"] for r in losers] == ["LOSS_BIG", "LOSS_SMALL"]


def test_top_movers_skips_zero_and_keeps_short_lists():
    gainers, losers = top_movers([{**_row(code="FLAT"), "day_pnl": 0.0}], n=3)
    assert gainers == [] and losers == []


def test_income_breakdown_share_uses_income_not_market_value():
    rows = [
        {**_row(code="A", quantity=1000, dividend=0.6), "annual_income": 600.0},
        {**_row(code="B", quantity=1000, dividend=0.4), "annual_income": 400.0},
    ]
    top = income_breakdown(rows, n=2)
    assert [t["code"] for t in top] == ["A", "B"]
    assert top[0]["share_percent"] == 60.0
    assert top[1]["share_percent"] == 40.0


def test_income_breakdown_without_dividends_is_empty():
    rows = [{**_row(dividend=0), "annual_income": 0.0}]
    assert income_breakdown(rows) == []


def test_concentration_top1_and_top3():
    rows = [
        {**_row(code="A", quantity=100, price=50.0), "name": "A"},
        {**_row(code="B", quantity=100, price=30.0), "name": "B"},
        {**_row(code="C", quantity=100, price=15.0), "name": "C"},
        {**_row(code="D", quantity=100, price=5.0), "name": "D"},
    ]
    result = concentration(rows)
    # 总市值 10,000；最大 5,000 → 50%；前三 9,500 → 95%
    assert result["top1_code"] == "A"
    assert result["top1_share_percent"] == 50.0
    assert result["top3_share_percent"] == 95.0


def test_concentration_empty_portfolio():
    result = concentration([])
    assert result["top1_share_percent"] is None
    assert result["top3_share_percent"] is None


# ---------- 观察池汇总 ----------

def _watch_row(pct, code="X", y_signal="watch", p_signal="watch"):
    return {
        "code": code,
        "name": code,
        "change_percent": pct,
        "yield_signal": y_signal,
        "price_signal": p_signal,
    }


def test_summarize_watchlist_equal_weight():
    rows = [_watch_row(2.0, "A"), _watch_row(-1.0, "B"), _watch_row(0.0, "C")]
    summary = summarize_watchlist(rows, notional=10000)

    assert summary["item_count"] == 3
    assert summary["priced_count"] == 3
    assert summary["up_count"] == 1
    assert summary["down_count"] == 1
    assert summary["flat_count"] == 1
    # 平均涨跌幅 = (2 - 1 + 0) / 3
    assert summary["avg_change_percent"] == pytest.approx(1 / 3)
    # 每只 1 万：+200 -100 +0
    assert summary["day_pnl"] == 100.0


def test_summarize_watchlist_skips_unknown_change():
    rows = [_watch_row(2.0, "A"), _watch_row(None, "B")]
    summary = summarize_watchlist(rows, notional=10000)
    assert summary["priced_count"] == 1
    assert summary["missing_prev_close_count"] == 1
    assert summary["avg_change_percent"] == 2.0
    assert summary["day_pnl"] == 200.0


def test_summarize_watchlist_empty():
    summary = summarize_watchlist([], notional=10000)
    assert summary["item_count"] == 0
    assert summary["day_pnl"] is None
    assert summary["avg_change_percent"] is None


def test_signal_buckets_keeps_two_lines_independent():
    """同一只标的可以「股息率达标但价格没到」，两条线各归各的桶。"""
    buckets = signal_buckets(
        [
            _watch_row(0, "Y", y_signal="hit", p_signal="watch"),
            _watch_row(0, "P", y_signal="watch", p_signal="hit"),
            _watch_row(0, "YN", y_signal="near", p_signal="not_set"),
            _watch_row(0, "PN", y_signal="unknown", p_signal="near"),
        ]
    )
    assert [r["code"] for r in buckets["hit_yield"]] == ["Y"]
    assert [r["code"] for r in buckets["hit_price"]] == ["P"]
    assert [r["code"] for r in buckets["near_yield"]] == ["YN"]
    assert [r["code"] for r in buckets["near_price"]] == ["PN"]
