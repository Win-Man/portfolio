"""每股成本（cost_price）与盈亏相关测试。

需求
    - 资产记录「每股成本」，人为填写
    - 新增时未填 → 默认等于当前价（盈亏为 0）
    - 成本是历史事实：刷新价格**不得**改写它

注意：成本/盈亏的**颜色**是 A 股语义（红涨绿跌），属前端表现，不在此测。
"""
import pytest


# ---------- 新增时的默认值 ----------

def test_create_asset_without_cost_defaults_to_price(client):
    """未填成本 → 默认等于当前价，盈亏为 0。"""
    r = client.post("/api/portfolios/1/assets", json={
        "name": "测试标的", "code": "600900", "quantity": 100, "price": 25.0,
    })
    assert r.status_code == 200, r.text
    b = r.json()

    assert b["cost_price"] == pytest.approx(25.0)
    assert b["has_cost"] is True
    assert b["cost_value"] == pytest.approx(2500.0)
    assert b["profit_amount"] == pytest.approx(0.0)
    assert b["profit_percent"] == pytest.approx(0.0)


def test_create_asset_with_explicit_cost_is_respected(client):
    """显式填了成本就尊重，不被价格覆盖。"""
    r = client.post("/api/portfolios/1/assets", json={
        "name": "测试标的", "code": "600900",
        "quantity": 100, "price": 30.0, "cost_price": 20.0,
    })
    assert r.status_code == 200, r.text
    b = r.json()

    assert b["cost_price"] == pytest.approx(20.0)
    assert b["profit_amount"] == pytest.approx((30.0 - 20.0) * 100)
    assert b["profit_percent"] == pytest.approx(50.0)


def test_create_asset_with_omitted_cost_key(client):
    """请求里完全不传 cost_price 键 → 用价格兜底。"""
    r = client.post("/api/portfolios/1/assets", json={
        "name": "测试标的", "code": "600900", "quantity": 100, "price": 16.0,
    })
    assert r.status_code == 200, r.text
    assert r.json()["cost_price"] == pytest.approx(16.0)


def test_create_asset_with_null_cost_falls_back_to_price(client):
    """成本传 null 同样用价格兜底（前端清空输入框的场景）。"""
    r = client.post("/api/portfolios/1/assets", json={
        "name": "测试标的", "code": "600900",
        "quantity": 100, "price": 12.0, "cost_price": None,
    })
    assert r.status_code == 200, r.text
    assert r.json()["cost_price"] == pytest.approx(12.0)


# ---------- 盈亏计算 ----------

def test_profit_amount_and_percent_math(client):
    r = client.post("/api/portfolios/1/assets", json={
        "name": "盈利标的", "code": "600900",
        "quantity": 200, "price": 29.15, "cost_price": 25.0,
    })
    b = r.json()
    # (29.15 − 25.00) × 200 = 830
    assert b["profit_amount"] == pytest.approx(830.0, abs=0.01)
    assert b["profit_percent"] == pytest.approx(16.6, abs=0.01)
    assert b["cost_value"] == pytest.approx(5000.0)


def test_loss_is_negative(client):
    r = client.post("/api/portfolios/1/assets", json={
        "name": "亏损标的", "code": "600900",
        "quantity": 100, "price": 8.0, "cost_price": 10.0,
    })
    b = r.json()
    assert b["profit_amount"] == pytest.approx(-200.0)
    assert b["profit_percent"] == pytest.approx(-20.0)


def test_break_even(client):
    r = client.post("/api/portfolios/1/assets", json={
        "name": "持平标的", "code": "600900",
        "quantity": 100, "price": 10.0, "cost_price": 10.0,
    })
    b = r.json()
    assert b["profit_amount"] == pytest.approx(0.0)
    assert b["profit_percent"] == pytest.approx(0.0)


def test_etf_cost_works_too(client):
    """ETF 同样支持成本与盈亏。"""
    r = client.post("/api/portfolios/1/assets", json={
        "name": "央企红利ETF", "code": "561580",
        "quantity": 10000, "price": 1.286, "cost_price": 1.10,
    })
    b = r.json()
    assert b["asset_type"] == "ETF"
    assert b["profit_amount"] == pytest.approx((1.286 - 1.10) * 10000, abs=0.01)


# ---------- 更新 ----------

def test_update_cost_recomputes_profit(client):
    r = client.post("/api/portfolios/1/assets", json={
        "name": "测试标的", "code": "600900",
        "quantity": 100, "price": 30.0, "cost_price": 30.0,
    })
    aid = r.json()["id"]
    assert r.json()["profit_percent"] == pytest.approx(0.0)

    r = client.put(f"/api/assets/{aid}", json={"cost_price": 20.0})
    assert r.status_code == 200
    b = r.json()
    assert b["cost_price"] == pytest.approx(20.0)
    assert b["profit_percent"] == pytest.approx(50.0)
    # 未传的字段不应被动到
    assert b["name"] == "测试标的"
    assert b["price"] == pytest.approx(30.0)


def test_update_without_cost_keeps_existing(client):
    """PUT 不传 cost_price 时，已有成本应保留（exclude_unset）。"""
    r = client.post("/api/portfolios/1/assets", json={
        "name": "测试标的", "code": "600900",
        "quantity": 100, "price": 30.0, "cost_price": 21.5,
    })
    aid = r.json()["id"]

    r = client.put(f"/api/assets/{aid}", json={"note": "只改备注"})
    assert r.status_code == 200
    assert r.json()["cost_price"] == pytest.approx(21.5)


# ---------- ★ 成本是历史事实：刷新不得改写 ----------

def test_refresh_price_does_not_touch_cost(client, mock_tencent_ok, mock_dividend):
    """★ 单只刷新价格后，成本必须原样保留。"""
    r = client.post("/api/portfolios/1/assets", json={
        "name": "测试标的", "code": "600900",
        "quantity": 100, "price": 30.0, "cost_price": 22.0,
    })
    aid = r.json()["id"]

    r = client.post(f"/api/assets/{aid}/refresh-price")
    assert r.status_code == 200, r.text
    b = r.json()

    assert b["price"] == pytest.approx(10.0)          # mock_tencent_ok 固定 10.0
    assert b["cost_price"] == pytest.approx(22.0), "刷新不得改写成本"
    # 盈亏应随新价格重算
    assert b["profit_amount"] == pytest.approx((10.0 - 22.0) * 100)


def test_bulk_refresh_does_not_touch_cost(client, mock_tencent_ok, mock_dividend):
    """★ 批量刷新同样不得改写成本。"""
    r = client.post("/api/portfolios/1/assets", json={
        "name": "测试标的", "code": "600900",
        "quantity": 100, "price": 30.0, "cost_price": 22.0,
    })
    aid = r.json()["id"]

    r = client.post("/api/portfolios/1/assets/refresh-all-prices")
    assert r.status_code == 200

    r = client.get(f"/api/assets/{aid}")
    b = r.json()
    assert b["cost_price"] == pytest.approx(22.0), "批量刷新不得改写成本"
    assert b["profit_amount"] == pytest.approx((10.0 - 22.0) * 100)


# ---------- ★ 负成本（分红/高抛低吸后摊薄成本转负）----------

def test_negative_cost_is_a_valid_value(client):
    """★ 负成本必须被当作「已填」，而不是「未填」。"""
    r = client.post("/api/portfolios/1/assets", json={
        "name": "招商银行", "code": "600036",
        "quantity": 100, "price": 42.80, "cost_price": -4.891,
    })
    assert r.status_code == 200, r.text
    b = r.json()

    assert b["cost_price"] == pytest.approx(-4.891)
    assert b["has_cost"] is True, "负成本是合法值，不能判定为未填"


def test_negative_cost_computes_amount_but_suppresses_percent(client):
    """负成本：金额照算，比例置 null（(42.8+4.891)/−4.891 = −975%，会误导）。"""
    r = client.post("/api/portfolios/1/assets", json={
        "name": "招商银行", "code": "600036",
        "quantity": 100, "price": 42.80, "cost_price": -4.891,
    })
    b = r.json()

    # 金额： (42.80 − (−4.891)) × 100 = 4769.10
    assert b["profit_amount"] == pytest.approx(4769.10, abs=0.01)
    # 持仓成本为负：表示净投入已收回
    assert b["cost_value"] == pytest.approx(-489.10, abs=0.01)
    # 比例无意义 → null
    assert b["profit_percent"] is None


def test_zero_cost_is_valid_but_percent_is_null(client):
    """显式填 0 成本（股份已全部回本）：不算「未填」，但比例置 null（除零）。"""
    r = client.post("/api/portfolios/1/assets", json={
        "name": "零成本", "code": "600900",
        "quantity": 100, "price": 20.0, "cost_price": 0,
    })
    assert r.status_code == 200, r.text
    b = r.json()

    assert b["cost_price"] == pytest.approx(0.0), "显式填 0 不应被价格覆盖"
    assert b["has_cost"] is True
    assert b["profit_amount"] == pytest.approx(2000.0)
    assert b["profit_percent"] is None


def test_negative_cost_on_etf(client):
    r = client.post("/api/portfolios/1/assets", json={
        "name": "红利ETF", "code": "561580",
        "quantity": 10000, "price": 1.286, "cost_price": -0.35,
    })
    b = r.json()
    assert b["asset_type"] == "ETF"
    assert b["has_cost"] is True
    assert b["profit_amount"] == pytest.approx((1.286 + 0.35) * 10000, abs=0.01)
    assert b["profit_percent"] is None


def test_refresh_preserves_negative_cost(client, mock_tencent_ok, mock_dividend):
    """刷新不得把负成本改写掉（成本是历史事实）。"""
    r = client.post("/api/portfolios/1/assets", json={
        "name": "招商银行", "code": "600036",
        "quantity": 100, "price": 42.80, "cost_price": -4.891,
    })
    aid = r.json()["id"]

    client.post(f"/api/assets/{aid}/refresh-price")
    b = client.get(f"/api/assets/{aid}").json()

    assert b["cost_price"] == pytest.approx(-4.891)
    assert b["has_cost"] is True
    assert b["profit_percent"] is None


def test_summary_with_negative_cost(client):
    """负成本参与汇总，且总成本≤0 时比例置 null。"""
    pid = client.post("/api/portfolios", json={"name": "负成本组合"}).json()["id"]

    # A：成本 −4.891，现价 42.80，100 股 → 盈亏 +4769.10，成本 −489.10
    client.post(f"/api/portfolios/{pid}/assets", json={
        "name": "A", "code": "600036", "quantity": 100, "price": 42.80, "cost_price": -4.891,
    })
    # B：成本 10，现价 20，100 股 → 盈亏 +1000，成本 1000
    client.post(f"/api/portfolios/{pid}/assets", json={
        "name": "B", "code": "600900", "quantity": 100, "price": 20.0, "cost_price": 10.0,
    })

    s = client.get(f"/api/portfolios/{pid}/summary").json()

    assert s["costed_asset_count"] == 2
    assert s["total_cost"] == pytest.approx(-489.10 + 1000.0, abs=0.01)
    assert s["total_profit"] == pytest.approx(4769.10 + 1000.0, abs=0.01)
    # 总成本 510.90 > 0 → 比例可算
    assert s["total_profit_percent"] == pytest.approx(
        (5769.10 / 510.90) * 100, abs=0.01
    )


def test_summary_percent_null_when_total_cost_non_positive(client):
    """总成本 ≤ 0 时总盈亏比例应为 null，而不是误导性的负数。"""
    pid = client.post("/api/portfolios", json={"name": "全负成本组合"}).json()["id"]

    client.post(f"/api/portfolios/{pid}/assets", json={
        "name": "A", "code": "600036", "quantity": 100, "price": 42.80, "cost_price": -4.891,
    })

    s = client.get(f"/api/portfolios/{pid}/summary").json()

    assert s["total_cost"] == pytest.approx(-489.10, abs=0.01)
    assert s["total_profit"] == pytest.approx(4769.10, abs=0.01)
    assert s["total_profit_percent"] is None


def test_summary_skips_null_cost_but_keeps_negative(client):
    """NULL 成本跳过；负成本保留。"""
    from models import Asset
    from database import SessionLocal

    pid = client.post("/api/portfolios", json={"name": "混合"}).json()["id"]
    client.post(f"/api/portfolios/{pid}/assets", json={
        "name": "负成本", "code": "600036",
        "quantity": 100, "price": 42.80, "cost_price": -4.891,
    })
    r = client.post(f"/api/portfolios/{pid}/assets", json={
        "name": "置空", "code": "600900",
        "quantity": 100, "price": 20.0, "cost_price": 10.0,
    })
    null_id = r.json()["id"]

    db = SessionLocal()
    try:
        a = db.query(Asset).filter(Asset.id == null_id).first()
        a.cost_price = None
        db.commit()
    finally:
        db.close()

    s = client.get(f"/api/portfolios/{pid}/summary").json()
    assert s["costed_asset_count"] == 1, "只应统计负成本那条"
    assert s["total_cost"] == pytest.approx(-489.10, abs=0.01)


# ---------- 组合汇总 ----------

def test_summary_includes_cost_and_profit(client):
    """汇总只统计已填成本的标的。"""
    pid = client.post("/api/portfolios", json={"name": "成本测试组合"}).json()["id"]

    # A：成本 20，现价 30，100 股 → 盈亏 +1000
    client.post(f"/api/portfolios/{pid}/assets", json={
        "name": "A", "code": "600900", "quantity": 100, "price": 30.0, "cost_price": 20.0,
    })
    # B：成本 50，现价 40，100 股 → 盈亏 −1000
    client.post(f"/api/portfolios/{pid}/assets", json={
        "name": "B", "code": "600036", "quantity": 100, "price": 40.0, "cost_price": 50.0,
    })

    r = client.get(f"/api/portfolios/{pid}/summary")
    assert r.status_code == 200
    s = r.json()

    assert s["asset_count"] == 2
    assert s["total_market_value"] == pytest.approx(7000.0)   # 30*100 + 40*100
    assert s["total_cost"] == pytest.approx(7000.0)           # 20*100 + 50*100
    assert s["total_profit"] == pytest.approx(0.0)            # 盈亏相抵
    assert s["costed_asset_count"] == 2


def test_summary_skips_assets_without_cost(client):
    """未填成本的标的（cost_price 为 NULL）不应被当成零成本拉高盈亏。"""
    from models import Asset
    from database import SessionLocal

    pid = client.post("/api/portfolios", json={"name": "混合组合"}).json()["id"]
    client.post(f"/api/portfolios/{pid}/assets", json={
        "name": "有成本", "code": "600900",
        "quantity": 100, "price": 30.0, "cost_price": 20.0,
    })
    r = client.post(f"/api/portfolios/{pid}/assets", json={
        "name": "无成本", "code": "600036",
        "quantity": 100, "price": 50.0, "cost_price": 50.0,
    })
    aid = r.json()["id"]

    # 手动把成本置空，模拟历史遗留数据
    db = SessionLocal()
    try:
        a = db.query(Asset).filter(Asset.id == aid).first()
        a.cost_price = None
        db.commit()
    finally:
        db.close()

    s = client.get(f"/api/portfolios/{pid}/summary").json()

    assert s["asset_count"] == 2
    assert s["costed_asset_count"] == 1
    assert s["total_cost"] == pytest.approx(2000.0)        # 只算有成本那条
    assert s["total_profit"] == pytest.approx(1000.0)      # (30−20)*100
    assert s["total_profit_percent"] == pytest.approx(50.0)

    # 该资产在列表里应标记为「无成本」，盈亏字段为 0
    assets = client.get(f"/api/portfolios/{pid}/assets").json()
    no_cost = [a for a in assets if a["id"] == aid][0]
    assert no_cost["has_cost"] is False
    assert no_cost["cost_price"] is None
    assert no_cost["profit_amount"] == pytest.approx(0.0)
    # 未填成本时比例也不适用
    assert no_cost["profit_percent"] is None


def test_summary_no_cost_at_all(client):
    """全都没成本时，总盈亏为 0、比例为 null 而不是报错。"""
    pid = client.post("/api/portfolios", json={"name": "空组合"}).json()["id"]
    s = client.get(f"/api/portfolios/{pid}/summary").json()
    assert s["total_cost"] == 0
    assert s["total_profit"] == 0
    assert s["total_profit_percent"] is None
    assert s["costed_asset_count"] == 0
