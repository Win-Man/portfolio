"""TTM（滚动 12 个月）分红/收益率口径测试。

★ 核心回归：`akshare.stock_dividend_cninfo` 返回的是**升序**（最老在前）。
早期实现假设"降序、第一条即最新"，直接取 `data[0]`，于是取到了最老的一期分红
（长江电力取到 2004 年的 0.09 而不是 2026 年的 0.79），TTM 收益率被低估 5~50 倍。

这里用**真实的 600900 分红历史**（26 期）做夹具锁死行为。
"""
from datetime import date, timedelta

import pytest

from routers.assets import _parse_dividend_date, _sum_trailing_12m

TODAY = date(2026, 10, 12)

# 长江电力 600900 的真实分红历史（akshare 返回顺序：升序，最老在前）
# 数据抓取日期 2026-10-12
CJDL_HISTORY_ASC = [
    ("2004-06-01", 0.0900),
    ("2005-05-24", 0.2100),
    ("", 0.1740),            # 无除权日 → 应被忽略
    ("2006-06-07", 0.1890),
    ("2007-06-11", 0.2353),
    ("2008-07-22", 0.2968),
    ("2009-08-07", 0.2159),
    ("2010-07-20", 0.3220),
    ("2011-06-23", 0.2558),
    ("2012-07-02", 0.2546),
    ("2013-07-01", 0.3316),
    ("2014-07-11", 0.2804),
    ("2015-06-30", 0.3791),
    ("2016-07-19", 0.4000),
    ("2017-07-14", 0.7250),
    ("2018-06-22", 0.6800),
    ("2019-07-18", 0.6800),
    ("2020-07-17", 0.6800),
    ("2021-07-16", 0.7000),
    ("2022-07-21", 0.8153),
    ("2023-07-21", 0.8533),
    ("2024-07-19", 0.8200),
    ("2025-01-24", 0.2100),
    ("2025-07-18", 0.7330),
    ("2026-02-12", 0.2100),
    ("2026-07-17", 0.7900),
]


def _as_rows(history):
    return [
        {"EX_DIVIDEND_DATE": d, "DIVIDEND_PER_SHARE": v,
         "IMPLEMENT_STATUS": "已实施"}
        for d, v in history
    ]


# ========== ★ 核心回归 ==========

def test_ttm_does_not_take_oldest_dividend():
    """回归：绝不能再取到 2004 年的 0.09。"""
    rows = _as_rows(CJDL_HISTORY_ASC)
    ttm = _sum_trailing_12m(rows, today=TODAY)

    # 近 12 个月（2025-10-12 ~ 2026-10-12）内的两期：
    #   2026-02-12  0.2100
    #   2026-07-17  0.7900
    assert ttm == pytest.approx(1.0000, abs=1e-9)

    # 明确排除：最老的那期（旧 bug 取的就是它）
    assert ttm != pytest.approx(0.09, abs=1e-9)


def test_ttm_is_order_independent():
    """升序 / 降序 / 乱序输入必须得到相同结果。"""
    rows_asc = _as_rows(CJDL_HISTORY_ASC)
    rows_desc = _as_rows(list(reversed(CJDL_HISTORY_ASC)))
    shuffled = CJDL_HISTORY_ASC[::3] + CJDL_HISTORY_ASC[1::3] + CJDL_HISTORY_ASC[2::3]
    rows_shuffled = _as_rows(shuffled)

    a = _sum_trailing_12m(rows_asc, today=TODAY)
    b = _sum_trailing_12m(rows_desc, today=TODAY)
    c = _sum_trailing_12m(rows_shuffled, today=TODAY)

    assert a == b == c == pytest.approx(1.0, abs=1e-9)


def test_ttm_yield_for_cjdl_is_realistic():
    """长江电力 TTM 收益率应为约 3.4%，而不是错误的 0.31%。"""
    rows = _as_rows(CJDL_HISTORY_ASC)
    ttm = _sum_trailing_12m(rows, today=TODAY)
    price = 29.38

    correct = ttm / price * 100
    buggy = 0.09 / price * 100

    assert correct == pytest.approx(3.40, abs=0.02)
    assert buggy == pytest.approx(0.31, abs=0.01)
    # 修复前后的差距
    assert correct / buggy > 10


def test_ttm_boundary_excludes_just_outside_12m():
    """恰好超出 12 个月的一期不能计入。"""
    rows = _as_rows([
        ("2025-10-11", 0.50),   # 比 cutoff(2025-10-12) 早 1 天 → 排除
        ("2025-10-13", 0.30),   # 比 cutoff 晚 1 天 → 计入
    ])
    assert _sum_trailing_12m(rows, today=TODAY) == pytest.approx(0.30)


def test_ttm_excludes_pending():
    """「待实施」的分红不计入 TTM。"""
    rows = [
        {"EX_DIVIDEND_DATE": "2026-05-01", "DIVIDEND_PER_SHARE": 0.40,
         "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2026-06-01", "DIVIDEND_PER_SHARE": 0.99,
         "IMPLEMENT_STATUS": "待实施"},
    ]
    assert _sum_trailing_12m(rows, today=TODAY) == pytest.approx(0.40)


def test_ttm_excludes_future():
    """已公告但除权日在未来的分红不计入。"""
    rows = [
        {"EX_DIVIDEND_DATE": "2026-05-01", "DIVIDEND_PER_SHARE": 0.40,
         "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2026-12-01", "DIVIDEND_PER_SHARE": 0.88,
         "IMPLEMENT_STATUS": "已实施"},
    ]
    assert _sum_trailing_12m(rows, today=TODAY) == pytest.approx(0.40)


def test_ttm_excludes_missing_date():
    """无除权日的记录（akshare 偶发）不计入，且不应崩溃。"""
    rows = [
        {"EX_DIVIDEND_DATE": "", "DIVIDEND_PER_SHARE": 9.99,
         "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": None, "DIVIDEND_PER_SHARE": 9.99,
         "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "garbage", "DIVIDEND_PER_SHARE": 9.99,
         "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2026-05-01", "DIVIDEND_PER_SHARE": 0.40,
         "IMPLEMENT_STATUS": "已实施"},
    ]
    assert _sum_trailing_12m(rows, today=TODAY) == pytest.approx(0.40)


def test_ttm_empty_history():
    assert _sum_trailing_12m([], today=TODAY) == 0.0


# ========== 日期解析 ==========

@pytest.mark.parametrize("raw,expected", [
    ("2026-07-17", date(2026, 7, 17)),
    ("20260717", date(2026, 7, 17)),
    ("2026/07/17", date(2026, 7, 17)),
    (date(2026, 7, 17), date(2026, 7, 17)),
    ("", None),
    (None, None),
    ("not-a-date", None),
])
def test_parse_dividend_date(raw, expected):
    got = _parse_dividend_date(raw)
    if expected is None:
        assert got is None
    else:
        assert got == expected


def test_parse_dividend_date_from_datetime():
    from datetime import datetime as _dt
    assert _parse_dividend_date(_dt(2026, 7, 17, 15, 30)) == date(2026, 7, 17)


def test_parse_dividend_date_from_pandas_timestamp():
    pd = pytest.importorskip("pandas")
    assert _parse_dividend_date(pd.Timestamp("2026-07-17")) == date(2026, 7, 17)


# ========== 统一入口 ==========

def test_fetch_ttm_dividend_per_share_unified(monkeypatch):
    """个股与 ETF 都应走滚动 12 个月口径，不再分叉。"""
    import asyncio
    import routers.assets as assets_mod

    rows = _as_rows(CJDL_HISTORY_ASC)

    async def fake_fetch(code):
        return rows

    monkeypatch.setattr(assets_mod, "fetch_dividend_from_eastmoney", fake_fetch)

    # 个股
    stock_val = asyncio.run(assets_mod.fetch_ttm_dividend_per_share("600900"))
    assert stock_val == pytest.approx(1.0, abs=1e-9)

    # ETF 走同一函数，口径一致
    etf_val = asyncio.run(assets_mod.fetch_ttm_dividend_per_share("561580"))
    assert etf_val == pytest.approx(1.0, abs=1e-9)


def test_fetch_ttm_dividend_per_share_empty(monkeypatch):
    import asyncio
    import routers.assets as assets_mod

    async def fake_fetch(code):
        return []

    monkeypatch.setattr(assets_mod, "fetch_dividend_from_eastmoney", fake_fetch)
    assert asyncio.run(assets_mod.fetch_ttm_dividend_per_share("600900")) == 0.0


# ========== 端到端：刷新后 DB 里的 TTM 必须正确 ==========

def test_refresh_price_writes_correct_ttm_for_stock(client, monkeypatch, mock_tencent_ok):
    """★ 端到端回归：刷新个股价格后，dividend_per_share 必须是 TTM 合计。"""
    import routers.assets as assets_mod

    rows = _as_rows(CJDL_HISTORY_ASC)

    async def fake_fetch(code):
        return rows

    monkeypatch.setattr(assets_mod, "fetch_dividend_from_eastmoney", fake_fetch)

    r = client.post("/api/portfolios/1/assets", json={
        "name": "长江电力", "code": "600900", "quantity": 1000, "price": 29.38,
    })
    assert r.status_code == 200, r.text
    asset_id = r.json()["id"]

    r = client.post(f"/api/assets/{asset_id}/refresh-price")
    assert r.status_code == 200, r.text
    body = r.json()

    # mock_tencent_ok 固定返回 10.0 元
    assert body["price"] == 10.0
    # TTM 每股分红 = 1.00（不是 2004 年的 0.09）
    assert body["dividend_per_share"] == pytest.approx(1.0, abs=1e-9)
    assert body["ttm_yield"] == pytest.approx(10.0, abs=0.01)   # 1.0/10.0*100


def test_bulk_refresh_writes_correct_ttm(client, monkeypatch, mock_tencent_ok):
    """批量刷新同样使用正确口径。"""
    import routers.assets as assets_mod

    rows = _as_rows(CJDL_HISTORY_ASC)

    async def fake_fetch(code):
        return rows

    monkeypatch.setattr(assets_mod, "fetch_dividend_from_eastmoney", fake_fetch)

    r = client.post("/api/portfolios/1/assets", json={
        "name": "长江电力", "code": "600900", "quantity": 1000, "price": 29.38,
    })
    asset_id = r.json()["id"]

    r = client.post("/api/portfolios/1/assets/refresh-all-prices")
    assert r.status_code == 200

    r = client.get(f"/api/assets/{asset_id}")
    body = r.json()
    assert body["dividend_per_share"] == pytest.approx(1.0, abs=1e-9)
    assert body["ttm_yield"] == pytest.approx(10.0, abs=0.01)
