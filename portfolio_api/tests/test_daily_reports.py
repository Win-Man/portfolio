"""每日日报：生成 / 落库 / 接口 / 定时任务 的集成测试。

原则（与其余用例一致）：
- **不发真实网络请求**：`routers.assets.fetch_real_time_price` 一律 monkeypatch；
- 日期全部相对「今天（Asia/Shanghai）」构造，用例不随系统日期腐化；
- 观察池数据直接写库（快照走 `WatchlistSnapshot`），不经过抓取流程。

覆盖的坑：
- 同一份日报重复生成不能攒出多行（UPSERT）；
- 行情拿不到昨收时，退到「上一份日报里的价格」，仍然算得出今日盈亏；
- 行情是 mock 时**不能**用它的价（±3% 随机数），要用库里的价；
- 观察池「今天没快照」的标的不能算进今日涨跌（那是旧数据）；
- 组合日报里的「今日盈亏」必须等于明细逐行相加。
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import daily_reports
import scheduler
from database import SessionLocal
from models import DailyReport, WatchlistSnapshot

SHANGHAI = ZoneInfo("Asia/Shanghai")


def today():
    return datetime.now(SHANGHAI).date()


# ---------- 测试工具 ----------

def make_portfolio(client, name="日报测试组合"):
    res = client.post("/api/portfolios", json={"name": name})
    assert res.status_code == 200 or res.status_code == 201, res.text
    return res.json()["id"]


def add_asset(client, portfolio_id, **overrides):
    payload = {
        "name": "长江电力",
        "code": "600900",
        "asset_type": "股票",
        "quantity": 1000,
        "price": 10.0,
        "cost_price": 8.0,
        "dividend_per_share": 0.5,
    }
    payload.update(overrides)
    res = client.post(f"/api/portfolios/{portfolio_id}/assets", json=payload)
    assert res.status_code == 200, res.text
    return res.json()


def patch_quotes(monkeypatch, quotes):
    """把行情换成固定表：{code: (price, prev_close)}；不在表里的代码返回 mock。"""
    calls = []

    async def fake_quote(code, current_price=0, dividend_per_share=0):
        calls.append(code)
        if code in quotes:
            price, prev_close = quotes[code]
            return {
                "price": price,
                "yesterday_close": prev_close,
                "name": code,
                "ttm_yield": 0.0,
                "success": True,
                "source": "real",
            }
        # 行情失败 → mock（±3% 随机价），日报不该采信
        return {
            "price": 99.0,
            "name": code,
            "ttm_yield": 0.0,
            "success": True,
            "mock": True,
            "source": "mock",
            "error": "测试环境模拟失败",
        }

    import routers.assets as assets_mod
    monkeypatch.setattr(assets_mod, "fetch_real_time_price", fake_quote)
    return calls


def add_snapshot(item_id, trade_date, price, ttm_dividend):
    db = SessionLocal()
    try:
        db.add(
            WatchlistSnapshot(
                item_id=item_id,
                trade_date=trade_date,
                price=price,
                ttm_dividend=ttm_dividend,
                ttm_yield=(ttm_dividend / price * 100) if (ttm_dividend > 0 and price > 0) else None,
                price_source="real",
            )
        )
        db.commit()
    finally:
        db.close()


def create_watch_item(client, **overrides):
    payload = {"code": "600900", "name": "长江电力", "target_yield": 5.0}
    payload.update(overrides)
    res = client.post("/api/watchlist", json=payload)
    assert res.status_code == 201, res.text
    return res.json()


# ---------- 组合日报 ----------

def test_portfolio_report_persists_summary_and_items(client, monkeypatch):
    pid = make_portfolio(client)
    add_asset(client, pid, code="600900", name="长江电力", quantity=1000, price=10.0, cost_price=8.0,
              dividend_per_share=0.5)
    add_asset(client, pid, code="601088", name="中国神华", quantity=100, price=20.0, cost_price=25.0,
              dividend_per_share=1.0)

    patch_quotes(monkeypatch, {"600900": (11.0, 10.0), "601088": (19.0, 20.0)})

    res = client.post("/api/reports/generate", json={"report_type": "portfolio", "portfolio_id": pid})
    assert res.status_code == 200, res.text
    brief = res.json()["items"][0]

    # 长江 (+1×1000) + 神华 (−1×100) = 900
    assert brief["total_pnl"] == pytest.approx(900.0)
    assert brief["report_date"] == today().isoformat()
    assert brief["item_count"] == 2

    detail = client.get(f"/api/reports/{brief['id']}").json()
    summary = detail["payload"]["summary"]
    assert summary["day_pnl"] == pytest.approx(900.0)
    # 昨日市值 10,000 + 2,000 = 12,000 → 涨跌幅 7.5%
    assert summary["day_pnl_percent"] == pytest.approx(7.5)
    assert summary["market_value"] == pytest.approx(11_000 + 1_900)

    # 汇总必须等于明细逐行相加，否则页面上「合计 ≠ 明细和」
    assert summary["day_pnl"] == pytest.approx(sum(i["day_pnl"] for i in detail["payload"]["items"]))
    assert summary["market_value"] == pytest.approx(
        sum(i["market_value"] for i in detail["payload"]["items"])
    )


def test_portfolio_report_upserts_instead_of_appending(client, monkeypatch):
    pid = make_portfolio(client)
    add_asset(client, pid)
    patch_quotes(monkeypatch, {"600900": (11.0, 10.0)})

    first = client.post("/api/reports/generate", json={"report_type": "portfolio", "portfolio_id": pid})
    second = client.post("/api/reports/generate", json={"report_type": "portfolio", "portfolio_id": pid})
    assert first.json()["items"][0]["id"] == second.json()["items"][0]["id"]

    rows = client.get("/api/reports?report_type=portfolio", params={"portfolio_id": pid}).json()
    assert rows["total"] == 1


def test_portfolio_report_falls_back_to_previous_report_price(client, monkeypatch):
    """行情挂了 → 用「上一份日报里的价格」当昨收，今日盈亏仍算得出来。"""
    pid = make_portfolio(client)
    add_asset(client, pid, code="600900", quantity=1000, price=10.0)

    # 昨天：生成一份日报，价格 10 元（作为今天的昨收）
    patch_quotes(monkeypatch, {"600900": (10.0, 9.0)})
    yesterday = today() - timedelta(days=1)
    with SessionLocal() as db:
        rows = __import__("asyncio").run(
            daily_reports.generate_portfolio_reports(db, today=yesterday, portfolio_id=pid)
        )
        assert len(rows) == 1

    # 今天：行情全挂（mock），库里价格改成 11
    patch_quotes(monkeypatch, {})
    with SessionLocal() as db:
        from models import Asset
        asset = db.query(Asset).filter(Asset.portfolio_id == pid).first()
        asset.price = 11.0
        db.commit()

    res = client.post("/api/reports/generate", json={"report_type": "portfolio", "portfolio_id": pid})
    payload = client.get(f"/api/reports/{res.json()['items'][0]['id']}").json()["payload"]

    item = payload["items"][0]
    assert item["prev_close"] == pytest.approx(10.0)   # 来自昨天那份日报
    assert item["price"] == pytest.approx(11.0)        # mock 价 99 没有被采信
    assert item["price_source"] != "real"
    assert item["day_pnl"] == pytest.approx(1000.0)
    # 提示里说清「价格不是当日真实行情」
    assert any("不是本次拉到的当日行情" in n for n in payload["notes"])


def test_portfolio_report_uses_db_price_when_quote_is_mock(client, monkeypatch):
    pid = make_portfolio(client)
    add_asset(client, pid, code="600900", quantity=100, price=7.5)
    patch_quotes(monkeypatch, {})

    res = client.post("/api/reports/generate", json={"report_type": "portfolio", "portfolio_id": pid})
    payload = client.get(f"/api/reports/{res.json()['items'][0]['id']}").json()["payload"]

    item = payload["items"][0]
    assert item["price"] == 7.5          # 库里的价，而不是 mock 的 99
    assert item["prev_close"] is None    # 没有上一份日报 → 没有昨收
    assert item["day_pnl"] is None       # 不知道昨收，就**不能**给出今日盈亏
    assert payload["summary"]["day_pnl"] is None
    assert any("没取到昨收" in n for n in payload["notes"])


def test_portfolio_report_includes_goal_progress(client, monkeypatch):
    """日报里带目标进度，且与目标卡片同一口径（都用 portfolio_math.goal_gap）。"""
    pid = make_portfolio(client)
    add_asset(client, pid, quantity=1000, price=10.0, dividend_per_share=0.5)  # 年收入 500
    client.post(f"/api/portfolios/{pid}/goals", json={"name": "退休", "annual_income_target": 1000})
    patch_quotes(monkeypatch, {"600900": (10.0, 10.0)})

    res = client.post("/api/reports/generate", json={"report_type": "portfolio", "portfolio_id": pid})
    payload = client.get(f"/api/reports/{res.json()['items'][0]['id']}").json()["payload"]

    goal = payload["goals"][0]
    assert goal["current_annual_income"] == pytest.approx(500.0)
    assert goal["progress_percent"] == pytest.approx(50.0)
    assert goal["gap_annual"] == pytest.approx(500.0)
    assert goal["is_achieved"] is False


# ---------- 观察池日报 ----------

def test_watchlist_report_signals_and_equal_weight(client):
    d1 = today() - timedelta(days=1)
    d0 = today()

    # 格力：股息率 7.65% ≥ 目标 7% → 股息率达标；现价 39.19 > 目标价 35 → 价格未到
    gree = create_watch_item(client, code="000651", name="格力电器", target_yield=7.0, target_price=35.0)
    add_snapshot(gree["id"], d1, 38.52, 3.0)      # 昨收
    add_snapshot(gree["id"], d0, 39.19, 3.0)      # 今收 → 股息率 7.65%

    # 平安：现价 45 ≤ 目标价 50 → 目标价达标；股息率 3% < 5% → 未达标
    pingan = create_watch_item(client, code="601318", name="中国平安", target_yield=5.0, target_price=50.0)
    add_snapshot(pingan["id"], d1, 50.0, 1.5)     # 昨收
    add_snapshot(pingan["id"], d0, 45.0, 1.5)     # 今收

    res = client.post("/api/reports/generate", json={"report_type": "watchlist"})
    assert res.status_code == 200, res.text
    report_id = res.json()["items"][0]["id"]
    payload = client.get(f"/api/reports/{report_id}").json()["payload"]

    summary = payload["summary"]
    assert summary["item_count"] == 2
    assert summary["today_count"] == 2
    assert summary["yield_hit_count"] == 1
    assert summary["price_hit_count"] == 1

    assert [i["code"] for i in payload["yield_hits"]] == ["000651"]
    assert [i["code"] for i in payload["price_hits"]] == ["601318"]

    items = {i["code"]: i for i in payload["items"]}
    # 格力 +1.74%（38.52 → 39.19）；平安 −10%
    assert items["000651"]["change_percent"] == pytest.approx((39.19 - 38.52) / 38.52 * 100)
    assert items["601318"]["change_percent"] == pytest.approx(-10.0)
    # 等权：每只 1 万 → 合计 = 1 万 × 两个涨跌幅之和
    expected = 10000 * items["000651"]["change_percent"] / 100 + 10000 * (-10.0) / 100
    assert summary["day_pnl"] == pytest.approx(expected)


def test_watchlist_report_excludes_items_without_today_snapshot(client):
    """今天没快照的标的：用最近快照展示，但**不计入**今日涨跌。"""
    item = create_watch_item(client, code="600900", name="长江电力", target_yield=5.0)
    add_snapshot(item["id"], today() - timedelta(days=2), 28.0, 1.0)
    add_snapshot(item["id"], today() - timedelta(days=1), 29.0, 1.0)   # 最新一条是昨天

    res = client.post("/api/reports/generate", json={"report_type": "watchlist"})
    payload = client.get(f"/api/reports/{res.json()['items'][0]['id']}").json()["payload"]

    assert payload["summary"]["today_count"] == 0
    assert payload["summary"]["day_pnl"] is None
    assert payload["items"][0]["is_today"] is False
    assert any("今日还没有快照" in n for n in payload["notes"])


def test_watchlist_report_handles_empty_watchlist(client):
    res = client.post("/api/reports/generate", json={"report_type": "watchlist"})
    assert res.status_code == 200
    payload = client.get(f"/api/reports/{res.json()['items'][0]['id']}").json()["payload"]
    assert payload["items"] == []
    assert any("观察池为空" in n for n in payload["notes"])


# ---------- 接口校验 ----------

def test_list_reports_requires_portfolio_id_for_portfolio_type(client):
    res = client.get("/api/reports", params={"report_type": "portfolio"})
    assert res.status_code == 422
    assert "portfolio_id" in res.json()["detail"]


def test_list_reports_rejects_unknown_type(client):
    res = client.get("/api/reports", params={"report_type": "nope"})
    assert res.status_code == 422


def test_latest_report_404_when_none(client):
    pid = make_portfolio(client)
    res = client.get("/api/reports/latest", params={"report_type": "portfolio", "portfolio_id": pid})
    assert res.status_code == 404


def test_generate_for_unknown_portfolio_404(client, monkeypatch):
    patch_quotes(monkeypatch, {})
    res = client.post("/api/reports/generate", json={"report_type": "portfolio", "portfolio_id": 999999})
    assert res.status_code == 404


def test_generate_all_portfolios_when_portfolio_id_omitted(client, monkeypatch):
    patch_quotes(monkeypatch, {})
    res = client.post("/api/reports/generate", json={"report_type": "portfolio"})
    assert res.status_code == 200
    generated = res.json()["items"]
    # conftest 种了 3 个组合，一个都不能漏
    assert len(generated) == 3
    assert {g["scope_id"] for g in generated} == {1, 2, 3}


def test_delete_report(client, monkeypatch):
    pid = make_portfolio(client)
    add_asset(client, pid)
    patch_quotes(monkeypatch, {"600900": (11.0, 10.0)})
    report_id = client.post(
        "/api/reports/generate", json={"report_type": "portfolio", "portfolio_id": pid}
    ).json()["items"][0]["id"]

    assert client.delete(f"/api/reports/{report_id}").status_code == 200
    assert client.get(f"/api/reports/{report_id}").status_code == 404


def test_list_reports_paginates_and_orders_desc(client, monkeypatch):
    pid = make_portfolio(client)
    add_asset(client, pid)
    patch_quotes(monkeypatch, {"600900": (11.0, 10.0)})

    import asyncio
    with SessionLocal() as db:
        for offset in range(3):
            asyncio.run(
                daily_reports.generate_portfolio_reports(
                    db, today=today() - timedelta(days=offset), portfolio_id=pid
                )
            )

    res = client.get(
        "/api/reports",
        params={"report_type": "portfolio", "portfolio_id": pid, "limit": 2, "offset": 0},
    ).json()
    assert res["total"] == 3
    assert len(res["items"]) == 2
    dates = [i["report_date"] for i in res["items"]]
    assert dates == sorted(dates, reverse=True)


# ---------- 定时任务 ----------

def test_scheduler_job_generates_all_portfolios(client, monkeypatch):
    patch_quotes(monkeypatch, {"600900": (11.0, 10.0)})
    with SessionLocal() as db:
        result = __import__("asyncio").run(scheduler.run_portfolio_daily_report(db))

    assert result.total == 3
    assert result.succeeded == 3
    assert result.failed == 0
    assert "组合日报 3/3 成功" in result.message


def test_scheduler_job_watchlist_counts_items(client):
    item = create_watch_item(client, code="600900", name="长江电力", target_yield=5.0)
    add_snapshot(item["id"], today() - timedelta(days=1), 28.0, 1.0)
    add_snapshot(item["id"], today(), 29.0, 1.0)

    import asyncio
    with SessionLocal() as db:
        result = asyncio.run(scheduler.run_watchlist_daily_report(db))

    assert result.succeeded == 1
    assert "1 只标的" in result.message


def test_scheduler_job_keeps_going_when_one_portfolio_fails(client, monkeypatch):
    """单个组合炸了，其余组合仍要有日报。"""
    patch_quotes(monkeypatch, {})
    calls = {"n": 0}
    original = daily_reports.generate_portfolio_reports

    async def flaky(db, today=None, portfolio_id=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("模拟第二个组合失败")
        return await original(db, today=today, portfolio_id=portfolio_id)

    monkeypatch.setattr(scheduler, "generate_portfolio_reports", flaky)

    import asyncio
    with SessionLocal() as db:
        result = asyncio.run(scheduler.run_portfolio_daily_report(db))

    assert result.total == 3
    assert result.succeeded == 2
    assert result.failed == 1
    assert "模拟第二个组合失败" in result.errors[0]
