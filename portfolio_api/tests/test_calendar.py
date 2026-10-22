"""收入日历：聚合口径 / 状态映射 / 确认幂等 / 补录 / 边界。

对应 docs/specs/002-income-calendar.md §10.1 的 19 个用例。
akshare 拉取一律用本文件里的 `dividends` 桩（可控日期与状态），
不依赖真实网络；`_dividend_cache` 由 conftest 每个用例前后清空。
"""
import time
from datetime import date, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from market import normalize_code
from models import Asset, DividendConfirmation, ManualDividendRecord

TODAY = date.today()
YEAR = TODAY.year
# 当月 1 号：既保证「≤ 今天」（补录的硬约束），又保证落在当前年当前月，
# 不会因为 today-30d 之类的相对日期跨月/跨年而让断言飘。
D_CUR = date(YEAR, TODAY.month, 1)


def _rec(day: date, per_share: float, status: str = "已实施") -> dict:
    """构造一条 akshare 分红记录（字段与 _fetch_stock_dividend_blocking 一致）。"""
    return {
        "EX_DIVIDEND_DATE": day.strftime("%Y-%m-%d"),
        "DIVIDEND_PER_SHARE": per_share,
        "BONUS_IT_RATIO": 0.0,
        "TRANSFER_RATIO": 0.0,
        "IMPLEMENT_STATUS": status,
        "REPORT_TIME": "",
    }


@pytest.fixture
def dividends(monkeypatch):
    """可控的分红桩：`dividends["600900"] = [_rec(...)]`。

    替换的是 `routers.stocks.fetch_dividend_from_eastmoney`，
    `fetch_all_dividend_records` 内部引用同一个模块全局名，因此同样被替换。
    """
    store: dict = {}

    async def fake_fetch(stock_code, *args, **kwargs):
        import routers.stocks as stocks_mod
        key = normalize_code(stock_code)
        rows = store.get(key, [])
        with stocks_mod._dividend_cache_lock:
            stocks_mod._dividend_cache[key] = (time.time(), rows)
        return rows

    import routers.stocks as stocks_mod
    monkeypatch.setattr(stocks_mod, "fetch_dividend_from_eastmoney", fake_fetch)
    return store


def _asset(
    db,
    pid,
    code,
    *,
    quantity=1000.0,
    asset_type="股票",
    price=10.0,
    cost_price=None,
    price_source="real",
    name=None,
):
    asset = Asset(
        portfolio_id=pid,
        name=name or f"标的{code}",
        code=code,
        asset_type=asset_type,
        quantity=quantity,
        price=price,
        cost_price=cost_price,
        dividend_per_share=0.0,
        ttm_yield=0.0,
        price_source=price_source,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


PID_EMPTY = 3   # 种子里的「备用金」组合，无资产
PID_OTHER = 2   # 种子里的「养老金」组合


def _calendar(client, pid=PID_EMPTY, year=YEAR):
    r = client.get(f"/api/portfolios/{pid}/calendar", params={"year": year})
    assert r.status_code == 200, r.text
    return r.json()


def _month(client, pid=PID_EMPTY, year=YEAR, month=D_CUR.month):
    r = client.get(f"/api/portfolios/{pid}/calendar/{year}/{month}")
    assert r.status_code == 200, r.text
    return r.json()


def _slot(body, d: date):
    return body["months"][d.month - 1]


# ---------- 1 ~ 6：结构与聚合 ----------

def test_calendar_empty_portfolio(client):
    """1. 组合无资产 → 12 个月全 0，asset_count 0（且不传 year 时默认当年）"""
    body = _calendar(client)
    assert body["portfolio_id"] == PID_EMPTY
    assert body["year"] == YEAR
    assert len(body["months"]) == 12
    assert [m["month"] for m in body["months"]] == list(range(1, 13))
    for m in body["months"]:
        assert m["total_amount"] == 0
        assert m["count"] == 0
        assert set(m["by_status"]) == {"predicted", "announced", "paid", "manual"}
        assert set(m["by_source"]) == {"stock", "etf", "bond", "otc", "reit", "custom", "self", "manual"}
    assert body["total_year"] == 0
    assert body["total_count"] == 0
    assert body["asset_count"] == 0

    # 不传 year → 当前年
    r = client.get(f"/api/portfolios/{PID_EMPTY}/calendar")
    assert r.status_code == 200
    assert r.json()["year"] == YEAR


def test_calendar_single_dividend(client, db_session, dividends):
    """2. 1 个资产 1 笔已实施分红 → 当月合计 = quantity × 每股分红"""
    dividends["600900"] = [_rec(D_CUR, 0.5)]
    _asset(db_session, PID_EMPTY, "600900", quantity=1000)

    body = _calendar(client)
    slot = _slot(body, D_CUR)
    assert slot["total_amount"] == pytest.approx(1000 * 0.5)
    assert slot["count"] == 1
    assert slot["by_status"]["paid"] == pytest.approx(500)
    assert slot["by_status"]["predicted"] == 0
    assert slot["by_source"]["stock"] == pytest.approx(500)
    assert body["total_year"] == pytest.approx(500)
    assert body["total_count"] == 1
    assert body["asset_count"] == 1

    # 回归锁定（用户实际反馈过的问题）：概览「预计年收入」= Σ(持股数 × 每股 TTM 分红)，
    # 走的是**滚动 12 个月**窗口；日历的「自然年合计」只算本自然年 —— 两者窗口不同，
    # 数字天然可能不相等。这里同时锁定：
    #   • 「近 12 个月」必须等于 summary.expected_annual_income（口径一致）
    #   • 「自然年合计」不包含落在去年、但仍在滚动窗口内的那一笔
    asset = db_session.query(Asset).filter(Asset.portfolio_id == PID_EMPTY).first()
    asset.dividend_per_share = 0.8          # 0.5（本月）+ 0.3（去年 12/31，仍在滚动窗口内）
    db_session.commit()
    prev_year_day = date(TODAY.year - 1, 12, 31)
    dividends["600900"] = [_rec(D_CUR, 0.5), _rec(prev_year_day, 0.3)]

    summary = client.get(f"/api/portfolios/{PID_EMPTY}/summary").json()
    body = _calendar(client)
    assert body["rolling_12m_total"] == pytest.approx(800)
    assert body["rolling_12m_count"] == 2
    assert body["rolling_12m_total"] == pytest.approx(summary["expected_annual_income"])
    # 去年 12/31 那笔会按「去年同期」投影到 12 月 → 预估 ¥300（标 is_estimated，不计入已收录）
    assert body["recorded_total"] == pytest.approx(500)
    assert body["estimated_total"] == pytest.approx(300)
    assert body["total_year"] == pytest.approx(800)
    assert body["total_count"] == 2


def test_calendar_two_assets_accumulate(client, db_session, dividends):
    """3. 两个标的各自贡献正确累加"""
    dividends["600900"] = [_rec(D_CUR, 0.5)]
    dividends["600519"] = [_rec(D_CUR, 1.0)]
    _asset(db_session, PID_EMPTY, "600900", quantity=1000)   # 500
    _asset(db_session, PID_EMPTY, "600519", quantity=500)    # 500

    body = _calendar(client)
    slot = _slot(body, D_CUR)
    assert slot["total_amount"] == pytest.approx(1000)
    assert slot["count"] == 2
    assert slot["by_source"]["stock"] == pytest.approx(1000)
    assert body["total_year"] == pytest.approx(1000)


def test_calendar_source_etf_vs_stock(client, db_session, dividends):
    """4. ETF 代码 → etf；个股 → stock"""
    dividends["600900"] = [_rec(D_CUR, 1.0)]
    dividends["561580"] = [_rec(D_CUR, 0.1)]
    _asset(db_session, PID_EMPTY, "600900", quantity=100)          # 股票 → 100
    _asset(db_session, PID_EMPTY, "561580", quantity=1000, asset_type="ETF")  # ETF → 100

    body = _calendar(client)
    slot = _slot(body, D_CUR)
    assert slot["by_source"]["stock"] == pytest.approx(100)
    assert slot["by_source"]["etf"] == pytest.approx(100)
    assert slot["total_amount"] == pytest.approx(200)


def test_calendar_source_by_asset_type(client, db_session, dividends):
    """5. asset_type 关键词 → bond / otc / reit / self / custom"""
    mapping = {
        "600011": "债券",
        "600012": "OTC",
        "600013": "REITs",
        "600014": "自建组合",
        "600015": "自定义",
    }
    for code, asset_type in mapping.items():
        dividends[code] = [_rec(D_CUR, 0.1)]
        _asset(db_session, PID_EMPTY, code, quantity=100, asset_type=asset_type)

    slot = _slot(_calendar(client), D_CUR)
    for expected in ("bond", "otc", "reit", "self", "custom"):
        assert slot["by_source"][expected] == pytest.approx(10), expected
    assert slot["total_amount"] == pytest.approx(50)


def test_calendar_cross_year(client, db_session, dividends):
    """6. 跨年：不同 year 取到不同月合计，互不串年"""
    d_this = date(YEAR, 6, 15)
    d_prev = date(YEAR - 1, 6, 15)
    d_prev2 = date(YEAR - 2, 6, 15)
    dividends["600900"] = [
        _rec(d_this, 0.5),
        _rec(d_prev, 0.7),
        _rec(d_prev2, 0.9),
    ]
    _asset(db_session, PID_EMPTY, "600900", quantity=1000)

    assert _calendar(client, year=YEAR)["months"][5]["total_amount"] == pytest.approx(500)
    assert _calendar(client, year=YEAR - 1)["months"][5]["total_amount"] == pytest.approx(700)
    assert _calendar(client, year=YEAR - 2)["months"][5]["total_amount"] == pytest.approx(900)
    # 当年总数只算当年的
    assert _calendar(client, year=YEAR)["total_year"] == pytest.approx(500)
    assert _calendar(client, year=YEAR)["total_count"] == 1


# ---------- 7 ~ 10：状态与确认 ----------

def test_calendar_status_mapping(client, db_session, dividends):
    """7. IMPLEMENT_STATUS → paid / announced / predicted（缺失与"未实施"都保守）"""
    cases = [
        ("已实施", "paid"),
        ("已发布", "announced"),
        ("实施中", "announced"),
        ("董事会预案", "predicted"),
        ("股东大会通过", "predicted"),
        ("", "predicted"),
        ("未实施", "predicted"),
    ]
    records = [
        _rec(date(YEAR, TODAY.month, i + 1), 1.0, status)
        for i, (status, _) in enumerate(cases)
    ]
    dividends["600900"] = records
    _asset(db_session, PID_EMPTY, "600900", quantity=100)

    body = _month(client)
    by_code_status = {item["ex_date"]: item["status"] for item in body["items"]}
    for i, (raw, expected) in enumerate(cases):
        day = date(YEAR, TODAY.month, i + 1).strftime("%Y-%m-%d")
        assert by_code_status[day] == expected, f"{raw!r} → {expected}"

    slot = _slot(_calendar(client), D_CUR)
    assert slot["count"] == len(cases)
    assert slot["by_status"]["paid"] == pytest.approx(100)
    assert slot["by_status"]["announced"] == pytest.approx(200)
    assert slot["by_status"]["predicted"] == pytest.approx(400)


def test_confirm_is_idempotent(client, db_session, dividends):
    """8. 同一 (code, ex_date) 确认两次 → 第二次幂等，confirmed_at 不变"""
    dividends["600900"] = [_rec(D_CUR, 0.5)]
    _asset(db_session, PID_EMPTY, "600900", quantity=1000)
    payload = {"code": "600900", "ex_date": D_CUR.strftime("%Y-%m-%d")}

    r1 = client.post(f"/api/portfolios/{PID_EMPTY}/dividends/confirm", json=payload)
    assert r1.status_code == 200, r1.text
    first = r1.json()
    assert first["status"] == "paid"
    assert first["code"] == "600900"

    r2 = client.post(f"/api/portfolios/{PID_EMPTY}/dividends/confirm", json=payload)
    assert r2.status_code == 200, r2.text
    second = r2.json()
    assert second["confirmed_at"] == first["confirmed_at"]

    db_session.expire_all()
    rows = db_session.query(DividendConfirmation).filter(
        DividendConfirmation.portfolio_id == PID_EMPTY
    ).all()
    assert len(rows) == 1


def test_confirm_flips_status_without_changing_amount(client, db_session, dividends):
    """9. 确认后 status 变 paid、is_confirmed=True，金额完全不变；未知记录 404"""
    dividends["600900"] = [_rec(D_CUR, 0.5, status="已发布")]
    _asset(db_session, PID_EMPTY, "600900", quantity=1000)

    before = _slot(_calendar(client), D_CUR)
    assert before["by_status"]["announced"] == pytest.approx(500)
    assert before["total_amount"] == pytest.approx(500)

    r = client.post(
        f"/api/portfolios/{PID_EMPTY}/dividends/confirm",
        json={"code": "600900", "ex_date": D_CUR.strftime("%Y-%m-%d")},
    )
    assert r.status_code == 200, r.text

    after = _slot(_calendar(client), D_CUR)
    assert after["by_status"]["paid"] == pytest.approx(500)
    assert after["by_status"]["announced"] == 0
    assert after["total_amount"] == pytest.approx(before["total_amount"])

    items = _month(client)["items"]
    assert len(items) == 1
    assert items[0]["status"] == "paid"
    assert items[0]["is_confirmed"] is True
    assert items[0]["amount"] == pytest.approx(500)

    # 找不到该笔记录 → 404
    r = client.post(
        f"/api/portfolios/{PID_EMPTY}/dividends/confirm",
        json={"code": "600900", "ex_date": (D_CUR - timedelta(days=400)).strftime("%Y-%m-%d")},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "找不到该笔分红记录"


def test_manual_overrides_akshare(client, db_session, dividends):
    """10. 补录：同 (code, ex_date) 覆盖 akshare，金额只算一次，来源归 manual"""
    dividends["600900"] = [_rec(D_CUR, 0.5)]   # akshare 500
    asset = _asset(db_session, PID_EMPTY, "600900", quantity=1000)

    r = client.post(
        f"/api/portfolios/{PID_EMPTY}/dividends/manual",
        json={
            "code": "600900",
            "ex_date": D_CUR.strftime("%Y-%m-%d"),
            "amount_per_share": 0.9,
            "note": "券商补账",
        },
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["portfolio_id"] == PID_EMPTY
    assert created["asset_id"] == asset.id
    assert created["status"] == "manual"
    assert created["note"] == "券商补账"

    slot = _slot(_calendar(client), D_CUR)
    assert slot["total_amount"] == pytest.approx(900)      # 900，而不是 500 + 900
    assert slot["count"] == 1
    assert slot["by_source"]["manual"] == pytest.approx(900)
    assert slot["by_source"]["stock"] == 0
    assert slot["by_status"]["manual"] == pytest.approx(900)

    items = _month(client)["items"]
    assert len(items) == 1
    assert items[0]["is_manual"] is True
    assert items[0]["status"] == "manual"
    assert items[0]["note"] == "券商补账"
    assert items[0]["dividend_per_share"] == pytest.approx(0.9)

    # 列表接口能看到补录
    lst = client.get(f"/api/portfolios/{PID_EMPTY}/dividends/manual").json()
    assert [x["id"] for x in lst] == [created["id"]]

    # D6 对补录同样成立：补录成 announced 后「点击确认」必须真的把它标成 paid
    # （否则 UI 上会是一个按了没反应的死按钮）
    client.delete(f"/api/portfolios/{PID_EMPTY}/dividends/manual/{created['id']}")
    announced = client.post(
        f"/api/portfolios/{PID_EMPTY}/dividends/manual",
        json={
            "code": "600900",
            "ex_date": D_CUR.strftime("%Y-%m-%d"),
            "amount_per_share": 0.4,
            "status": "announced",
        },
    ).json()
    item = _month(client)["items"][0]
    assert item["status"] == "announced" and item["is_manual"] is True

    r = client.post(
        f"/api/portfolios/{PID_EMPTY}/dividends/confirm",
        json={"code": "600900", "ex_date": D_CUR.strftime("%Y-%m-%d")},
    )
    assert r.status_code == 200, r.text
    item = _month(client)["items"][0]
    assert item["status"] == "paid"
    assert item["is_confirmed"] is True and item["is_manual"] is True
    assert item["amount"] == pytest.approx(400)   # 1000 × 0.4，确认不加金额
    assert client.delete(f"/api/portfolios/{PID_EMPTY}/dividends/manual/{announced['id']}").status_code == 200


# ---------- 11 ~ 14：补录的校验 / 删除 / 隔离 ----------

def test_manual_validation(client, db_session, dividends):
    """11. 未来日期 / 组合内无此标的 / 组合不存在 / 非法状态 → 422 / 404"""
    _asset(db_session, PID_EMPTY, "600900", quantity=1000)
    url = f"/api/portfolios/{PID_EMPTY}/dividends/manual"
    future = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")

    r = client.post(url, json={"code": "600900", "ex_date": future, "amount_per_share": 1.0})
    assert r.status_code == 422
    assert r.json()["detail"] == "不能补未来日期；预告请等待 akshare 收录"

    r = client.post(url, json={"code": "000001", "ex_date": D_CUR.strftime("%Y-%m-%d"), "amount_per_share": 1.0})
    assert r.status_code == 422
    assert r.json()["detail"] == "组合内不存在该标的"

    r = client.post("/api/portfolios/9999/dividends/manual",
                    json={"code": "600900", "ex_date": D_CUR.strftime("%Y-%m-%d"), "amount_per_share": 1.0})
    assert r.status_code == 404
    assert r.json()["detail"] == "Portfolio not found"

    r = client.post(url, json={"code": "600900", "ex_date": D_CUR.strftime("%Y-%m-%d"), "amount_per_share": 1.0, "status": "unknown"})
    assert r.status_code == 422


def test_manual_amount_must_be_positive(client, db_session, dividends):
    """12. 每股金额 ≤ 0 / 日期格式错 → 422"""
    _asset(db_session, PID_EMPTY, "600900", quantity=1000)
    url = f"/api/portfolios/{PID_EMPTY}/dividends/manual"
    for bad in (0, -1, -0.5):
        r = client.post(url, json={"code": "600900", "ex_date": D_CUR.strftime("%Y-%m-%d"), "amount_per_share": bad})
        assert r.status_code == 422, f"amount={bad} 应为 422"

    r = client.post(url, json={"code": "600900", "ex_date": "not-a-date", "amount_per_share": 1.0})
    assert r.status_code == 422


def test_delete_manual(client, db_session, dividends):
    """13. 删除补录 → 该笔消失；重复删 / 跨组合删 → 404"""
    dividends["600900"] = [_rec(D_CUR, 0.5)]
    _asset(db_session, PID_EMPTY, "600900", quantity=1000)
    created = client.post(
        f"/api/portfolios/{PID_EMPTY}/dividends/manual",
        json={"code": "600900", "ex_date": D_CUR.strftime("%Y-%m-%d"), "amount_per_share": 0.9},
    ).json()

    # 覆盖中：900
    assert _slot(_calendar(client), D_CUR)["total_amount"] == pytest.approx(900)

    r = client.delete(f"/api/portfolios/{PID_EMPTY}/dividends/manual/{created['id']}")
    assert r.status_code == 200
    # 删掉后回落到 akshare 的 500
    assert _slot(_calendar(client), D_CUR)["total_amount"] == pytest.approx(500)
    assert client.get(f"/api/portfolios/{PID_EMPTY}/dividends/manual").json() == []

    r = client.delete(f"/api/portfolios/{PID_EMPTY}/dividends/manual/{created['id']}")
    assert r.status_code == 404
    assert r.json()["detail"] == "补录记录不存在"

    # 跨组合删除（用另一个组合的 pid 去删）→ 404，不泄露存在性
    other = client.post(
        f"/api/portfolios/{PID_EMPTY}/dividends/manual",
        json={"code": "600900", "ex_date": D_CUR.strftime("%Y-%m-%d"), "amount_per_share": 0.3},
    ).json()
    r = client.delete(f"/api/portfolios/{PID_OTHER}/dividends/manual/{other['id']}")
    assert r.status_code == 404
    assert r.json()["detail"] == "补录记录不存在"
    # 记录仍在
    assert len(client.get(f"/api/portfolios/{PID_EMPTY}/dividends/manual").json()) == 1


def test_cross_portfolio_isolation(client, db_session, dividends):
    """14. 组合 A 的确认 / 补录不影响组合 B（含 code 不在 B 时的 404）"""
    dividends["600900"] = [_rec(D_CUR, 0.5, status="已发布")]
    dividends["600519"] = [_rec(D_CUR, 1.0, status="已发布")]
    _asset(db_session, PID_EMPTY, "600900", quantity=1000)   # A：500
    _asset(db_session, PID_OTHER, "600519", quantity=100)    # B：100

    client.post(
        f"/api/portfolios/{PID_EMPTY}/dividends/confirm",
        json={"code": "600900", "ex_date": D_CUR.strftime("%Y-%m-%d")},
    )
    client.post(
        f"/api/portfolios/{PID_EMPTY}/dividends/manual",
        json={"code": "600900", "ex_date": D_CUR.strftime("%Y-%m-%d"), "amount_per_share": 0.9},
    )

    a = _slot(_calendar(client, pid=PID_EMPTY), D_CUR)
    b = _slot(_calendar(client, pid=PID_OTHER), D_CUR)
    assert a["total_amount"] == pytest.approx(900)
    assert a["by_source"]["manual"] == pytest.approx(900)
    assert b["total_amount"] == pytest.approx(100)
    assert b["by_status"]["announced"] == pytest.approx(100)
    assert b["by_source"]["manual"] == 0

    # B 里没有 600900 这个标的 → 确认返回 404
    r = client.post(
        f"/api/portfolios/{PID_OTHER}/dividends/confirm",
        json={"code": "600900", "ex_date": D_CUR.strftime("%Y-%m-%d")},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "找不到该笔分红记录"


# ---------- 15 ~ 19：边界与回归 ----------

def test_akshare_failure_does_not_break_calendar(client, db_session, monkeypatch):
    """15. akshare 抛异常且无缓存 → 该资产跳过，calendar 仍 200"""
    _asset(db_session, PID_EMPTY, "600900", quantity=1000)

    async def boom(stock_code, *args, **kwargs):
        raise RuntimeError("network down")

    import routers.stocks as stocks_mod
    monkeypatch.setattr(stocks_mod, "fetch_dividend_from_eastmoney", boom)

    body = _calendar(client)
    assert body["total_year"] == 0
    assert body["total_count"] == 0
    assert body["asset_count"] == 1        # 资产还在（只是没数据）
    assert all(m["total_amount"] == 0 for m in body["months"])

    month = _month(client)
    assert month["count"] == 0
    assert month["items"] == []


def test_zero_quantity_asset(client, db_session, dividends):
    """16. quantity=0 → 仍出现在 items，但金额 0、不影响合计"""
    dividends["600900"] = [_rec(D_CUR, 0.5)]
    _asset(db_session, PID_EMPTY, "600900", quantity=0)

    slot = _slot(_calendar(client), D_CUR)
    assert slot["count"] == 1
    assert slot["total_amount"] == 0
    assert slot["by_source"]["stock"] == 0

    items = _month(client)["items"]
    assert len(items) == 1
    assert items[0]["quantity"] == 0
    assert items[0]["amount"] == 0
    assert items[0]["dividend_per_share"] == pytest.approx(0.5)


def test_mock_price_flag(client, db_session, dividends):
    """17. price_source=mock → 月明细带 is_mock_price（金额口径不受影响）"""
    dividends["600900"] = [_rec(D_CUR, 0.5)]
    dividends["600519"] = [_rec(D_CUR, 0.5)]
    _asset(db_session, PID_EMPTY, "600900", quantity=1000, price_source="mock")
    _asset(db_session, PID_EMPTY, "600519", quantity=1000, price_source="real")

    items = {i["code"]: i for i in _month(client)["items"]}
    assert items["600900"]["is_mock_price"] is True
    assert items["600519"]["is_mock_price"] is False
    # 金额与价格来源无关
    assert items["600900"]["amount"] == pytest.approx(500)
    assert _slot(_calendar(client), D_CUR)["total_amount"] == pytest.approx(1000)


def test_cost_price_does_not_affect_calendar(client, db_session, dividends):
    """18. 负成本 / NULL 成本完全不影响日历（回归锁定）"""
    dividends["600900"] = [_rec(D_CUR, 0.5)]
    asset = _asset(db_session, PID_EMPTY, "600900", quantity=1000, cost_price=None)

    baseline = _calendar(client)

    asset.cost_price = -5.0
    db_session.commit()
    assert _calendar(client) == baseline

    asset.cost_price = 0.0
    db_session.commit()
    assert _calendar(client) == baseline


def test_confirmation_unique_constraint_and_idempotency(client, db_session, dividends):
    """19. (portfolio_id, code, ex_date) 唯一约束生效；confirm API 永远幂等"""
    dividends["600900"] = [_rec(D_CUR, 0.5)]
    _asset(db_session, PID_EMPTY, "600900", quantity=1000)

    # 直接绕过 API 插两条同 key → 数据库唯一约束必须拦住
    db_session.add(DividendConfirmation(portfolio_id=PID_EMPTY, code="600900", ex_date=D_CUR))
    db_session.commit()
    db_session.add(DividendConfirmation(portfolio_id=PID_EMPTY, code="600900", ex_date=D_CUR))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # 约束被撞过之后，API 依旧幂等（不会因为已有记录而报错）
    payload = {"code": "600900", "ex_date": D_CUR.strftime("%Y-%m-%d")}
    r1 = client.post(f"/api/portfolios/{PID_EMPTY}/dividends/confirm", json=payload)
    r2 = client.post(f"/api/portfolios/{PID_EMPTY}/dividends/confirm", json=payload)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["confirmed_at"] == r2.json()["confirmed_at"]

    db_session.expire_all()
    assert db_session.query(DividendConfirmation).filter(
        DividendConfirmation.portfolio_id == PID_EMPTY
    ).count() == 1
    # 手动补录表同样按组合隔离，只有真正写入的才算
    assert db_session.query(ManualDividendRecord).count() == 0


# ---------- 20：去年同期预估（用户反馈「未来月份没有预计收入」） ----------

def test_calendar_estimates_from_last_year(client, db_session, dividends):
    """akshare 只收录已发生的分红，未来月份用「去年同期」投影成预估项。

    锁定三件事：
      ① 未来月份生成 is_estimated=True 的预估项（status=predicted），且计入该月合计与 estimated_total
      ② 真实记录优先：该标的该月已有真实分红 → 不生成预估
      ③ 预估项不算「已到账」：rolling_12m_total 不受影响；confirm 也对它 404（前端不给按钮）
    """
    # 「下一个未来月份」（今天是当月，故 next_month 一定晚于今天）→ 去年同期某天
    next_month = TODAY.month + 1 if TODAY.month < 12 else 1
    next_year = TODAY.year if TODAY.month < 12 else TODAY.year + 1
    last_year_same_month = date(TODAY.year - 1, next_month, 15)
    target = date(next_year, next_month, 15)
    assert target > TODAY, "构造的月份必须在未来"

    # 去年同期那一笔（落在滚动 12 个月窗口内）
    dividends["600900"] = [_rec(last_year_same_month, 0.25)]
    _asset(db_session, PID_EMPTY, "600900", quantity=1000)

    body = _calendar(client)
    slot = _slot(body, target)
    assert slot["count"] == 1
    assert slot["total_amount"] == pytest.approx(250)
    assert slot["estimated_amount"] == pytest.approx(250)
    assert slot["by_status"]["predicted"] == pytest.approx(250)
    assert body["estimated_total"] == pytest.approx(250)
    assert body["recorded_total"] == 0
    assert body["total_year"] == pytest.approx(250)
    # 滚动 12 个月只认「已到账」的真实记录：去年同月那笔 250 算，预估出来的那笔不算
    # （否则同一笔会被算两次 —— 这条断言正好证明预估不污染已到账口径）
    assert body["rolling_12m_total"] == pytest.approx(250)
    assert body["rolling_12m_count"] == 1

    items = _month(client, year=target.year, month=target.month)["items"]
    assert len(items) == 1
    assert items[0]["is_estimated"] is True
    assert items[0]["status"] == "predicted"
    assert items[0]["amount"] == pytest.approx(250)
    assert items[0]["ex_date"] == target.strftime("%Y-%m-%d")

    # ② 真实记录优先：该标的该月已经有真实分红 → 不再预估
    dividends["600900"] = [_rec(last_year_same_month, 0.25), _rec(target, 0.3)]
    body = _calendar(client)
    slot = _slot(body, target)
    assert slot["count"] == 1                      # 只有真实那一笔，没有叠加预估
    assert slot["estimated_amount"] == 0
    assert slot["total_amount"] == pytest.approx(300)
    assert slot["by_status"]["paid"] == pytest.approx(300)
    assert body["estimated_total"] == 0

    # ③ 预估项不能「点击确认」（前端也不给按钮）：它不是一笔真实记录
    dividends["600900"] = [_rec(last_year_same_month, 0.25)]
    r = client.post(
        f"/api/portfolios/{PID_EMPTY}/dividends/confirm",
        json={"code": "600900", "ex_date": target.strftime("%Y-%m-%d")},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "找不到该笔分红记录"
