"""`portfolio_math.reinvest_years` 单测：只靠股息复投，多少年能达到目标。

这是「目标达成路径 ③」的唯一实现。它最容易出的错不是公式写错，
而是给出一个**看起来很像真的**年数，所以这里重点压三类断言：

1. **自洽性**：按返回的年数把收入复利回去，必须真的够到目标；
   再用逐年模拟（本金复利 × 收益率）交叉验证 —— 两条独立路径必须给出同一个年数。
2. **不可达**：没有股息 / 收益率为 0 时返回 None + 一句能直接展示的原因，
   绝不能返回 0 或一个巨大但"合法"的数字。
3. **浮点边界**：正好整数年（1000 → 1050 @5%）不能被 1e-16 的误差进成「第 2 年」。
"""

import math

import pytest

from portfolio_math import reinvest_years


# ---------- 公式本身 ----------

def test_basic_formula():
    """1000 → 5000、年化 6.5%：ln(5) / ln(1.065) ≈ 25.6 年"""
    r = reinvest_years(1000, 5000, 6.5)

    assert r["years"] == pytest.approx(round(math.log(5) / math.log(1.065), 1))
    assert r["years_ceil"] == 26
    assert r["note"] == ""


@pytest.mark.parametrize(
    "current,target,y",
    [
        (1000, 5000, 6.5),
        (4656.4, 100000, 4.813927642700939),
        (1200, 12000, 12.0),
        (500, 800, 3.2),
    ],
)
def test_compound_back_reaches_target(current, target, y):
    """自洽性：第 years_ceil 年必须够到目标，且前一年还不够（否则年数算少了）"""
    r = reinvest_years(current, target, y)

    assert current * (1 + y / 100) ** r["years_ceil"] >= target
    assert current * (1 + y / 100) ** (r["years_ceil"] - 1) < target


def test_matches_year_by_year_simulation():
    """与「本金复利 × 收益率」逐年模拟交叉验证（独立的第二条路径）

    D0 = 市值 × y，每年股息全数买入 → 市值按 (1+y) 增长，收入始终 = 市值 × y。
    直接对收入复利应当得到同一个「第几年」。
    """
    market_value, y, target = 100_000.0, 5.0, 30_000.0
    value = market_value
    years = 0
    while value * y / 100 < target:
        value *= 1 + y / 100
        years += 1

    r = reinvest_years(market_value * y / 100, target, y)

    assert r["years_ceil"] == years


def test_higher_yield_needs_fewer_years():
    """收益率越高，年数单调减少（界面上调大输入框必须看到年数变小）"""
    years = [reinvest_years(1000, 10000, y)["years"] for y in (2, 4, 6, 8)]

    assert years == sorted(years, reverse=True)


# ---------- 无需等待 ----------

def test_already_achieved_returns_zero():
    """已达成 / 正好达标：0 年，且不该带任何「不可达」原因"""
    for current in (120_000, 100_000):
        r = reinvest_years(current, 100_000, 5)
        assert r["years"] == 0.0
        assert r["years_ceil"] == 0
        assert r["note"] == ""


def test_tiny_gap_still_reports_positive_years():
    """缺口 0.1% 时 round 会把 0.04 变成 0.0，读起来像「马上就达成」——必须抬到 0.1"""
    r = reinvest_years(99_900, 100_000, 5)

    assert r["years"] == 0.1
    assert r["years_ceil"] == 1


# ---------- 不可达 ----------

def test_no_current_income_is_unreachable():
    """当前没有股息：没有钱可复投，无论收益率多高都不成立"""
    r = reinvest_years(0, 100_000, 5)

    assert r["years"] is None
    assert r["years_ceil"] is None
    assert r["note"] == "当前无股息收入"


@pytest.mark.parametrize("y", [0, -1, -5])
def test_non_positive_yield_is_unreachable(y):
    """收益率 <= 0：收入恒定，永远到不了（不能返回 inf 或一个超大数）"""
    r = reinvest_years(4656.4, 100_000, y)

    assert r["years"] is None
    assert r["years_ceil"] is None
    assert r["note"] == "复投收益率为 0"


def test_non_finite_yield_is_unreachable():
    """NaN / inf 也不能算出一个数来（后端字段是 float，脏数据要挡住）"""
    for y in (float("nan"), float("inf")):
        assert reinvest_years(1000, 5000, y)["years"] is None


def test_zero_target_is_guarded():
    """目标 <= 0（schema 已拦住，纯防御）：不除零、不返回一个负数年数"""
    r = reinvest_years(1000, 0, 5)

    assert r["years"] is None
    assert r["note"]


# ---------- 浮点边界 ----------

def test_exact_integer_year_is_not_rounded_up():
    """1000 → 1050 @5% 正好 1 年：ceil 必须是 1，不能因浮点误差变 2"""
    r = reinvest_years(1000, 1050, 5)

    assert r["years"] == 1.0
    assert r["years_ceil"] == 1


def test_large_years_keeps_one_decimal():
    """高年份也保留 1 位小数，不在后端提前取整（展示层决定怎么说）"""
    r = reinvest_years(4656.4, 100_000, 4.813927642700939)

    assert r["years"] == 65.2
    assert r["years_ceil"] == 66
