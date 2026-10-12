"""ETF / 基金支持相关测试。

覆盖三类：
  1. market.py 代码规则（前缀、类型）
  2. ETF 分红数据模型转换（sina「累计分红」→ 每期金额）
  3. 端到端：创建 ETF 资产、TTM 口径
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from market import (
    guess_asset_type,
    is_etf,
    market_code,
    market_label,
    normalize_code,
)


# ========== 1. market.py 规则 ==========

@pytest.mark.parametrize("code,expected_prefix", [
    ("600900", "sh"),   # 沪市主板
    ("601088", "sh"),   # 沪市主板
    ("688981", "sh"),   # 科创板
    ("900901", "sh"),   # 沪市 B 股
    ("561580", "sh"),   # 沪市 ETF ★ 旧逻辑会错判成 sz
    ("510050", "sh"),   # 上证 50ETF
    ("588000", "sh"),   # 科创 50ETF
    ("000001", "sz"),   # 深市主板
    ("002142", "sz"),   # 深市中小板
    ("300750", "sz"),   # 创业板
    ("200011", "sz"),   # 深市 B 股
    ("159915", "sz"),   # 深市 ETF
    ("161725", "sz"),   # 深市 LOF
])
def test_market_prefix(code, expected_prefix):
    assert market_code(code).startswith(expected_prefix)


@pytest.mark.parametrize("code,expected", [
    ("561580", True), ("510050", True), ("512880", True), ("588000", True),
    ("159915", True), ("161725", True), ("180101", True),
    ("600900", False), ("000001", False), ("300750", False), ("688981", False),
    ("900901", False), ("200011", False),
])
def test_is_etf(code, expected):
    assert is_etf(code) is expected


def test_prefix_normalization():
    assert normalize_code("sh561580") == "561580"
    assert normalize_code("SZ159915") == "159915"
    assert normalize_code(" 600900 ") == "600900"
    assert normalize_code("561580") == "561580"


def test_guess_asset_type():
    assert guess_asset_type("561580") == "ETF"
    assert guess_asset_type("600900") == "股票"


def test_market_label():
    assert market_label("561580") == "沪市"
    assert market_label("159915") == "深市"


def test_etf_regression_571580_old_logic_would_be_wrong():
    """回归：旧实现 '6/9→sh 其它→sz' 会把 561580 判成 sz561580（无效）。"""
    assert market_code("561580") == "sh561580"
    assert market_code("561580") != "sz561580"


# ========== 2. ETF 分红「累计 → 每期」转换 ==========

def test_etf_dividend_cumulative_to_per_period(monkeypatch):
    """sina 返回累计分红，必须差分还原每期金额。"""
    import routers.stocks as stocks_mod

    fake_df = pd.DataFrame({
        "日期": ["2025-05-19", "2025-06-16", "2025-07-14", "2025-08-18"],
        "累计分红": [0.005, 0.010, 0.015, 0.035],
    })
    monkeypatch.setattr(stocks_mod.ak, "fund_etf_dividend_sina", lambda symbol: fake_df)

    ok, rows = stocks_mod._fetch_etf_dividend_blocking("561580")

    assert ok is True
    assert len(rows) == 4
    assert [r["DIVIDEND_PER_SHARE"] for r in rows] == [0.005, 0.005, 0.005, 0.020]
    # 累加应回到最终累计值
    assert round(sum(r["DIVIDEND_PER_SHARE"] for r in rows), 6) == 0.035
    # ETF 无送股/转增，且一律视为已实施
    assert all(r["BONUS_IT_RATIO"] == 0 for r in rows)
    assert all(r["TRANSFER_RATIO"] == 0 for r in rows)
    assert all(r["IMPLEMENT_STATUS"] == "已实施" for r in rows)


def test_etf_dividend_handles_unsorted_input(monkeypatch):
    """输入乱序时应先排序，否则差分结果错误。"""
    import routers.stocks as stocks_mod

    fake_df = pd.DataFrame({
        "日期": ["2025-08-18", "2025-05-19", "2025-07-14", "2025-06-16"],
        "累计分红": [0.035, 0.005, 0.015, 0.010],
    })
    monkeypatch.setattr(stocks_mod.ak, "fund_etf_dividend_sina", lambda symbol: fake_df)

    _, rows = stocks_mod._fetch_etf_dividend_blocking("561580")
    assert [r["DIVIDEND_PER_SHARE"] for r in rows] == [0.005, 0.005, 0.005, 0.020]


def test_etf_dividend_empty_is_success(monkeypatch):
    """空结果是合法结果（该基金确实没分过红），ok=True 才允许被缓存。"""
    import routers.stocks as stocks_mod

    monkeypatch.setattr(
        stocks_mod.ak, "fund_etf_dividend_sina", lambda symbol: pd.DataFrame()
    )
    ok, rows = stocks_mod._fetch_etf_dividend_blocking("561580")
    assert ok is True
    assert rows == []


def test_etf_dividend_failure_is_not_cached(monkeypatch):
    """★ 回归：sina 偶发 SSL 断连时，ok=False 且**不得写入缓存**。

    线上实录：第一次 refresh 遇到
        SSLError(SSL: UNEXPECTED_EOF_WHILE_READING)
    空结果被缓存 6 小时，导致该 ETF 股息率一直显示 0。
    """
    import asyncio
    import routers.stocks as stocks_mod

    def boom(symbol):
        raise RuntimeError("SSL: UNEXPECTED_EOF_WHILE_READING")

    monkeypatch.setattr(stocks_mod.ak, "fund_etf_dividend_sina", boom)
    stocks_mod.invalidate_dividend_cache()

    ok, rows = stocks_mod._fetch_etf_dividend_blocking("561580")
    assert ok is False
    assert rows == []

    # 走完整入口：失败后缓存里不应该有 561580 这一条
    out = asyncio.run(stocks_mod.fetch_dividend_from_eastmoney("561580"))
    assert out == []
    assert "561580" not in stocks_mod._dividend_cache

    # 网络恢复后立即能拿到数据，无需等 TTL 过期
    fake_df = pd.DataFrame({"日期": ["2025-05-19"], "累计分红": [0.005]})
    monkeypatch.setattr(stocks_mod.ak, "fund_etf_dividend_sina", lambda symbol: fake_df)
    out = asyncio.run(stocks_mod.fetch_dividend_from_eastmoney("561580"))
    assert len(out) == 1
    assert out[0]["DIVIDEND_PER_SHARE"] == 0.005


def test_stale_cache_served_when_refetch_fails(monkeypatch):
    """已有过期缓存时，重新拉取失败应回退到旧数据而不是返回空。"""
    import asyncio
    import time as _time
    import routers.stocks as stocks_mod

    stocks_mod.invalidate_dividend_cache()
    stale_rows = [{"EX_DIVIDEND_DATE": "2025-01-01",
                   "DIVIDEND_PER_SHARE": 0.01,
                   "IMPLEMENT_STATUS": "已实施"}]
    # 手动塞一条已过期的缓存
    stocks_mod._dividend_cache["561580"] = (
        _time.time() - stocks_mod.DIVIDEND_CACHE_TTL_SECONDS - 10,
        stale_rows,
    )

    def boom(symbol):
        raise RuntimeError("network down")

    monkeypatch.setattr(stocks_mod.ak, "fund_etf_dividend_sina", boom)
    out = asyncio.run(stocks_mod.fetch_dividend_from_eastmoney("561580"))
    assert out == stale_rows


def test_fetch_dividend_dispatches_by_type(monkeypatch):
    """fetch_dividend_from_eastmoney 应按代码类型分派到不同数据源。"""
    import routers.stocks as stocks_mod

    called = {"etf": [], "stock": []}

    monkeypatch.setattr(
        stocks_mod, "_fetch_etf_dividend_blocking",
        lambda code: (called["etf"].append(code) or True, []),
    )
    monkeypatch.setattr(
        stocks_mod, "_fetch_stock_dividend_blocking",
        lambda code: (called["stock"].append(code) or True, []),
    )
    stocks_mod.invalidate_dividend_cache()

    import asyncio
    asyncio.run(stocks_mod.fetch_dividend_from_eastmoney("561580"))
    stocks_mod.invalidate_dividend_cache()
    asyncio.run(stocks_mod.fetch_dividend_from_eastmoney("600900"))

    assert called["etf"] == ["561580"]
    assert called["stock"] == ["600900"]


# ========== 3. TTM 口径 ==========

def test_ttm_uses_trailing_12_months():
    """ETF 月度分红：TTM 必须是近 12 个月合计，不能只取最近一期。"""
    from routers.assets import _sum_trailing_12m

    today = date(2026, 10, 12)
    # 构造 24 期月度分红，每期 0.01
    rows = []
    for i in range(24):
        d = today - timedelta(days=30 * (i + 1))
        rows.append({
            "EX_DIVIDEND_DATE": d,
            "DIVIDEND_PER_SHARE": 0.01,
            "IMPLEMENT_STATUS": "已实施",
        })
    ttm = _sum_trailing_12m(rows, today=today)
    # 近 12 个月内约 12 期 × 0.01
    assert 0.11 <= ttm <= 0.13, ttm


def test_ttm_excludes_pending_and_future():
    from routers.assets import _sum_trailing_12m

    today = date(2026, 10, 12)
    rows = [
        {"EX_DIVIDEND_DATE": today - timedelta(days=10),
         "DIVIDEND_PER_SHARE": 0.05, "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": today - timedelta(days=20),
         "DIVIDEND_PER_SHARE": 0.09, "IMPLEMENT_STATUS": "待实施"},   # 排除
        {"EX_DIVIDEND_DATE": today + timedelta(days=5),
         "DIVIDEND_PER_SHARE": 0.50, "IMPLEMENT_STATUS": "已实施"},   # 未来，排除
        {"EX_DIVIDEND_DATE": "", "DIVIDEND_PER_SHARE": 0.30,
         "IMPLEMENT_STATUS": "已实施"},                                # 无日期，排除
    ]
    assert _sum_trailing_12m(rows, today=today) == 0.05


def test_ttm_real_etf_shape():
    """用 561580 的真实数据形态验证：单期 0.005 vs TTM 0.095 差 19 倍。"""
    from routers.assets import _sum_trailing_12m

    rows = [
        {"EX_DIVIDEND_DATE": "2025-05-19", "DIVIDEND_PER_SHARE": 0.005, "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2025-06-16", "DIVIDEND_PER_SHARE": 0.005, "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2025-07-14", "DIVIDEND_PER_SHARE": 0.005, "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2025-08-18", "DIVIDEND_PER_SHARE": 0.020, "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2025-09-15", "DIVIDEND_PER_SHARE": 0.020, "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2025-10-20", "DIVIDEND_PER_SHARE": 0.020, "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2025-11-17", "DIVIDEND_PER_SHARE": 0.010, "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2025-12-15", "DIVIDEND_PER_SHARE": 0.010, "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2026-01-21", "DIVIDEND_PER_SHARE": 0.010, "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2026-02-26", "DIVIDEND_PER_SHARE": 0.010, "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2026-03-23", "DIVIDEND_PER_SHARE": 0.005, "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2026-04-20", "DIVIDEND_PER_SHARE": 0.005, "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2026-05-18", "DIVIDEND_PER_SHARE": 0.005, "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2026-06-15", "DIVIDEND_PER_SHARE": 0.005, "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2026-07-20", "DIVIDEND_PER_SHARE": 0.005, "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2026-08-17", "DIVIDEND_PER_SHARE": 0.005, "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2026-09-14", "DIVIDEND_PER_SHARE": 0.005, "IMPLEMENT_STATUS": "已实施"},
    ]
    ttm = _sum_trailing_12m(rows, today=date(2026, 10, 12))
    assert ttm == pytest.approx(0.095, abs=1e-9)
    # 按 1.286 元价格，正确收益率 ~7.4%，而非单期口径的 0.39%
    assert ttm / 1.286 * 100 == pytest.approx(7.39, abs=0.05)
    assert ttm / 1.286 * 100 > 5


# ========== 4. 端到端（HTTP） ==========

def test_create_etf_asset_infers_type(client):
    """建 ETF 资产时若不指定 asset_type，后端应按代码推断为 ETF。"""
    r = client.post("/api/portfolios/1/assets", json={
        "name": "央企红利ETF华泰柏瑞",
        "code": "561580",
        "quantity": 10000,
        "price": 1.286,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["asset_type"] == "ETF"
    assert body["code"] == "561580"
    assert body["holding_value"] == pytest.approx(10000 * 1.286)


def test_create_etf_asset_accepts_prefixed_code(client):
    """带 sh 前缀的代码应被规整为纯数字。"""
    r = client.post("/api/portfolios/1/assets", json={
        "name": "上证50ETF",
        "code": "sh510050",
        "quantity": 100,
        "price": 3.0,
    })
    assert r.status_code == 200, r.text
    assert r.json()["code"] == "510050"
    assert r.json()["asset_type"] == "ETF"


def test_create_stock_still_infers_stock(client):
    r = client.post("/api/portfolios/1/assets", json={
        "name": "长江电力", "code": "600900", "quantity": 100, "price": 28.5,
    })
    assert r.status_code == 200
    assert r.json()["asset_type"] == "股票"


def test_explicit_asset_type_is_respected(client):
    """用户显式传了 asset_type 就应尊重，不被代码推断覆盖。"""
    r = client.post("/api/portfolios/1/assets", json={
        "name": "自定义", "code": "600900", "quantity": 1, "price": 1.0,
        "asset_type": "自定义类型",
    })
    assert r.status_code == 200
    assert r.json()["asset_type"] == "自定义类型"


def test_etf_dividend_flows_into_assets_endpoint(client, monkeypatch):
    """ETF 分红应能通过 /assets 端点算出 y2025/y2024 与 TTM。"""
    import routers.stocks as stocks_mod

    fake_df = pd.DataFrame({
        "日期": ["2024-06-15", "2025-05-19", "2025-08-18"],
        "累计分红": [0.400, 0.405, 0.425],
    })
    monkeypatch.setattr(stocks_mod.ak, "fund_etf_dividend_sina", lambda symbol: fake_df)
    stocks_mod.invalidate_dividend_cache()

    r = client.post("/api/portfolios/1/assets", json={
        "name": "红利ETF", "code": "561580", "quantity": 1000, "price": 1.286,
    })
    assert r.status_code == 200

    r = client.get("/api/portfolios/1/assets")
    assert r.status_code == 200
    etf = [a for a in r.json() if a["code"] == "561580"][0]
    # 2024: 0.400（首期即全部）
    assert etf["y2024_dividend"] == pytest.approx(0.400)
    # 2025: (0.405-0.400) + (0.425-0.405) = 0.025
    assert etf["y2025_dividend"] == pytest.approx(0.025)


def test_etf_refresh_price_uses_trailing_ttm(client, monkeypatch, mock_tencent_ok):
    """刷新 ETF 价格时，TTM 应基于近 12 个月合计，而非最近一期。"""
    import routers.stocks as stocks_mod
    from datetime import date as _date

    today = _date.today()
    # 12 期月度分红，每期 0.01；最近一期很小，用于验证不会被误用
    dates, cums = [], []
    cum = 0.0
    for i in range(12):
        d = today - timedelta(days=30 * (i + 1))
        cum += 0.01
        dates.append(d.strftime("%Y-%m-%d"))
        cums.append(round(cum, 6))
    fake_df = pd.DataFrame({"日期": dates, "累计分红": cums})
    monkeypatch.setattr(stocks_mod.ak, "fund_etf_dividend_sina", lambda symbol: fake_df)
    stocks_mod.invalidate_dividend_cache()

    r = client.post("/api/portfolios/1/assets", json={
        "name": "月度红利ETF", "code": "561580", "quantity": 1000, "price": 1.0,
    })
    asset_id = r.json()["id"]

    r = client.post(f"/api/assets/{asset_id}/refresh-price")
    assert r.status_code == 200, r.text
    body = r.json()
    # mock_tencent_ok 固定返回 10.0 元
    assert body["price"] == 10.0
    # 12 期 × 0.01 = 0.12（TTM），而不是最近一期 0.01
    assert body["dividend_per_share"] == pytest.approx(0.12, abs=1e-6)
    assert body["ttm_yield"] == pytest.approx(1.2, abs=0.01)
