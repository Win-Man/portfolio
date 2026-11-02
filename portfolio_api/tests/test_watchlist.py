"""观察池（watchlist）后端用例 —— 对应 spec 003 §10.1 的必测清单。

约定：
- 测试期间定时任务已关闭（conftest 设 `WATCHLIST_SNAPSHOT_ENABLED=false`），
  不会有后台协程，也不会发出真实网络请求；
- 抓取函数全部 monkeypatch（`routers.watchlist.fetch_real_time_price` /
  `fetch_ttm_dividend_per_share`），快照数据直接写库，避免用例依赖外部数据源；
- 快照日期一律相对「今天（Asia/Shanghai）」构造，用例不随系统日期腐化。
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from database import SessionLocal
from models import WatchlistSnapshot
from watchlist_math import (
    current_yield,
    derive_metrics,
    price_gap_percent,
    price_signal,
    staleness,
    yield_signal,
    yield_trigger_price,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def today() -> "datetime.date":
    """观察池口径的「今天」（与后端 _refresh_one / is_stale 完全一致）。"""
    return datetime.now(SHANGHAI).date()


# ---------- 测试工具 ----------

def create_item(client, **overrides):
    """通过 API 建一条观察项，返回响应体。"""
    payload = {"code": "600900", "name": "长江电力", "target_yield": 5.0}
    payload.update(overrides)
    res = client.post("/api/watchlist", json=payload)
    assert res.status_code == 201, res.text
    return res.json()


def add_snapshot(item_id, trade_date, price, ttm_dividend, price_source="real"):
    """直接写一条快照（绕过外部数据源）。ttm_yield 按口径现算，与 _refresh_one 一致。"""
    ttm_yield = (
        ttm_dividend / price * 100 if (ttm_dividend > 0 and price > 0) else None
    )
    db = SessionLocal()
    try:
        db.add(
            WatchlistSnapshot(
                item_id=item_id,
                trade_date=trade_date,
                price=price,
                ttm_dividend=ttm_dividend,
                ttm_yield=ttm_yield,
                price_source=price_source,
            )
        )
        db.commit()
    finally:
        db.close()


def count_snapshots(item_id):
    db = SessionLocal()
    try:
        return (
            db.query(WatchlistSnapshot)
            .filter(WatchlistSnapshot.item_id == item_id)
            .count()
        )
    finally:
        db.close()


def snapshot_rows(item_id):
    """按 trade_date 升序返回 (date, price, ttm_dividend, ttm_yield) 列表。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(WatchlistSnapshot)
            .filter(WatchlistSnapshot.item_id == item_id)
            .order_by(WatchlistSnapshot.trade_date.asc())
            .all()
        )
        return [(r.trade_date, r.price, r.ttm_dividend, r.ttm_yield) for r in rows]
    finally:
        db.close()


def get_one(client, item_id):
    res = client.get("/api/watchlist")
    assert res.status_code == 200, res.text
    for row in res.json():
        if row["id"] == item_id:
            return row
    raise AssertionError(f"watchlist item {item_id} not found in list")


class FakeMarket:
    """可编程的行情/分红替身：记录调用次数，供「GET 不得抓取」等断言使用。"""

    def __init__(self):
        self.price = 10.0
        self.source = "real"
        self.ttm = 0.5
        self.price_calls = 0
        self.ttm_calls = 0

    @property
    def total_calls(self) -> int:
        return self.price_calls + self.ttm_calls


@pytest.fixture
def fake_market(monkeypatch):
    """把观察池路由里的两个抓取函数替换成替身（模块级别名，需 patch routers.watchlist）。"""
    fake = FakeMarket()

    async def fake_price(code, current_price=0, dividend_per_share=0):
        fake.price_calls += 1
        if fake.source != "real":
            return {
                "price": 10.0,
                "name": code,
                "source": "mock",
                "success": True,
                "mock": True,
            }
        return {
            "price": fake.price,
            "name": code,
            "source": "real",
            "success": True,
            "yesterday_close": fake.price,
        }

    async def fake_ttm(code):
        fake.ttm_calls += 1
        return fake.ttm

    import routers.watchlist as watchlist_router

    monkeypatch.setattr(watchlist_router, "fetch_real_time_price", fake_price)
    monkeypatch.setattr(watchlist_router, "fetch_ttm_dividend_per_share", fake_ttm)
    return fake


# ---------- 新增 ----------

def test_create_normalizes_code_and_infers_asset_type(client):
    """新增成功：sh 前缀被剥掉，5 开头的基金代码推断为 ETF。"""
    item = create_item(client, code="sh600900", name="长江电力", target_yield=5.5)
    assert item["code"] == "600900"
    assert item["asset_type"] == "股票"
    assert item["target_yield"] == 5.5

    etf = create_item(client, code="sh561580", name="央企红利ETF", target_yield=6.0)
    assert etf["code"] == "561580"
    assert etf["asset_type"] == "ETF"


def test_create_duplicate_code_returns_409(client):
    create_item(client, code="601398")
    res = client.post(
        "/api/watchlist", json={"code": "601398", "name": "工商银行", "target_yield": 4.0}
    )
    assert res.status_code == 409
    assert res.json()["detail"] == "该标的已在观察池中"


def test_create_duplicate_after_normalize_returns_409(client):
    """sh600900 与 600900 是同一个标的，判重按归一化后的值。"""
    create_item(client, code="sh600900")
    res = client.post(
        "/api/watchlist", json={"code": "600900", "name": "长江电力", "target_yield": 5.0}
    )
    assert res.status_code == 409


def test_create_target_yield_zero_returns_422(client):
    res = client.post(
        "/api/watchlist", json={"code": "601398", "name": "工商银行", "target_yield": 0}
    )
    assert res.status_code == 422


def test_create_target_yield_too_high_returns_422(client):
    res = client.post(
        "/api/watchlist", json={"code": "601398", "name": "工商银行", "target_yield": 31}
    )
    assert res.status_code == 422


def test_create_negative_target_price_returns_422(client):
    res = client.post(
        "/api/watchlist",
        json={"code": "601398", "name": "工商银行", "target_yield": 5, "target_price": -1},
    )
    assert res.status_code == 422


def test_create_blank_name_returns_422(client):
    res = client.post(
        "/api/watchlist", json={"code": "601398", "name": "   ", "target_yield": 5}
    )
    assert res.status_code == 422


def test_create_invalid_code_returns_422(client):
    for bad in ("60090", "6009001", "ABCDEF"):
        res = client.post(
            "/api/watchlist", json={"code": bad, "name": "XX", "target_yield": 5}
        )
        assert res.status_code == 422, f"{bad} 应被拒"


# ---------- 更新 ----------

def test_partial_update_only_touches_given_field(client):
    item = create_item(
        client, code="601398", name="工商银行", target_yield=5.0, target_price=6.5, note="观察"
    )
    res = client.put(f"/api/watchlist/{item['id']}", json={"target_yield": 6.5})
    assert res.status_code == 200
    body = res.json()
    assert body["target_yield"] == 6.5
    # 未传的字段保持不变
    assert body["name"] == "工商银行"
    assert body["target_price"] == 6.5
    assert body["note"] == "观察"


def test_update_target_price_zero_clears_and_price_signal_not_set(client):
    item = create_item(client, code="601398", target_price=6.5)
    add_snapshot(item["id"], today(), price=7.0, ttm_dividend=0.35)

    before = get_one(client, item["id"])
    assert before["price_signal"] in ("near", "watch")

    res = client.put(f"/api/watchlist/{item['id']}", json={"target_price": 0})
    assert res.status_code == 200
    body = res.json()
    assert body["target_price"] is None
    assert body["price_signal"] == "not_set"
    assert body["price_gap_percent"] is None


# ---------- 删除 ----------

def test_delete_cascades_snapshots(client):
    item = create_item(client, code="601398")
    add_snapshot(item["id"], today(), price=10.0, ttm_dividend=0.5)
    add_snapshot(item["id"], today() - timedelta(days=1), price=9.9, ttm_dividend=0.5)
    assert count_snapshots(item["id"]) == 2

    res = client.delete(f"/api/watchlist/{item['id']}")
    assert res.status_code == 200
    assert res.json() == {"message": "Watchlist item deleted"}
    # 级联删除其快照
    assert count_snapshots(item["id"]) == 0
    # 组合与资产不受影响
    assert client.get("/api/portfolios").status_code == 200
    assert len(client.get("/api/portfolios").json()) >= 1


def test_delete_missing_returns_404(client):
    res = client.delete("/api/watchlist/999999")
    assert res.status_code == 404
    assert res.json()["detail"] == "Watchlist item not found"


# ---------- 列表不抓取 / 派生字段 ----------

def test_get_watchlist_never_calls_external_source(client, fake_market):
    create_item(client, code="601398")
    res = client.get("/api/watchlist")
    assert res.status_code == 200
    assert fake_market.total_calls == 0

    # 新增同样不自动抓价
    create_item(client, code="601288")
    assert fake_market.total_calls == 0


def test_new_item_without_snapshot_has_null_derived_fields(client):
    item = create_item(client, code="601398", target_yield=5.0)
    assert item["price"] is None
    assert item["current_yield"] is None
    assert item["yield_trigger_price"] is None
    assert item["ttm_dividend"] == 0
    assert item["snapshot_date"] is None
    assert item["yield_signal"] == "unknown"
    # 未设目标价 → not_set
    assert item["price_signal"] == "not_set"
    assert item["stale_days"] is None
    assert item["is_stale"] is False
    assert item["refresh_error"] is None

    # 设了目标价但还没有价格 → unknown
    with_price = create_item(client, code="601288", target_price=4.0)
    assert with_price["price_signal"] == "unknown"


def test_zero_ttm_dividend_gives_null_trigger_price(client):
    item = create_item(client, code="601398", target_yield=5.0)
    add_snapshot(item["id"], today(), price=10.0, ttm_dividend=0.0)
    row = get_one(client, item["id"])
    assert row["ttm_dividend"] == 0
    assert row["yield_trigger_price"] is None      # 不是 0.00
    assert row["yield_signal"] == "unknown"
    assert row["current_yield"] is None


# ---------- 两条信号（互不合并） ----------

def test_yield_signal_hit(client):
    item = create_item(client, code="601398", target_yield=5.0)
    add_snapshot(item["id"], today(), price=10.0, ttm_dividend=0.5)  # 股息率 5.00%
    row = get_one(client, item["id"])
    assert row["current_yield"] == 5.0
    assert row["yield_signal"] == "hit"
    assert row["yield_gap_points"] == 0.0


def test_yield_signal_near(client):
    item = create_item(client, code="601398", target_yield=5.0)
    add_snapshot(item["id"], today(), price=10.0, ttm_dividend=0.49)  # 4.90%，接近但未达标
    row = get_one(client, item["id"])
    assert row["yield_signal"] == "near"


def test_yield_signal_watch(client):
    item = create_item(client, code="601398", target_yield=5.0)
    add_snapshot(item["id"], today(), price=10.0, ttm_dividend=0.4)  # 4.00%
    row = get_one(client, item["id"])
    assert row["yield_signal"] == "watch"
    assert row["yield_gap_points"] == -1.0


def test_price_signal_hit_near_watch(client):
    hit = create_item(client, code="601398", target_price=11.0)
    add_snapshot(hit["id"], today(), price=10.0, ttm_dividend=0.5)
    assert get_one(client, hit["id"])["price_signal"] == "hit"

    near = create_item(client, code="601288", target_price=10.0)
    add_snapshot(near["id"], today(), price=10.2, ttm_dividend=0.5)  # 高出 2%
    assert get_one(client, near["id"])["price_signal"] == "near"

    watch = create_item(client, code="601988", target_price=10.0)
    add_snapshot(watch["id"], today(), price=11.0, ttm_dividend=0.5)  # 高出 10%
    assert get_one(client, watch["id"])["price_signal"] == "watch"


def test_signals_are_independent(client):
    """股息率达标而价格未达标（两条线各自判定，绝不合并）。"""
    item = create_item(client, code="601398", target_yield=5.0, target_price=5.0)
    add_snapshot(item["id"], today(), price=10.0, ttm_dividend=0.6)  # 股息率 6%，价格远高于目标
    row = get_one(client, item["id"])
    assert row["yield_signal"] == "hit"
    assert row["price_signal"] == "watch"


# ---------- 已持有 / 陈旧 ----------

def test_is_held_across_portfolios(client):
    # 种子数据里 600900 已在「投资组合」中持有
    item = create_item(client, code="600900")
    assert item["is_held"] is True
    assert item["held_portfolios"] == ["投资组合"]

    # 再加一个组合同样持有 → 两个组合名，按组合 id 升序
    p = client.post("/api/portfolios", json={"name": "第二个组合"}).json()
    client.post(
        f"/api/portfolios/{p['id']}/assets",
        json={"name": "长江电力", "code": "600900", "quantity": 100, "price": 28.5},
    )
    row = get_one(client, item["id"])
    assert row["is_held"] is True
    assert row["held_portfolios"] == ["投资组合", "第二个组合"]
    # 该行仍保留在列表里（不删除不隐藏）
    assert any(x["id"] == item["id"] for x in client.get("/api/watchlist").json())


def test_is_stale_boundary(client):
    stale = create_item(client, code="601398")
    add_snapshot(stale["id"], today() - timedelta(days=6), price=10.0, ttm_dividend=0.5)
    row = get_one(client, stale["id"])
    assert row["is_stale"] is True
    assert row["stale_days"] == 6

    fresh = create_item(client, code="601288")
    add_snapshot(fresh["id"], today(), price=10.0, ttm_dividend=0.5)
    row = get_one(client, fresh["id"])
    assert row["is_stale"] is False
    assert row["stale_days"] == 0


# ---------- 刷新与快照 ----------

def test_refresh_writes_snapshot_and_same_day_is_idempotent(client, fake_market):
    item = create_item(client, code="601398", target_yield=5.0)

    res = client.post(f"/api/watchlist/{item['id']}/refresh")
    assert res.status_code == 200
    body = res.json()
    assert body["refresh_error"] is None
    assert body["price"] == 10.0
    assert body["ttm_dividend"] == 0.5
    assert body["current_yield"] == 5.0
    assert body["snapshot_date"] == today().isoformat()
    assert body["price_source"] == "real"
    assert count_snapshots(item["id"]) == 1

    # 同日再刷：UPSERT 覆盖，不新增行
    res2 = client.post(f"/api/watchlist/{item['id']}/refresh")
    assert res2.status_code == 200
    assert count_snapshots(item["id"]) == 1

    # 刷新全部同样幂等
    assert client.post("/api/watchlist/refresh").status_code == 200
    assert count_snapshots(item["id"]) == 1


def test_refresh_with_mock_price_does_not_write_and_reports_error(client, fake_market):
    item = create_item(client, code="601398")
    fake_market.source = "mock"

    res = client.post(f"/api/watchlist/{item['id']}/refresh")
    assert res.status_code == 200                     # 永不因外部源失败返回 5xx
    body = res.json()
    assert body["refresh_error"] == "行情获取失败，已保留上次数据"
    assert body["price"] is None
    assert count_snapshots(item["id"]) == 0

    # 全量刷新：整体 200，失败项带 refresh_error
    all_rows = client.post("/api/watchlist/refresh")
    assert all_rows.status_code == 200
    assert all_rows.json()[0]["refresh_error"] == "行情获取失败，已保留上次数据"
    assert count_snapshots(item["id"]) == 0


def test_refresh_keeps_previous_ttm_when_source_returns_zero(client, fake_market):
    """D8：本次抓到 0 而历史最新快照 > 0 → 沿用历史值，不写 0。"""
    item = create_item(client, code="601398", target_yield=5.0)
    add_snapshot(
        item["id"], today() - timedelta(days=1), price=12.0, ttm_dividend=0.6
    )

    fake_market.ttm = 0
    fake_market.price = 10.0
    res = client.post(f"/api/watchlist/{item['id']}/refresh")
    assert res.status_code == 200
    assert res.json()["ttm_dividend"] == 0.6
    assert res.json()["current_yield"] == 6.0


def test_refresh_all_continues_when_one_item_raises(client, monkeypatch):
    """单条异常被吞掉：整体仍 200，其余标的照常刷新。"""
    ok_item = create_item(client, code="601398")
    bad_item = create_item(client, code="601288")

    import routers.watchlist as watchlist_router

    async def boom(code):
        if code == "601288":
            raise RuntimeError("数据源挂了")
        return 0.5

    async def fake_price(code, current_price=0, dividend_per_share=0):
        return {"price": 10.0, "name": code, "source": "real", "success": True}

    monkeypatch.setattr(watchlist_router, "fetch_ttm_dividend_per_share", boom)
    monkeypatch.setattr(watchlist_router, "fetch_real_time_price", fake_price)

    res = client.post("/api/watchlist/refresh")
    assert res.status_code == 200
    rows = {row["id"]: row for row in res.json()}
    assert rows[ok_item["id"]]["refresh_error"] is None
    assert rows[bad_item["id"]]["refresh_error"] is not None
    assert count_snapshots(ok_item["id"]) == 1
    assert count_snapshots(bad_item["id"]) == 0


# ---------- 快照序列 ----------

def test_snapshots_filter_by_days_and_sort_ascending(client):
    item = create_item(client, code="601398")
    add_snapshot(item["id"], today() - timedelta(days=40), price=8.0, ttm_dividend=0.4)
    add_snapshot(item["id"], today() - timedelta(days=10), price=9.0, ttm_dividend=0.45)
    add_snapshot(item["id"], today() - timedelta(days=1), price=10.0, ttm_dividend=0.5)

    res = client.get(f"/api/watchlist/{item['id']}/snapshots?days=30")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 2                                  # 40 天前那条被过滤
    dates = [row["trade_date"] for row in rows]
    assert dates == sorted(dates)
    assert rows[-1]["price"] == 10.0

    all_rows = client.get(f"/api/watchlist/{item['id']}/snapshots?days=90").json()
    assert len(all_rows) == 3


def test_snapshots_days_out_of_range_returns_422(client):
    item = create_item(client, code="601398")
    assert client.get(f"/api/watchlist/{item['id']}/snapshots?days=0").status_code == 422
    assert client.get(f"/api/watchlist/{item['id']}/snapshots?days=10000").status_code == 422


def test_snapshots_missing_item_returns_404(client):
    assert client.get("/api/watchlist/999999/snapshots?days=30").status_code == 404


# ---------- 历史回填（新标的首次刷新补历史，图表才有得看） ----------

class FakeKline:
    """日K替身：bar_days 是「相对今天往前多少天」的交易日列表。"""

    def __init__(self, bar_days):
        self.bar_days = bar_days
        self.calls = 0

    async def __call__(self, code, days=365, **kwargs):
        self.calls += 1
        base = kwargs.get("today") or today()
        # 收盘价随「离今天的天数」变化，便于断言某个日期取到了对应价
        return [(base - timedelta(days=n), 10.0 + n / 100) for n in self.bar_days]


@pytest.fixture
def backfill_on(monkeypatch):
    """打开历史回填，并把日K与分红都换成替身（不允许用例发真实请求）。"""
    import config as config_mod
    import watchlist_history

    monkeypatch.setattr(config_mod.settings, "watchlist_backfill_enabled", True)

    async def fake_dividend(code):
        # 两期：30 天前（0.5）与 400 天前（0.4）——
        # 对不同历史日期，「滚动 12 个月」窗口命中的期数不同，可验证按日回放
        return [
            {
                "EX_DIVIDEND_DATE": today() - timedelta(days=30),
                "DIVIDEND_PER_SHARE": 0.5,
                "IMPLEMENT_STATUS": "已实施",
            },
            {
                "EX_DIVIDEND_DATE": today() - timedelta(days=400),
                "DIVIDEND_PER_SHARE": 0.4,
                "IMPLEMENT_STATUS": "已实施",
            },
        ]

    monkeypatch.setattr(watchlist_history, "fetch_dividend_from_eastmoney", fake_dividend)
    return monkeypatch


def test_first_refresh_backfills_history(client, fake_market, backfill_on):
    """首次刷新：当天写实时价，同时把历史交易日补齐（价格取当日收盘、TTM 取当日滚动 12 个月）。"""
    item = create_item(client, code="601398", target_yield=5.0)

    kline = FakeKline([10, 100, 5])
    backfill_on.setattr("watchlist_history.fetch_daily_closes", kline)

    res = client.post(f"/api/watchlist/{item['id']}/refresh")
    assert res.status_code == 200
    assert res.json()["price"] == 10.0          # 当天用实时价

    rows = snapshot_rows(item["id"])
    assert len(rows) == 4                        # 3 条历史 + 今天
    by_date = {r[0]: r for r in rows}

    # 今天：实时价 10.0（不是日K收盘价），且不会被回填覆盖
    assert by_date[today()][1] == 10.0
    assert by_date[today()][2] == 0.5

    # 10 天前：TTM 窗口命中 30 天前那一期 0.5
    d10 = today() - timedelta(days=10)
    assert by_date[d10][1] == pytest.approx(10.1)
    assert by_date[d10][2] == pytest.approx(0.5)
    assert by_date[d10][3] == pytest.approx(0.5 / 10.1 * 100)

    # 100 天前：窗口落在 30 天前那期之前 → 命中 400 天前那期 0.4
    d100 = today() - timedelta(days=100)
    assert by_date[d100][2] == pytest.approx(0.4)

    # 升序 & 只有一条今天的行
    assert [r[0] for r in rows] == sorted(r[0] for r in rows)
    assert sum(1 for r in rows if r[0] == today()) == 1


def test_backfill_skipped_when_history_already_reaches_listing(client, fake_market, backfill_on):
    """已补到上市日（进程内标记命中）→ 不再白跑日K，日常由定时任务逐日累加。"""
    item = create_item(client, code="601398")
    oldest = today() - timedelta(days=3000)
    add_snapshot(item["id"], oldest, price=9.5, ttm_dividend=0.45)

    # 模拟「这个进程里已经确认该标的的历史从头拉到过上市日」：
    # 已知最早的那根日K 与库里最早的一条同一天 → 没有缺口
    import watchlist_history

    watchlist_history._EARLIEST_BAR["601398"] = oldest

    kline = FakeKline([10, 5])
    backfill_on.setattr("watchlist_history.fetch_daily_closes", kline)

    client.post(f"/api/watchlist/{item['id']}/refresh")
    assert kline.calls == 0
    assert count_snapshots(item["id"]) == 2      # 原有 1 条 + 今天 1 条


def test_backfill_extends_existing_history_without_overwriting(client, fake_market, backfill_on):
    """已有快照也继续向前补齐（历史越长越好），但已存在的日期绝不被覆盖。"""
    item = create_item(client, code="601398")
    d3 = today() - timedelta(days=3)
    add_snapshot(item["id"], d3, price=9.5, ttm_dividend=0.45)

    kline = FakeKline([100, 50, 3])
    backfill_on.setattr("watchlist_history.fetch_daily_closes", kline)

    client.post(f"/api/watchlist/{item['id']}/refresh")
    assert kline.calls == 1

    rows = {r[0]: r for r in snapshot_rows(item["id"])}
    assert set(rows) == {
        today() - timedelta(days=100),
        today() - timedelta(days=50),
        d3,
        today(),
    }
    # 原有那条的收盘价/分红不被日K结果改写
    assert rows[d3][1] == 9.5
    assert rows[d3][2] == 0.45


def test_backfill_disabled_by_config(client, fake_market):
    """默认（测试环境）关闭回填：只写当天一条，不发日K请求。"""
    item = create_item(client, code="601398")
    res = client.post(f"/api/watchlist/{item['id']}/refresh")
    assert res.status_code == 200
    assert count_snapshots(item["id"]) == 1


def test_backfill_failure_does_not_break_refresh(client, fake_market, backfill_on):
    """日K 拉不到（返回空）→ 刷新照常成功，只是没有历史。"""
    item = create_item(client, code="601398")

    async def empty(code, days=365, **kwargs):
        return []

    backfill_on.setattr("watchlist_history.fetch_daily_closes", empty)
    res = client.post(f"/api/watchlist/{item['id']}/refresh")
    assert res.status_code == 200
    assert res.json()["refresh_error"] is None
    assert count_snapshots(item["id"]) == 1


def test_build_history_rows_pure_function():
    """纯函数：无分红 → 股息率 null（不是 0）；有分红 → 按当日现算。"""
    from watchlist_history import build_history_rows

    d1 = today() - timedelta(days=10)
    d2 = today() - timedelta(days=20)

    rows = build_history_rows([(d1, 10.0)], [])
    assert rows[0]["ttm_dividend"] == 0.0
    assert rows[0]["ttm_yield"] is None          # null 表示「算不出」，不是 0
    assert rows[0]["price"] == 10.0
    assert rows[0]["price_source"] == "real"

    records = [
        {
            "EX_DIVIDEND_DATE": today() - timedelta(days=15),
            "DIVIDEND_PER_SHARE": 0.6,
            "IMPLEMENT_STATUS": "已实施",
        }
    ]
    rows = build_history_rows([(d1, 10.0), (d2, 12.0)], records)
    assert rows[0]["ttm_dividend"] == pytest.approx(0.6)   # d1 窗口内命中
    assert rows[1]["ttm_dividend"] == 0.0                  # d2 早于除权日 → 窗口内没有
    assert rows[1]["ttm_yield"] is None

    assert build_history_rows([], records) == []


# ---------- 纯函数：null 传播与边界 ----------

def test_math_null_propagation_never_raises():
    metrics = derive_metrics(
        price=None, ttm_dividend=0, target_yield=5.0, target_price=10.0
    )
    assert metrics["current_yield"] is None
    assert metrics["yield_trigger_price"] is None
    assert metrics["yield_gap_points"] is None
    assert metrics["yield_trigger_gap_percent"] is None
    assert metrics["price_gap_percent"] is None
    assert metrics["yield_signal"] == "unknown"
    assert metrics["price_signal"] == "unknown"

    # 未设目标价 → not_set（优先于 unknown）
    assert price_signal(None, None) == "not_set"
    assert price_gap_percent(None, 10.0) is None
    assert current_yield(None, 10.0) is None
    assert current_yield(0.5, 0) is None
    assert yield_trigger_price(0, 5.0) is None
    assert staleness(None, today()) == (False, None)


def test_math_signal_boundaries():
    """near 的两条阈值方向相反：股息率 ×0.97、价格 ×1.03。"""
    assert yield_signal(5.0, 5.0) == "hit"
    assert yield_signal(4.85, 5.0) == "near"      # == target × 0.97
    assert yield_signal(4.84, 5.0) == "watch"

    assert price_signal(10.0, 10.0) == "hit"
    assert price_signal(10.3, 10.0) == "near"     # == target × 1.03
    assert price_signal(10.31, 10.0) == "watch"
    assert price_signal(9.0, 10.0) == "hit"


# ---------- 除权日竖线：纯函数 + 端点 ----------

def test_extract_ex_dividends_filters_status_amount_and_window():
    """只留「已实施 + 金额 > 0 + 窗口内」，按除权日升序。"""
    from datetime import date

    from watchlist_history import extract_ex_dividends

    ref = date(2026, 11, 1)
    records = [
        {"EX_DIVIDEND_DATE": "2026-07-10", "DIVIDEND_PER_SHARE": 0.5, "IMPLEMENT_STATUS": "已实施"},
        {"EX_DIVIDEND_DATE": "2026-01-05", "DIVIDEND_PER_SHARE": 0.3, "IMPLEMENT_STATUS": "实施"},
        # 窗口外（> 365 天）
        {"EX_DIVIDEND_DATE": "2024-01-05", "DIVIDEND_PER_SHARE": 0.4, "IMPLEMENT_STATUS": "已实施"},
        # 未实施（预案）：还没除权，不能当作竖线
        {"EX_DIVIDEND_DATE": "2026-09-01", "DIVIDEND_PER_SHARE": 0.2, "IMPLEMENT_STATUS": "预案"},
        # 金额 0：画出来也解释不了台阶
        {"EX_DIVIDEND_DATE": "2026-08-01", "DIVIDEND_PER_SHARE": 0, "IMPLEMENT_STATUS": "已实施"},
        # 未来日期（已公告未除权）
        {"EX_DIVIDEND_DATE": "2026-12-20", "DIVIDEND_PER_SHARE": 0.6, "IMPLEMENT_STATUS": "已实施"},
    ]

    rows = extract_ex_dividends(records, days=365, today=ref)
    assert [r["ex_date"].isoformat() for r in rows] == ["2026-01-05", "2026-07-10"]
    assert [r["dividend_per_share"] for r in rows] == [0.3, 0.5]

    # 窗口放宽到 3 年 → 2024 那期进来，仍按升序
    wide = extract_ex_dividends(records, days=1095, today=ref)
    assert [r["ex_date"].isoformat() for r in wide] == ["2024-01-05", "2026-01-05", "2026-07-10"]

    # 空入参不抛错
    assert extract_ex_dividends([], days=365, today=ref) == []
    assert extract_ex_dividends(None, days=365, today=ref) == []


def test_dividends_endpoint_returns_sorted_rows(client, monkeypatch):
    """GET /api/watchlist/{id}/dividends 透出除权日，且按 ex_date 升序。"""
    item = create_item(client, code="601328", name="交通银行", target_yield=5.0)

    async def fake_dividend(code):
        assert code == "601328"
        return [
            {"EX_DIVIDEND_DATE": today() - timedelta(days=10), "DIVIDEND_PER_SHARE": 0.35,
             "IMPLEMENT_STATUS": "已实施"},
            {"EX_DIVIDEND_DATE": today() - timedelta(days=200), "DIVIDEND_PER_SHARE": 0.30,
             "IMPLEMENT_STATUS": "已实施"},
            {"EX_DIVIDEND_DATE": today() - timedelta(days=5), "DIVIDEND_PER_SHARE": 0.10,
             "IMPLEMENT_STATUS": "预案"},
        ]

    monkeypatch.setattr("routers.watchlist.fetch_dividend_from_eastmoney", fake_dividend)

    res = client.get(f"/api/watchlist/{item['id']}/dividends?days=365")
    assert res.status_code == 200, res.text
    body = res.json()
    assert [row["dividend_per_share"] for row in body] == [0.30, 0.35]
    assert body[0]["ex_date"] < body[1]["ex_date"]        # 升序
    assert set(body[0].keys()) == {"ex_date", "dividend_per_share"}


def test_dividends_endpoint_days_window_and_validation(client, monkeypatch):
    """days 越小窗口越窄；days 越界 → 422。"""
    item = create_item(client, code="601398", name="工商银行", target_yield=5.0)

    async def fake_dividend(code):
        return [
            {"EX_DIVIDEND_DATE": today() - timedelta(days=10), "DIVIDEND_PER_SHARE": 0.35,
             "IMPLEMENT_STATUS": "已实施"},
            {"EX_DIVIDEND_DATE": today() - timedelta(days=200), "DIVIDEND_PER_SHARE": 0.30,
             "IMPLEMENT_STATUS": "已实施"},
        ]

    monkeypatch.setattr("routers.watchlist.fetch_dividend_from_eastmoney", fake_dividend)

    assert len(client.get(f"/api/watchlist/{item['id']}/dividends?days=30").json()) == 1
    assert len(client.get(f"/api/watchlist/{item['id']}/dividends?days=365").json()) == 2

    assert client.get(f"/api/watchlist/{item['id']}/dividends?days=0").status_code == 422
    assert client.get(f"/api/watchlist/{item['id']}/dividends?days=731").status_code == 422


def test_dividends_endpoint_swallows_source_failure(client, monkeypatch):
    """外部数据源挂了 → 200 + []（图照画，只是没有竖线），绝不 5xx。"""
    item = create_item(client, code="600519", name="贵州茅台", target_yield=3.0)

    async def boom(code):
        raise RuntimeError("eastmoney down")

    monkeypatch.setattr("routers.watchlist.fetch_dividend_from_eastmoney", boom)

    res = client.get(f"/api/watchlist/{item['id']}/dividends?days=365")
    assert res.status_code == 200
    assert res.json() == []


def test_dividends_endpoint_missing_item_404(client):
    res = client.get("/api/watchlist/999999/dividends?days=365")
    assert res.status_code == 404
    assert res.json()["detail"] == "Watchlist item not found"


def test_dividends_endpoint_does_not_touch_snapshots(client, monkeypatch):
    """除权日只读外部事实、不写库：调完后快照数量不变。"""
    item = create_item(client, code="600028", name="中国石化", target_yield=6.0)
    add_snapshot(item["id"], today(), 6.5, 0.4)
    before = count_snapshots(item["id"])

    async def fake_dividend(code):
        return [
            {"EX_DIVIDEND_DATE": today() - timedelta(days=3), "DIVIDEND_PER_SHARE": 0.4,
             "IMPLEMENT_STATUS": "已实施"},
        ]

    monkeypatch.setattr("routers.watchlist.fetch_dividend_from_eastmoney", fake_dividend)

    res = client.get(f"/api/watchlist/{item['id']}/dividends?days=365")
    assert res.status_code == 200
    assert len(res.json()) == 1
    assert count_snapshots(item["id"]) == before


# ---------- 区间查询（走势图「全部」/ 自定义起止区间） ----------

def test_snapshots_custom_range_start_end(client):
    """start/end 为闭区间，任一端可省略；与 days 互斥（给了 start/end 就忽略 days）。"""
    item = create_item(client, code="601398")
    d = lambda n: today() - timedelta(days=n)  # noqa: E731
    for n, price in ((300, 7.0), (200, 8.0), (100, 9.0), (10, 10.0)):
        add_snapshot(item["id"], d(n), price=price, ttm_dividend=0.4)

    rows = client.get(
        f"/api/watchlist/{item['id']}/snapshots",
        params={"start": d(250).isoformat(), "end": d(50).isoformat()},
    ).json()
    assert [r["price"] for r in rows] == [8.0, 9.0]

    # 只给 start → 到现在
    only_start = client.get(
        f"/api/watchlist/{item['id']}/snapshots", params={"start": d(150).isoformat()}
    ).json()
    assert [r["price"] for r in only_start] == [9.0, 10.0]

    # 只给 end → 从最早
    only_end = client.get(
        f"/api/watchlist/{item['id']}/snapshots", params={"end": d(150).isoformat()}
    ).json()
    assert [r["price"] for r in only_end] == [7.0, 8.0]


def test_snapshots_all_returns_full_history(client):
    """?all=true → 不做任何日期过滤（上市日至今）。"""
    item = create_item(client, code="601398")
    add_snapshot(item["id"], today() - timedelta(days=2000), price=5.0, ttm_dividend=0.2)
    add_snapshot(item["id"], today(), price=10.0, ttm_dividend=0.5)

    assert len(client.get(f"/api/watchlist/{item['id']}/snapshots?days=90").json()) == 1
    rows = client.get(f"/api/watchlist/{item['id']}/snapshots?all=true").json()
    assert [r["price"] for r in rows] == [5.0, 10.0]


def test_snapshots_start_after_end_returns_422(client):
    item = create_item(client, code="601398")
    res = client.get(
        f"/api/watchlist/{item['id']}/snapshots",
        params={"start": "2026-01-02", "end": "2026-01-01"},
    )
    assert res.status_code == 422
    assert res.json()["detail"] == "起始日期不能晚于结束日期"


def test_snapshots_bad_date_format_returns_422(client):
    item = create_item(client, code="601398")
    res = client.get(f"/api/watchlist/{item['id']}/snapshots?start=2026-13-01")
    assert res.status_code == 422


def test_history_range_endpoint(client):
    """区间元信息：前端用它给自定义日期选择器设 min/max、显示「全部」覆盖范围。"""
    item = create_item(client, code="601398")
    empty = client.get(f"/api/watchlist/{item['id']}/history-range").json()
    assert empty == {"first_date": None, "last_date": None, "count": 0}

    add_snapshot(item["id"], today() - timedelta(days=100), price=8.0, ttm_dividend=0.4)
    add_snapshot(item["id"], today() - timedelta(days=10), price=9.0, ttm_dividend=0.45)

    got = client.get(f"/api/watchlist/{item['id']}/history-range").json()
    assert got["first_date"] == (today() - timedelta(days=100)).isoformat()
    assert got["last_date"] == (today() - timedelta(days=10)).isoformat()
    assert got["count"] == 2


def test_history_range_missing_item_404(client):
    assert client.get("/api/watchlist/999999/history-range").status_code == 404


def test_dividends_all_returns_whole_history(client, monkeypatch):
    """?all=true → 不裁窗口，20 年前的除权日也返回（长区间才有竖线可看）。"""
    item = create_item(client, code="600028", name="中国石化", target_yield=6.0)

    async def fake_dividend(code):
        return [
            {"EX_DIVIDEND_DATE": today() - timedelta(days=4000), "DIVIDEND_PER_SHARE": 0.2,
             "IMPLEMENT_STATUS": "已实施"},
            {"EX_DIVIDEND_DATE": today() - timedelta(days=10), "DIVIDEND_PER_SHARE": 0.4,
             "IMPLEMENT_STATUS": "已实施"},
        ]

    monkeypatch.setattr("routers.watchlist.fetch_dividend_from_eastmoney", fake_dividend)

    narrow = client.get(f"/api/watchlist/{item['id']}/dividends?days=365").json()
    assert len(narrow) == 1
    everything = client.get(f"/api/watchlist/{item['id']}/dividends?all=true").json()
    assert [r["dividend_per_share"] for r in everything] == [0.2, 0.4]


# ---------- 日K翻页（腾讯单次最多 2000 根，全历史要往前翻） ----------

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeKlineClient:
    """按顺序吐出预设的分页响应，并记录每次请求的 param。"""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.params = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        self.params.append(params["param"])
        payload = self._payloads.pop(0) if self._payloads else {"code": 0, "data": {}}
        return _FakeResponse(payload)


def _payload(mc, first_day, count):
    from datetime import datetime as _dt

    bars = []
    for i in range(count):
        day = (first_day + timedelta(days=i)).isoformat()
        bars.append([day, "10.000", f"{10 + i / 1000:.3f}", "10.5", "9.5", "1000"])
    return {"code": 0, "msg": "", "data": {mc: {"day": bars, "_dt": str(_dt)}}}


def test_fetch_daily_closes_pages_back_to_listing(monkeypatch):
    """第一页装满 2000 根 → 继续往前翻；第二页不足 2000 → 到头，合并两页。"""
    import asyncio

    import watchlist_history

    mc = "sh601398"
    page1 = _payload(mc, today() - timedelta(days=2400), watchlist_history.KLINE_PAGE_BARS)
    page2 = _payload(mc, today() - timedelta(days=5000), 120)
    fake = _FakeKlineClient([page1, page2])
    monkeypatch.setattr(watchlist_history.httpx, "AsyncClient", lambda **kw: fake)

    rows = asyncio.run(watchlist_history.fetch_daily_closes("601398", today=today()))

    assert len(rows) == watchlist_history.KLINE_PAGE_BARS + 120
    assert rows == sorted(rows, key=lambda x: x[0])          # 升序
    assert len(fake.params) == 2                              # 翻了两次
    # 第二次请求的 end 顶到第一页最早那天的前一天，保证无缝衔接
    page1_first = page1["data"][mc]["day"][0][0]
    assert fake.params[1].split(",")[3] == (
        datetime.strptime(page1_first, "%Y-%m-%d").date() - timedelta(days=1)
    ).isoformat()
    # 翻到上市日 → 记下边界，后续刷新不必再翻
    assert watchlist_history._EARLIEST_BAR["601398"] == rows[0][0]


def test_fetch_daily_closes_returns_empty_on_network_error(monkeypatch):
    """网络异常 → 空列表（不回填，绝不写假数据）。"""
    import asyncio

    import watchlist_history

    class Boom:
        async def __aenter__(self):
            raise RuntimeError("network down")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(watchlist_history.httpx, "AsyncClient", lambda **kw: Boom())

    assert asyncio.run(watchlist_history.fetch_daily_closes("601398")) == []
    assert "601398" not in watchlist_history._EARLIEST_BAR


def test_fetch_daily_closes_days_window_still_supported(monkeypatch):
    """传 days → 只在最近 N 个自然日内取（旧行为兜底）。"""
    import asyncio

    import watchlist_history

    mc = "sh601398"
    fake = _FakeKlineClient([_payload(mc, today() - timedelta(days=500), 60)])
    monkeypatch.setattr(watchlist_history.httpx, "AsyncClient", lambda **kw: fake)

    rows = asyncio.run(
        watchlist_history.fetch_daily_closes("601398", days=30, today=today())
    )
    # 请求的 start 被裁到 today-30
    assert fake.params[0].split(",")[2] == (today() - timedelta(days=30)).isoformat()
    assert all(d >= today() - timedelta(days=30) for d, _ in rows)
