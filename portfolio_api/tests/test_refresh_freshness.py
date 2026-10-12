"""刷新端点必须使用「最新」分红数据，而不是命中过期缓存。

背景（事故记录见 docs §13）
    批量/单只刷新原先是「先拉分红 → 再清缓存」。
    缓存是热的时候（最多 6 小时前），拉取会命中旧缓存，
    于是写进 DB 的 ttm_yield 基于过期数据，
    而清缓存后列表展示的 y2024/y2025 又是新拉的 —— 同一页面两套数据。

    修复：改为「先清缓存 → 再拉」。

测试策略
    这里 patch 的是**最底层**的 `_fetch_*_dividend_blocking`（缓存包裹的那一层），
    让真实的 `fetch_dividend_from_eastmoney` 参与运行。
    若换成 patch `fetch_dividend_from_eastmoney` 本身，就绕过了缓存，
    测不出「有没有命中旧缓存」这个行为。

    所有用例都在**新建的空组合**里操作，避免 `init_sample_data` 种下的
    3 只资产（600900/601088/601328）污染调用计数。
"""
from datetime import date, timedelta

import pytest

import routers.assets as assets_mod
import routers.stocks as stocks_mod

TODAY = date.today()

STALE_VALUE = 9.99


# ---------- 数据构造 ----------

def _fresh_rows(per_period=0.5, periods=2):
    """periods 期、每期 per_period 的分红，日期均落在近 12 个月内。TTM = per_period*periods"""
    return [
        {
            "EX_DIVIDEND_DATE": TODAY - timedelta(days=30 * (i + 1)),
            "DIVIDEND_PER_SHARE": per_period,
            "BONUS_IT_RATIO": 0,
            "TRANSFER_RATIO": 0,
            "IMPLEMENT_STATUS": "已实施",
            "REPORT_TIME": "",
        }
        for i in range(periods)
    ]


def _stale_rows(value=STALE_VALUE):
    """一条特征鲜明的「过期」分红，用于识别是否命中了旧缓存。"""
    return [
        {
            "EX_DIVIDEND_DATE": TODAY - timedelta(days=10),
            "DIVIDEND_PER_SHARE": value,
            "BONUS_IT_RATIO": 0,
            "TRANSFER_RATIO": 0,
            "IMPLEMENT_STATUS": "已实施",
            "REPORT_TIME": "",
        }
    ]


# ---------- fixtures / helpers ----------

@pytest.fixture
def network(monkeypatch):
    """把底层拉取换成可控假实现，并记录调用过的代码。"""
    calls = []

    def fake_stock(code):
        calls.append(code)
        return True, _fresh_rows(per_period=0.5, periods=2)   # TTM = 1.0

    def fake_etf(code):
        calls.append(code)
        return True, _fresh_rows(per_period=0.5, periods=2)

    monkeypatch.setattr(stocks_mod, "_fetch_stock_dividend_blocking", fake_stock)
    monkeypatch.setattr(stocks_mod, "_fetch_etf_dividend_blocking", fake_etf)
    return calls


def _prime_stale_cache(code, value=STALE_VALUE):
    """往缓存塞一条**未过期**的旧数据，模拟用户 5 小时前看过列表。"""
    import time as _time
    stocks_mod._dividend_cache[code] = (_time.time() - 5 * 3600, _stale_rows(value))


def _new_portfolio(client, name="隔离测试组合"):
    r = client.post("/api/portfolios", json={"name": name})
    assert r.status_code == 200
    return r.json()["id"]


def _add_asset(client, pid, code, name="测试标的"):
    r = client.post(f"/api/portfolios/{pid}/assets", json={
        "name": name, "code": code, "quantity": 100, "price": 10.0,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---------- 0. 前置校验：证明这个测试确实能捕捉到隐患 ----------

def test_precondition_hot_cache_would_be_used(network):
    """不清缓存时，热缓存确实会被命中并返回旧值。

    这条用例保证后面的断言不是「恰好通过」——先证明隐患真实存在。
    """
    import asyncio
    stocks_mod.invalidate_dividend_cache()
    _prime_stale_cache("600900")

    ttm = asyncio.run(assets_mod.fetch_ttm_dividend_per_share("600900"))

    assert ttm == pytest.approx(STALE_VALUE), "热缓存应被命中"
    assert network == [], "命中缓存时不应发起网络请求"


# ---------- 1. 单只刷新 ----------

def test_single_refresh_bypasses_stale_cache(client, network, mock_tencent_ok):
    """★ 单只刷新必须重新拉分红，而不是用 5 小时前的缓存。"""
    stocks_mod.invalidate_dividend_cache()
    pid = _new_portfolio(client)
    aid = _add_asset(client, pid, "600900", "长江电力")
    _prime_stale_cache("600900")

    r = client.post(f"/api/assets/{aid}/refresh-price")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["price"] == 10.0                       # mock_tencent_ok 固定价
    assert body["dividend_per_share"] == pytest.approx(1.0), "不得使用过期缓存"
    assert body["ttm_yield"] == pytest.approx(10.0)
    assert network == ["600900"], "应恰好发起一次真实拉取"


# ---------- 2. 批量刷新 ----------

def test_bulk_refresh_bypasses_stale_cache(client, network, mock_tencent_ok):
    """★ 批量刷新同样必须重新拉分红。"""
    stocks_mod.invalidate_dividend_cache()
    pid = _new_portfolio(client)
    for code, name in (("600900", "长江电力"), ("600036", "招商银行")):
        _add_asset(client, pid, code, name)
        _prime_stale_cache(code)

    r = client.post(f"/api/portfolios/{pid}/assets/refresh-all-prices")
    assert r.status_code == 200, r.text
    results = {x["code"]: x for x in r.json()["results"]}

    for code in ("600900", "600036"):
        assert results[code]["dividend_per_share"] == pytest.approx(1.0), \
            f"{code} 不得使用过期缓存"
    assert sorted(network) == ["600036", "600900"]


def test_bulk_refresh_updates_db_and_yields(client, network, mock_tencent_ok):
    """批量刷新写进 DB 的 TTM 必须是新值，且与展示的年度分红同源。"""
    stocks_mod.invalidate_dividend_cache()
    pid = _new_portfolio(client)
    _add_asset(client, pid, "600900", "长江电力")
    _prime_stale_cache("600900")

    client.post(f"/api/portfolios/{pid}/assets/refresh-all-prices")

    r = client.get(f"/api/portfolios/{pid}/assets")
    assert r.status_code == 200
    a = r.json()[0]

    # DB 里的 TTM 是新值（不是缓存里的 9.99）
    assert a["dividend_per_share"] == pytest.approx(1.0)
    assert a["ttm_yield"] == pytest.approx(10.0)

    # 年度分红按自然年汇总，期望值由同一份 mock 数据推出（与运行日期无关）
    rows = _fresh_rows()
    for code_year, field in (("2025", "y2025_dividend"), ("2024", "y2024_dividend")):
        expected = sum(
            r["DIVIDEND_PER_SHARE"] for r in rows
            if str(r["EX_DIVIDEND_DATE"])[:4] == code_year
        )
        assert a[field] == pytest.approx(expected), f"{field} 应与同源数据一致"


# ---------- 3. 刷新后的缓存应可复用 ----------

def test_cache_holds_fresh_data_after_refresh(client, network, mock_tencent_ok):
    """刷新后缓存里应是**新数据**，后续请求直接命中，不再联网。"""
    stocks_mod.invalidate_dividend_cache()
    pid = _new_portfolio(client)
    aid = _add_asset(client, pid, "600900", "长江电力")
    _prime_stale_cache("600900")

    client.post(f"/api/assets/{aid}/refresh-price")

    entry = stocks_mod._dividend_cache.get("600900")
    assert entry is not None, "刷新后应缓存新数据"
    rows = entry[1]
    assert sum(r["DIVIDEND_PER_SHARE"] for r in rows) == pytest.approx(1.0)
    assert not any(r["DIVIDEND_PER_SHARE"] == STALE_VALUE for r in rows), \
        "旧数据应已被新数据覆盖"

    calls_before = len(network)
    r = client.get(f"/api/portfolios/{pid}/assets")
    assert r.status_code == 200
    assert len(network) == calls_before, "刷新后的缓存应被复用，不再联网"


# ---------- 4. 单次批量刷新内每只只拉一次 ----------

def test_bulk_refresh_fetches_each_once(client, network, monkeypatch):
    """同一次批量刷新内，每只标的只应拉取一次分红。"""
    stocks_mod.invalidate_dividend_cache()
    pid = _new_portfolio(client)
    for code in ("600900", "600036", "000538"):
        _add_asset(client, pid, code)

    r = client.post(f"/api/portfolios/{pid}/assets/refresh-all-prices")
    assert r.status_code == 200

    assert sorted(network) == ["000538", "600036", "600900"]
    assert len(network) == len(set(network)), "不应重复拉取同一只"


# ---------- 5. 空组合不应报错 ----------

def test_bulk_refresh_empty_portfolio(client, network):
    stocks_mod.invalidate_dividend_cache()
    pid = _new_portfolio(client, "空组合")

    r = client.post(f"/api/portfolios/{pid}/assets/refresh-all-prices")
    assert r.status_code == 200
    assert r.json()["results"] == []
    assert network == []
