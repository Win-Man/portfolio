"""后台管理 + 定时任务引擎的用例。

约定：
- 测试期间两个任务都由 conftest 关闭（`*_ENABLED=false`），不会有后台协程；
- 行情全部 monkeypatch：`scheduler.fetch_real_time_price` 是**独立绑定**（`from ... import`），
  只 patch `routers.assets` 里的那份不会影响任务，必须直接 patch `scheduler`；
- 观察池任务走 `routers.watchlist._refresh_one`，那里面用的是 watchlist 模块自己的绑定，
  所以另有一份 patch。
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import scheduler as sched
from config import settings
from database import SessionLocal
from models import Asset, JobRun, WatchlistSnapshot

SHANGHAI = ZoneInfo("Asia/Shanghai")


# ---------- 工具 ----------

def quote_ok(price=12.5):
    """正常的行情返回（source=real）。"""

    async def fake(code, *args, **kwargs):
        return {
            "success": True,
            "name": f"测试{code}",
            "price": price,
            "yesterday_close": price - 0.5,
            "market_code": f"sh{code}",
            "source": "real",
        }

    return fake


def quote_mock(price=1.0):
    """行情失败时 fetch_real_time_price 的降级返回（source=mock）。"""

    async def fake(code, *args, **kwargs):
        return {
            "success": True,
            "name": f"测试{code}",
            "price": price,
            "yesterday_close": price,
            "market_code": f"sh{code}",
            "source": "mock",
        }

    return fake


def patch_quote(monkeypatch, fake):
    monkeypatch.setattr(sched, "fetch_real_time_price", fake)
    return fake


def patch_watchlist_market(monkeypatch, price=10.0, ttm=0.5, source="real"):
    """观察池任务链路上的两个抓取函数（都在 routers.watchlist 的命名空间里）。"""
    import routers.watchlist as wl_mod

    async def fake_price(code, *args, **kwargs):
        return {
            "success": True,
            "name": f"测试{code}",
            "price": price,
            "yesterday_close": price,
            "market_code": f"sh{code}",
            "source": source,
        }

    async def fake_ttm(code, *args, **kwargs):
        return ttm

    monkeypatch.setattr(wl_mod, "fetch_real_time_price", fake_price)
    monkeypatch.setattr(wl_mod, "fetch_ttm_dividend_per_share", fake_ttm)


def all_assets():
    db = SessionLocal()
    try:
        return db.query(Asset).order_by(Asset.id.asc()).all()
    finally:
        db.close()


def run_rows():
    db = SessionLocal()
    try:
        return db.query(JobRun).order_by(JobRun.id.asc()).all()
    finally:
        db.close()


def snapshot_count(item_id):
    db = SessionLocal()
    try:
        return db.query(WatchlistSnapshot).filter(WatchlistSnapshot.item_id == item_id).count()
    finally:
        db.close()


def create_item(client, code="601398", name="工商银行", target_yield=5.0):
    return client.post(
        "/api/watchlist", json={"code": code, "name": name, "target_yield": target_yield}
    ).json()


# ---------- GET /api/admin/jobs ----------

def test_list_jobs_returns_both_tasks_with_config(client):
    """两个任务都要出现在后台管理页，且带计划时刻、开关、时区。"""
    rows = client.get("/api/admin/jobs").json()
    by_key = {r["key"]: r for r in rows}

    assert set(by_key) == {"asset_price_refresh", "watchlist_snapshot"}

    price_job = by_key["asset_price_refresh"]
    assert price_job["name"] == "投资组合价格刷新"
    assert price_job["schedule_time"] == "15:05"
    assert price_job["schedule_label"] == "每天 15:05（Asia/Shanghai）"
    assert price_job["timezone"] == "Asia/Shanghai"

    snapshot_job = by_key["watchlist_snapshot"]
    assert snapshot_job["schedule_time"] == "15:30"

    # conftest 里两个开关都是 false
    assert all(r["enabled"] is False for r in rows)
    # 从没跑过 → 没有最后运行时间，也不该显示「下次运行」
    assert all(r["last_run"] is None for r in rows)
    assert all(r["next_run_at"] is None for r in rows)
    assert all(r["total_run_count"] == 0 for r in rows)


def test_next_run_at_only_when_enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "asset_price_refresh_enabled", True)
    by_key = {r["key"]: r for r in client.get("/api/admin/jobs").json()}

    assert by_key["asset_price_refresh"]["next_run_at"] is not None
    # 另一个仍禁用
    assert by_key["watchlist_snapshot"]["next_run_at"] is None


# ---------- POST /api/admin/jobs/{key}/run：价格任务 ----------

def test_manual_run_refreshes_all_asset_prices(client, monkeypatch):
    patch_quote(monkeypatch, quote_ok(12.5))

    body = client.post("/api/admin/jobs/asset_price_refresh/run").json()

    assert body["job_key"] == "asset_price_refresh"
    assert body["trigger"] == "manual"
    assert body["status"] == "success"
    assert body["total"] == 5          # 种子数据：3 + 2 个资产
    assert body["succeeded"] == 5
    assert body["failed"] == 0
    assert "5/5" in body["message"]
    assert body["finished_at"] is not None
    assert body["duration_ms"] >= 0

    # 价格真的落库了（不是只改了响应）
    assert [a.price for a in all_assets()] == [12.5] * 5


def test_same_code_fetched_only_once(client, monkeypatch, mock_dividend):
    """同一只票出现在两个组合里，只打一次行情接口。"""
    calls = []

    async def fake(code, *args, **kwargs):
        calls.append(code)
        return {"success": True, "price": 20.0, "source": "real", "name": code}

    patch_quote(monkeypatch, fake)

    # 组合 2 里再加一个和组合 1 相同的代码
    created = client.post(
        "/api/portfolios/2/assets",
        json={"name": "长江电力", "code": "600900", "quantity": 100, "price": 28.5},
    )
    assert created.status_code == 200

    body = client.post("/api/admin/jobs/asset_price_refresh/run").json()

    assert calls.count("600900") == 1
    assert len(calls) == len(set(calls))          # 每个代码只出现一次
    assert body["total"] == 6                     # 资产数（含重复代码）
    assert body["succeeded"] == 6


def test_mock_price_is_not_written(client, monkeypatch):
    """行情降级成 mock 时：这一轮算失败，DB 里的价格保持原样（不写假数）。"""
    before = [a.price for a in all_assets()]
    patch_quote(monkeypatch, quote_mock(1.0))

    body = client.post("/api/admin/jobs/asset_price_refresh/run").json()

    assert body["status"] == "failed"
    assert body["succeeded"] == 0
    assert body["failed"] == 5
    assert body["errors"], "失败原因要能看见"
    assert [a.price for a in all_assets()] == before


def test_partial_failure_is_partial(client, monkeypatch):
    """一只标的抛异常 → 状态 partial，其它正常更新。"""

    async def fake(code, *args, **kwargs):
        if code == "601088":
            raise RuntimeError("行情源超时")
        return {"success": True, "price": 9.9, "source": "real", "name": code}

    patch_quote(monkeypatch, fake)
    body = client.post("/api/admin/jobs/asset_price_refresh/run").json()

    assert body["status"] == "partial"
    assert body["succeeded"] == 4
    assert body["failed"] == 1
    assert any("601088" in e for e in body["errors"])


# ---------- 运行历史 ----------

def test_runs_history_is_newest_first_and_paginated(client, monkeypatch):
    patch_quote(monkeypatch, quote_ok(11.0))
    for _ in range(3):
        client.post("/api/admin/jobs/asset_price_refresh/run")

    first_page = client.get("/api/admin/jobs/asset_price_refresh/runs?limit=2").json()
    assert first_page["total"] == 3
    assert len(first_page["items"]) == 2
    assert first_page["items"][0]["id"] > first_page["items"][1]["id"]   # 新的在前

    second_page = client.get(
        "/api/admin/jobs/asset_price_refresh/runs?limit=2&offset=2"
    ).json()
    assert len(second_page["items"]) == 1
    assert second_page["items"][0]["trigger"] == "manual"


def test_list_jobs_reflects_last_run(client, monkeypatch):
    """「最后运行时间」必须来自真实的运行记录。"""
    patch_quote(monkeypatch, quote_ok(11.0))
    run = client.post("/api/admin/jobs/asset_price_refresh/run").json()

    by_key = {r["key"]: r for r in client.get("/api/admin/jobs").json()}
    info = by_key["asset_price_refresh"]

    assert info["last_run"]["id"] == run["id"]
    assert info["last_run"]["started_at"] == run["started_at"]
    assert info["last_success_at"] == run["started_at"]
    assert info["total_run_count"] == 1
    assert info["status_counts"] == {"success": 1}
    assert info["errors_hint"] is None


def test_unknown_job_returns_404(client):
    assert client.get("/api/admin/jobs/nope/runs").status_code == 404
    assert client.post("/api/admin/jobs/nope/run").status_code == 404


def test_job_crash_is_recorded_as_failed(client, monkeypatch):
    """任务本体抛异常也必须留一条失败记录 —— 后台管理页要能看到「为什么没数据」。"""
    import routers.admin as admin_mod

    async def boom(db):
        raise RuntimeError("数据库连接断了")

    spec = sched.JobSpec(
        key="asset_price_refresh",
        name="投资组合价格刷新",
        description="",
        enabled_attr="asset_price_refresh_enabled",
        time_attr="asset_price_refresh_time",
        default_time="15:05",
        runner=boom,
    )
    monkeypatch.setattr(sched, "get_job_specs", lambda: {spec.key: spec})
    monkeypatch.setattr(admin_mod, "get_job_spec", lambda key: spec)

    body = client.post("/api/admin/jobs/asset_price_refresh/run").json()

    assert body["status"] == "failed"
    assert "数据库连接断了" in body["message"]
    assert body["started_at"] and body["finished_at"]
    assert len(run_rows()) == 1


# ---------- 观察池任务 ----------

def test_manual_run_watchlist_writes_snapshot(client, monkeypatch):
    patch_watchlist_market(monkeypatch, price=10.0, ttm=0.5)
    item = create_item(client)

    body = client.post("/api/admin/jobs/watchlist_snapshot/run").json()

    assert body["status"] == "success"
    assert body["total"] == 1
    assert body["succeeded"] == 1
    assert snapshot_count(item["id"]) == 1

    snaps = client.get(f"/api/watchlist/{item['id']}/snapshots").json()
    assert snaps[-1]["price"] == 10.0
    assert snaps[-1]["ttm_yield"] == 5.0


def test_watchlist_run_when_nothing_missing_is_skipped_but_recorded(client, monkeypatch):
    """今天已经齐了：手动触发仍如实记一条 skipped —— 用户点了按钮就该看到确实跑了。"""
    patch_watchlist_market(monkeypatch, price=10.0, ttm=0.5)
    item = create_item(client)

    client.post("/api/admin/jobs/watchlist_snapshot/run")
    body = client.post("/api/admin/jobs/watchlist_snapshot/run").json()

    assert body["status"] == "skipped"
    assert body["total"] == 0
    assert "无需更新" in body["message"]
    assert snapshot_count(item["id"]) == 1      # 没有重复写
    assert len(run_rows()) == 2                  # 两次手动都记了


def test_watchlist_run_records_error_when_price_is_mock(client, monkeypatch):
    patch_watchlist_market(monkeypatch, source="mock")
    item = create_item(client)

    body = client.post("/api/admin/jobs/watchlist_snapshot/run").json()

    assert body["status"] == "failed"
    assert body["failed"] == 1
    assert snapshot_count(item["id"]) == 0
    assert body["errors"]


# ---------- 调度引擎本身 ----------

async def test_schedule_skip_is_recorded_once_per_day(client, monkeypatch):
    """调度触发的空转：当天留一条「跳过」证明跑过，之后不再重复写。"""
    patch_watchlist_market(monkeypatch)
    db = SessionLocal()
    try:
        first = await sched.execute_job("watchlist_snapshot", trigger="schedule", db=db)
        first_status = first.status if first is not None else None
        second = await sched.execute_job("watchlist_snapshot", trigger="schedule", db=db)
    finally:
        db.close()

    assert first_status == "skipped"
    assert second is None                  # 当天已经有记录，不再堆一条
    assert len(run_rows()) == 1


async def test_tick_runs_due_job_once_per_day(monkeypatch):
    monkeypatch.setattr(settings, "asset_price_refresh_enabled", True)
    monkeypatch.setattr(settings, "asset_price_refresh_time", "00:00")
    monkeypatch.setattr(settings, "watchlist_snapshot_enabled", False)
    patch_quote(monkeypatch, quote_ok(13.0))

    now = datetime(2026, 3, 2, 9, 0, tzinfo=SHANGHAI)
    runs = await sched.tick(now)
    assert [r.job_key for r in runs] == ["asset_price_refresh"]
    assert runs[0].status == "success"

    # 一分钟后：今天已成功 → 不再跑
    assert await sched.tick(now + timedelta(minutes=1)) == []
    assert len(run_rows()) == 1


async def test_tick_waits_for_scheduled_time(monkeypatch):
    monkeypatch.setattr(settings, "asset_price_refresh_enabled", True)
    monkeypatch.setattr(settings, "asset_price_refresh_time", "15:05")
    monkeypatch.setattr(settings, "watchlist_snapshot_enabled", False)
    patch_quote(monkeypatch, quote_ok(13.0))

    before = datetime(2026, 3, 2, 15, 4, tzinfo=SHANGHAI)
    assert await sched.tick(before) == []

    due = datetime(2026, 3, 2, 15, 5, tzinfo=SHANGHAI)
    assert len(await sched.tick(due)) == 1


async def test_tick_retries_up_to_three_times_then_gives_up(monkeypatch):
    """外部源整体挂掉：一天最多试 3 轮，不会每分钟重试。"""
    monkeypatch.setattr(settings, "asset_price_refresh_enabled", True)
    monkeypatch.setattr(settings, "asset_price_refresh_time", "00:00")
    monkeypatch.setattr(settings, "watchlist_snapshot_enabled", False)
    patch_quote(monkeypatch, quote_mock())

    now = datetime(2026, 3, 3, 9, 0, tzinfo=SHANGHAI)
    for i in range(4):
        await sched.tick(now + timedelta(minutes=i))

    # 前 3 轮各记一条失败，第 4 轮放弃
    assert len(run_rows()) == sched.MAX_ATTEMPTS_PER_DAY
    assert all(r.status == "failed" for r in run_rows())


def test_parse_hhmm_falls_back_on_garbage():
    assert sched.parse_hhmm("15:30", "09:00") == datetime(2000, 1, 1, 15, 30).time()
    assert sched.parse_hhmm("25:00", "09:00") == datetime(2000, 1, 1, 9, 0).time()
    assert sched.parse_hhmm("", "09:00") == datetime(2000, 1, 1, 9, 0).time()
    assert sched.parse_hhmm("abc", "09:00") == datetime(2000, 1, 1, 9, 0).time()


def test_next_run_at_rolls_to_tomorrow():
    spec = sched.get_job_specs()["watchlist_snapshot"]
    before = datetime(2026, 3, 2, 9, 0, tzinfo=SHANGHAI)
    after = datetime(2026, 3, 2, 16, 0, tzinfo=SHANGHAI)

    assert sched.next_run_at(spec, before) == datetime(2026, 3, 2, 15, 30, tzinfo=SHANGHAI)
    assert sched.next_run_at(spec, after) == datetime(2026, 3, 3, 15, 30, tzinfo=SHANGHAI)
