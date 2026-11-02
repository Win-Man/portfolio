"""标的搜索：名称归一化 / 缓存残缺兜底 / 缓存刷新不被降级覆盖。

对应两个真实问题：
  ① 搜不到 000858 五粮液 —— 交易所给的数据是「五 粮 液」（短名用空格补齐），
     名称包含匹配失败；同类还有 000002「万  科Ａ」（全角 Ａ）。
  ② 缓存里只剩基金（个股来源当天拉取失败），全市场股票一只都搜不到，
     而且残缺结果被写回文件、当天不再重试。
"""
import os
import shutil
import tempfile

import pandas as pd
import pytest

import routers.stocks as stocks_mod
from routers.stocks import normalize_name, name_key


@pytest.fixture
def cache_path():
    """临时缓存文件路径。

    用 tempfile 而不是 pytest 的 `tmp_path`：本机沙箱里 `tmp_path` 建目录会 EEXIST。
    """
    directory = tempfile.mkdtemp(prefix="stockcache-")
    try:
        yield os.path.join(directory, "stock_cache.json")
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _frame(rows):
    """构造缓存表（故意不带 name_key，验证搜索侧也会兜底补列）。"""
    return pd.DataFrame(rows)


def _use_cache(monkeypatch, rows):
    monkeypatch.setattr(stocks_mod, "_stock_cache", _frame(rows))
    monkeypatch.setattr(stocks_mod, "_cache_date", stocks_mod._get_today_date())
    monkeypatch.setattr(stocks_mod, "_cache_complete", True)
    monkeypatch.setattr(stocks_mod, "_next_refresh_retry", 0.0)


# ---------- 1. 名称归一化（纯函数） ----------

def test_normalize_name_strips_padding_and_fullwidth():
    assert normalize_name("五 粮 液") == "五粮液"
    assert normalize_name("万  科Ａ") == "万科A"          # 全角Ａ → 半角A
    assert normalize_name("  贵州茅台 ") == "贵州茅台"
    assert normalize_name("华夏\u3000上证50ETF") == "华夏上证50ETF"   # 全角空格
    assert normalize_name(None) == ""
    # 搜索键统一大写，用户输入 '万科a' / '万科A' 等价
    assert name_key("万  科Ａ") == name_key("万科a") == "万科A"


# ---------- 2. 带排版空格的名称能搜到 ----------

@pytest.mark.parametrize(
    "keyword,expected_code",
    [
        ("000858", "000858"),
        ("五粮液", "000858"),
        ("五 粮 液", "000858"),
        ("万科A", "000002"),
        ("万科a", "000002"),
        ("0002", "000002"),
    ],
)
def test_search_matches_padded_names(client, monkeypatch, keyword, expected_code):
    """名称里的空格/全角不该让用户搜不到（'五粮液' 命中 '五 粮 液'）。"""
    _use_cache(monkeypatch, [
        {"code": "000858", "name": "五 粮 液", "asset_type": "股票"},
        {"code": "000002", "name": "万  科Ａ", "asset_type": "股票"},
        {"code": "510720", "name": "红利国企ETF国泰", "asset_type": "ETF"},
    ])

    body = client.get("/api/stocks/search", params={"keyword": keyword}).json()
    assert expected_code in [item["code"] for item in body], f"{keyword} 没搜到 {expected_code}"


def test_search_returns_normalized_display_name(client, monkeypatch):
    """返回给前端的名称同样归一化（'五 粮 液' → '五粮液'），否则选完还是带空格。"""
    _use_cache(monkeypatch, [{"code": "000858", "name": "五 粮 液", "asset_type": "股票"}])

    body = client.get("/api/stocks/search", params={"keyword": "五粮液"}).json()
    assert body == [
        {"code": "000858", "name": "五粮液", "market": "深市", "type": "股票"}
    ]


# ---------- 3. 缓存里没有 → 6 位代码直接问行情 ----------

def test_search_falls_back_to_quote_for_6_digit_code(client, monkeypatch):
    """列表缓存残缺（只剩基金）时，按代码仍能加上：直接问实时行情拿名称。"""
    _use_cache(monkeypatch, [{"code": "510720", "name": "红利国企ETF国泰", "asset_type": "ETF"}])

    async def fake_quote(code):
        assert code == "000858"
        # 行情接口返回的名称一样带空格
        return {"success": True, "name": "五 粮 液", "price": 74.25}

    monkeypatch.setattr(stocks_mod, "fetch_price_from_tencent", fake_quote)

    body = client.get("/api/stocks/search", params={"keyword": "000858"}).json()
    assert body == [
        {"code": "000858", "name": "五粮液", "market": "深市", "type": "股票"}
    ]


def test_search_fallback_only_for_6_digit_codes(client, monkeypatch):
    """名称搜不到时不该去问行情（否则每次都白打一次网络请求）。"""
    _use_cache(monkeypatch, [])

    called = {"n": 0}

    async def fake_quote(code):  # pragma: no cover - 不应被调用
        called["n"] += 1
        return {"success": True, "name": "不该出现", "price": 1.0}

    monkeypatch.setattr(stocks_mod, "fetch_price_from_tencent", fake_quote)

    assert client.get("/api/stocks/search", params={"keyword": "五粮液"}).json() == []
    assert called["n"] == 0


def test_search_fallback_returns_empty_for_unknown_code(client, monkeypatch):
    """行情也拿不到（代码不存在 / 停牌退市）→ 空数组，不抛错。"""
    _use_cache(monkeypatch, [])

    async def fake_quote(code):
        return {"success": False, "error": "none_match"}

    monkeypatch.setattr(stocks_mod, "fetch_price_from_tencent", fake_quote)

    assert client.get("/api/stocks/search", params={"keyword": "999999"}).json() == []


# ---------- 4. 刷新失败不能被「只剩基金」覆盖 ----------

def _fund_frame():
    return pd.DataFrame({
        "代码": ["sh510720", "sz159998"],
        "名称": ["红利国企ETF国泰", "计算机ETF天弘"],
    })


def test_refresh_keeps_previous_stocks_when_stock_source_fails(client, monkeypatch, cache_path):
    """个股来源挂掉时沿用上次的个股行，绝不能把缓存洗成「只有基金」。"""
    monkeypatch.setattr(stocks_mod, "STOCK_CACHE_FILE", cache_path)
    monkeypatch.setattr(stocks_mod, "_fetch_stock_rows", lambda: None)
    monkeypatch.setattr(
        stocks_mod.ak, "fund_etf_category_sina", lambda symbol: _fund_frame()
    )
    monkeypatch.setattr(
        stocks_mod, "_stock_cache",
        _frame([{"code": "000858", "name": "五 粮 液", "asset_type": "股票"}]),
    )
    monkeypatch.setattr(stocks_mod, "_cache_complete", True)
    monkeypatch.setattr(stocks_mod, "_next_refresh_retry", 0.0)

    assert stocks_mod._refresh_stock_cache() is True

    codes = set(stocks_mod._stock_cache["code"])
    assert "000858" in codes, "个股来源失败后个体股被丢掉了"
    assert "510720" in codes, "基金仍然要写进缓存"
    # 不完整 → 标记出来，退避窗口后重试（否则一整天都只有基金）
    assert stocks_mod._cache_complete is False
    assert stocks_mod._next_refresh_retry > 0


def test_refresh_marks_stock_source_ok_again(client, monkeypatch, cache_path):
    """个股来源恢复后，完整性标记要回到 True 并清掉重试窗口。"""
    monkeypatch.setattr(stocks_mod, "STOCK_CACHE_FILE", cache_path)
    monkeypatch.setattr(
        stocks_mod, "_fetch_stock_rows",
        lambda: pd.DataFrame({"code": ["000858"], "name": ["五 粮 液"]}),
    )
    monkeypatch.setattr(
        stocks_mod.ak, "fund_etf_category_sina", lambda symbol: _fund_frame()
    )
    monkeypatch.setattr(stocks_mod, "_cache_complete", False)
    monkeypatch.setattr(stocks_mod, "_next_refresh_retry", 9_999_999_999.0)

    assert stocks_mod._refresh_stock_cache() is True
    assert stocks_mod._cache_complete is True
    assert stocks_mod._next_refresh_retry == 0.0
    assert "000858" in set(stocks_mod._stock_cache["code"])
    assert stocks_mod._stock_cache.loc[
        stocks_mod._stock_cache["code"] == "000858", "name"
    ].iloc[0] == "五粮液"


def test_load_marks_funds_only_cache_file_as_incomplete(client, monkeypatch, cache_path):
    """文件里只有基金（历史遗留的残缺缓存）→ 加载时就标记不完整，触发重新拉取。"""
    path = cache_path
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            '{"cache_time": 1, "cache_date": "2026-01-01", '
            '"stocks": [{"code": "510720", "name": "红利国企ETF国泰", "asset_type": "ETF"}]}'
        )
    monkeypatch.setattr(stocks_mod, "STOCK_CACHE_FILE", str(path))
    monkeypatch.setattr(stocks_mod, "_stock_cache", None)

    assert stocks_mod._load_cache_from_file() is True
    assert stocks_mod._cache_complete is False
