"""年收入目标（income goal）：CRUD / 校验 / 归属 / 级联 / 进度计算。

对应 docs/specs/001-income-goal.md §11.1 的 19 个用例。
所有断言锁的是 spec 里已定死的口径，改动实现时这里应当红。
"""
import pytest

from models import Asset, Goal

# 种子数据（main.SAMPLE_PORTFOLIOS）：
#   pid=1 投资组合（3 个标的）、pid=2 养老金（2 个标的）、pid=3 备用金（无标的）
PID_WITH_ASSETS = 1
PID_OTHER = 2
PID_EMPTY = 3


def _create_goal(client, pid, name, target, expect=201):
    r = client.post(
        f"/api/portfolios/{pid}/goals",
        json={"name": name, "annual_income_target": target},
    )
    assert r.status_code == expect, r.text
    return r.json()


def _add_asset(
    db_session,
    pid,
    *,
    name="标的",
    code="000001",
    quantity=1000.0,
    price=10.0,
    dividend_per_share=1.2,
    ttm_yield=0.0,
    cost_price=None,
    price_source="real",
):
    asset = Asset(
        portfolio_id=pid,
        name=name,
        code=code,
        asset_type="股票",
        quantity=quantity,
        price=price,
        dividend_per_share=dividend_per_share,
        ttm_yield=ttm_yield,
        cost_price=cost_price,
        price_source=price_source,
    )
    db_session.add(asset)
    db_session.commit()
    db_session.refresh(asset)
    return asset


# ---------- 1 / 2 ----------

def test_get_goals_empty_list(client):
    """1. 空列表返回 []"""
    r = client.get(f"/api/portfolios/{PID_WITH_ASSETS}/goals")
    assert r.status_code == 200
    assert r.json() == []


def test_create_goal_and_list(client):
    """2. POST 201 + 字段回显；GET 列表能查到"""
    body = _create_goal(client, PID_WITH_ASSETS, "生活自由线", 120000)
    assert body["id"] > 0
    assert body["portfolio_id"] == PID_WITH_ASSETS
    assert body["name"] == "生活自由线"
    assert body["annual_income_target"] == 120000
    assert body["created_at"] and body["updated_at"]

    r = client.get(f"/api/portfolios/{PID_WITH_ASSETS}/goals")
    assert r.status_code == 200
    goals = r.json()
    assert len(goals) == 1
    assert goals[0]["id"] == body["id"]
    assert goals[0]["name"] == "生活自由线"


def test_create_goal_portfolio_not_found(client):
    """3. POST 到不存在的组合 → 404"""
    r = client.post(
        "/api/portfolios/9999/goals",
        json={"name": "x", "annual_income_target": 1000},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Portfolio not found"

    r = client.get("/api/portfolios/9999/goals")
    assert r.status_code == 404


def test_create_goal_invalid_target(client):
    """4. 目标金额 0 / 负数 → 422"""
    for bad in (0, -1, -120000):
        r = client.post(
            f"/api/portfolios/{PID_WITH_ASSETS}/goals",
            json={"name": "x", "annual_income_target": bad},
        )
        assert r.status_code == 422, f"target={bad} 应为 422"


def test_create_goal_blank_name(client):
    """5. 纯空白名称 → 422「目标名称不能为空」"""
    r = client.post(
        f"/api/portfolios/{PID_WITH_ASSETS}/goals",
        json={"name": "   ", "annual_income_target": 120000},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "目标名称不能为空"


def test_create_goal_duplicate_name(client):
    """6. 同组合重名 → 409；不同组合同名 → 201"""
    _create_goal(client, PID_WITH_ASSETS, "加菜线", 60000)

    # 完全同名
    r = client.post(
        f"/api/portfolios/{PID_WITH_ASSETS}/goals",
        json={"name": "加菜线", "annual_income_target": 80000},
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "同名目标已存在"

    # 去首尾空白后同名也算重名
    r = client.post(
        f"/api/portfolios/{PID_WITH_ASSETS}/goals",
        json={"name": "  加菜线  ", "annual_income_target": 80000},
    )
    assert r.status_code == 409

    # 不同组合允许同名
    body = _create_goal(client, PID_OTHER, "加菜线", 60000)
    assert body["portfolio_id"] == PID_OTHER


# ---------- 7 / 8 ----------

def test_update_goal_partial_and_duplicate(client):
    """7. PUT 改名+改金额；撞同组另一目标名 → 409；只传 name 时金额不变"""
    a = _create_goal(client, PID_WITH_ASSETS, "目标A", 120000)
    b = _create_goal(client, PID_WITH_ASSETS, "目标B", 60000)

    # 改名 + 改金额
    r = client.put(
        f"/api/goals/{a['id']}",
        json={"name": "目标A改", "annual_income_target": 150000},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "目标A改"
    assert r.json()["annual_income_target"] == 150000

    # 改成与同组另一目标同名 → 409
    r = client.put(f"/api/goals/{a['id']}", json={"name": "目标B"})
    assert r.status_code == 409
    assert r.json()["detail"] == "同名目标已存在"

    # 部分更新：只传 name，金额不变
    r = client.put(f"/api/goals/{b['id']}", json={"name": "目标B改"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "目标B改"
    assert r.json()["annual_income_target"] == 60000

    # 改成自己的名字（排除自身）不应 409
    r = client.put(f"/api/goals/{b['id']}", json={"name": "目标B改"})
    assert r.status_code == 200

    # 纯空白名称 → 422
    r = client.put(f"/api/goals/{b['id']}", json={"name": "   "})
    assert r.status_code == 422
    assert r.json()["detail"] == "目标名称不能为空"


def test_update_delete_goal_not_found(client):
    """8. PUT / DELETE 不存在的目标 → 404 Goal not found"""
    r = client.put("/api/goals/99999", json={"name": "x"})
    assert r.status_code == 404
    assert r.json()["detail"] == "Goal not found"

    r = client.delete("/api/goals/99999")
    assert r.status_code == 404
    assert r.json()["detail"] == "Goal not found"


def test_delete_goal_and_cascade_on_portfolio_delete(client, db_session):
    """9. DELETE 目标后列表不含；DELETE 组合后 goals 表无残留（级联）"""
    g = _create_goal(client, PID_WITH_ASSETS, "临时目标", 1000)

    r = client.delete(f"/api/goals/{g['id']}")
    assert r.status_code == 200
    assert r.json() == {"message": "Goal deleted"}
    r = client.get(f"/api/portfolios/{PID_WITH_ASSETS}/goals")
    assert g["id"] not in [x["id"] for x in r.json()]

    # 级联：给组合 1 建两个目标后删除组合
    _create_goal(client, PID_WITH_ASSETS, "级联A", 1000)
    _create_goal(client, PID_WITH_ASSETS, "级联B", 2000)
    assert db_session.query(Goal).filter(Goal.portfolio_id == PID_WITH_ASSETS).count() == 2

    r = client.delete(f"/api/portfolios/{PID_WITH_ASSETS}")
    assert r.status_code == 200
    db_session.expire_all()
    assert db_session.query(Goal).filter(Goal.portfolio_id == PID_WITH_ASSETS).count() == 0


# ---------- 10 ~ 19：进度计算 ----------

def test_progress_basic_calculation(client, db_session):
    """10. 基础计算：市值 10000、年收入 1200、目标 12000"""
    _add_asset(db_session, PID_EMPTY, quantity=1000, price=10.0,
               dividend_per_share=1.2, ttm_yield=0.0)
    g = _create_goal(client, PID_EMPTY, "基础", 12000)

    r = client.get(f"/api/portfolios/{PID_EMPTY}/goals/{g['id']}/progress")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["goal_id"] == g["id"]
    assert d["goal_name"] == "基础"
    assert d["portfolio_id"] == PID_EMPTY

    assert d["current_annual_income"] == pytest.approx(1200)
    assert d["current_monthly_income"] == pytest.approx(100)
    assert d["target_annual_income"] == pytest.approx(12000)
    assert d["target_monthly_income"] == pytest.approx(1000)
    assert d["gap_annual"] == pytest.approx(10800)
    assert d["gap_monthly"] == pytest.approx(900)
    assert d["progress_percent"] == pytest.approx(10)
    assert d["is_achieved"] is False
    assert d["surplus_annual"] == pytest.approx(0)
    assert d["surplus_monthly"] == pytest.approx(0)
    assert d["total_market_value"] == pytest.approx(10000)
    assert d["required_yield_percent"] == pytest.approx(120)
    assert d["current_yield_percent"] == pytest.approx(12)
    assert d["yield_gap_points"] == pytest.approx(108)
    assert d["asset_count"] == 1
    assert d["has_mock_price"] is False


def test_progress_default_assumed_yield_is_weighted_ttm(client, db_session):
    """11. 默认假设收益率 = 组合加权 TTM（两标的验证加权值）"""
    # mv = 100×10 + 100×30 = 4000；加权 TTM = (1000×4 + 3000×6)/4000 = 5.5
    _add_asset(db_session, PID_EMPTY, name="A", code="000001", quantity=100, price=10.0,
               dividend_per_share=1.0, ttm_yield=4.0)
    _add_asset(db_session, PID_EMPTY, name="B", code="000002", quantity=100, price=30.0,
               dividend_per_share=2.0, ttm_yield=6.0)
    # 年收入 = 100 + 200 = 300
    g = _create_goal(client, PID_EMPTY, "加权", 1000)

    r = client.get(f"/api/portfolios/{PID_EMPTY}/goals/{g['id']}/progress")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["asset_count"] == 2
    assert d["total_market_value"] == pytest.approx(4000)
    assert d["weighted_ttm_yield"] == pytest.approx(5.5)
    assert d["assumed_yield_percent"] == pytest.approx(5.5)
    assert d["assumed_yield_is_default"] is True
    assert d["current_annual_income"] == pytest.approx(300)
    assert d["gap_annual"] == pytest.approx(700)
    assert d["additional_capital"] == pytest.approx(700 / (5.5 / 100))
    # D3：所需收益率只用「目标 ÷ 市值」，与加权 TTM 无关
    assert d["required_yield_percent"] == pytest.approx(1000 / 4000 * 100)
    assert d["current_yield_percent"] == pytest.approx(300 / 4000 * 100)
    assert d["yield_gap_points"] == pytest.approx(25 - 7.5)


def test_progress_assumed_yield_override(client, db_session):
    """12. 传 assumed_yield=5 → is_default False，本金按 5% 算"""
    _add_asset(db_session, PID_EMPTY, quantity=1000, price=10.0,
               dividend_per_share=1.2, ttm_yield=9.0)
    g = _create_goal(client, PID_EMPTY, "覆盖", 12000)

    r = client.get(
        f"/api/portfolios/{PID_EMPTY}/goals/{g['id']}/progress",
        params={"assumed_yield": 5},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["assumed_yield_is_default"] is False
    assert d["assumed_yield_percent"] == pytest.approx(5)
    assert d["additional_capital"] == pytest.approx(10800 / 0.05)
    # 默认值来自组合加权 TTM，仍原样返回供对照
    assert d["weighted_ttm_yield"] == pytest.approx(9.0)


def test_progress_assumed_yield_zero_and_out_of_range(client, db_session):
    """13. assumed_yield=0 → additional_capital=None；-1 / 200 → 422"""
    _add_asset(db_session, PID_EMPTY, quantity=1000, price=10.0,
               dividend_per_share=1.2, ttm_yield=3.0)
    g = _create_goal(client, PID_EMPTY, "零收益率", 12000)
    url = f"/api/portfolios/{PID_EMPTY}/goals/{g['id']}/progress"

    r = client.get(url, params={"assumed_yield": 0})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["assumed_yield_is_default"] is False
    assert d["assumed_yield_percent"] == 0
    assert d["additional_capital"] is None
    assert d["calc_note"] == "假设加仓收益率为 0，无法推算所需本金；填写一个大于 0 的收益率"

    assert client.get(url, params={"assumed_yield": -1}).status_code == 422
    assert client.get(url, params={"assumed_yield": 200}).status_code == 422


def test_progress_zero_market_value(client, db_session):
    """14. 市值 0 → 收益率类字段全 None，calc_note 非空；手填 4% 时本金有值"""
    # PID_EMPTY 无任何资产
    g = _create_goal(client, PID_EMPTY, "空组合", 12000)
    url = f"/api/portfolios/{PID_EMPTY}/goals/{g['id']}/progress"

    r = client.get(url)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total_market_value"] == 0
    assert d["asset_count"] == 0
    assert d["current_annual_income"] == 0
    assert d["required_yield_percent"] is None
    assert d["current_yield_percent"] is None
    assert d["yield_gap_points"] is None
    assert d["assumed_yield_percent"] == 0
    assert d["assumed_yield_is_default"] is True
    assert d["additional_capital"] is None
    assert d["calc_note"] != ""

    # 手填 4% → 仍能算出所需本金
    r = client.get(url, params={"assumed_yield": 4})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["required_yield_percent"] is None
    assert d["additional_capital"] == pytest.approx(12000 / 0.04)


def test_progress_achieved(client, db_session):
    """15. 已达成：is_achieved True、gap=0、surplus 正确、additional_capital=0、calc_note"""
    # 年收入 1200，目标 1000 → 超额 200
    _add_asset(db_session, PID_EMPTY, quantity=1000, price=10.0,
               dividend_per_share=1.2, ttm_yield=5.0)
    g = _create_goal(client, PID_EMPTY, "已达成", 1000)

    r = client.get(f"/api/portfolios/{PID_EMPTY}/goals/{g['id']}/progress")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["is_achieved"] is True
    assert d["gap_annual"] == 0
    assert d["gap_monthly"] == 0
    assert d["surplus_annual"] == pytest.approx(200)
    assert d["surplus_monthly"] == pytest.approx(200 / 12)
    assert d["progress_percent"] == pytest.approx(120)
    assert d["additional_capital"] == 0
    assert d["calc_note"] == "目标已达成"

    # 边界：当前正好等于目标（surplus = 0，进度 100）
    g2 = _create_goal(client, PID_EMPTY, "刚好达成", 1200)
    r = client.get(f"/api/portfolios/{PID_EMPTY}/goals/{g2['id']}/progress")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["is_achieved"] is True
    assert d["gap_annual"] == 0
    assert d["surplus_annual"] == 0
    assert d["surplus_monthly"] == 0
    assert d["progress_percent"] == pytest.approx(100)
    assert d["additional_capital"] == 0
    assert d["calc_note"] == "目标已达成"


def test_progress_goal_not_in_portfolio(client):
    """16. 目标不属于该组合 / 目标不存在 → 404 Goal not found"""
    g = _create_goal(client, PID_WITH_ASSETS, "归属", 120000)

    # 拿组合 1 的目标去请求组合 2 的 progress → 404（不返回 400）
    r = client.get(f"/api/portfolios/{PID_OTHER}/goals/{g['id']}/progress")
    assert r.status_code == 404
    assert r.json()["detail"] == "Goal not found"

    # 目标不存在
    r = client.get(f"/api/portfolios/{PID_OTHER}/goals/99999/progress")
    assert r.status_code == 404
    assert r.json()["detail"] == "Goal not found"

    # 组合不存在
    r = client.get(f"/api/portfolios/9999/goals/{g['id']}/progress")
    assert r.status_code == 404
    assert r.json()["detail"] == "Portfolio not found"


def test_progress_has_mock_price(client, db_session):
    """17. 含 price_source=mock → True；全 real → False"""
    _add_asset(db_session, PID_EMPTY, name="真实", code="000001", quantity=100,
               price=10.0, dividend_per_share=1.0, ttm_yield=4.0, price_source="real")
    g = _create_goal(client, PID_EMPTY, "mock 标记", 12000)
    url = f"/api/portfolios/{PID_EMPTY}/goals/{g['id']}/progress"

    assert client.get(url).json()["has_mock_price"] is False

    _add_asset(db_session, PID_EMPTY, name="模拟", code="000002", quantity=100,
               price=10.0, dividend_per_share=1.0, ttm_yield=4.0, price_source="mock")

    r = client.get(url)
    assert r.status_code == 200, r.text
    assert r.json()["has_mock_price"] is True


def test_cost_price_does_not_affect_income_or_progress(client, db_session):
    """18. 成本口径隔离：负成本 / NULL 成本不影响收入与目标计算"""
    asset = _add_asset(db_session, PID_EMPTY, quantity=1000, price=10.0,
                       dividend_per_share=1.2, ttm_yield=5.0, cost_price=None)
    g = _create_goal(client, PID_EMPTY, "成本隔离", 12000)
    url = f"/api/portfolios/{PID_EMPTY}/goals/{g['id']}/progress"

    baseline = client.get(url).json()

    # 负成本是合法值（分红 / 高抛低吸后摊薄成本转负），不应影响收入口径
    asset.cost_price = -5.0
    db_session.commit()
    with_negative = client.get(url).json()
    assert with_negative == baseline

    # 成本为 0 同样不影响
    asset.cost_price = 0.0
    db_session.commit()
    with_zero = client.get(url).json()
    assert with_zero == baseline

    # 收入口径只依赖 quantity / dividend_per_share
    assert baseline["current_annual_income"] == pytest.approx(1000 * 1.2)


def test_summary_regression_locked(client):
    """19. 回归锁定：summary 字段与重构前一致（含市值为 0 时 weighted_ttm_yield == 0.0）"""
    r = client.get(f"/api/portfolios/{PID_WITH_ASSETS}/summary")
    assert r.status_code == 200
    d = r.json()
    # seed：长江电力 1000×28.5 + 中国神华 500×38.2 + 交通银行 2000×7.15
    assert d["total_market_value"] == pytest.approx(28500 + 19100 + 14300)
    assert d["asset_count"] == 3
    assert d["expected_annual_income"] == pytest.approx(820 + 1000 + 710)
    expected_weighted = (28500 * 2.88 + 19100 * 5.24 + 14300 * 4.97) / 61900
    assert d["weighted_ttm_yield"] == pytest.approx(expected_weighted)
    # seed 资产没有成本 → 不计入成本汇总
    assert d["total_cost"] == 0
    assert d["total_profit"] == 0
    assert d["total_profit_percent"] is None
    assert d["costed_asset_count"] == 0

    # 空组合：市值为 0 时 weighted_ttm_yield 必须是 0.0（不是 None）
    r = client.get(f"/api/portfolios/{PID_EMPTY}/summary")
    assert r.status_code == 200
    d = r.json()
    assert d["total_market_value"] == 0
    assert d["asset_count"] == 0
    assert d["weighted_ttm_yield"] == 0.0
    assert d["expected_annual_income"] == 0
    assert d["total_cost"] == 0
    assert d["total_profit_percent"] is None

    # summary.expected_annual_income 必须与 goal progress 的 current_annual_income 完全一致
    g = _create_goal(client, PID_WITH_ASSETS, "口径一致性", 120000)
    progress = client.get(
        f"/api/portfolios/{PID_WITH_ASSETS}/goals/{g['id']}/progress"
    ).json()
    summary = client.get(f"/api/portfolios/{PID_WITH_ASSETS}/summary").json()
    assert progress["current_annual_income"] == summary["expected_annual_income"]
    assert progress["total_market_value"] == summary["total_market_value"]
    assert progress["weighted_ttm_yield"] == summary["weighted_ttm_yield"]
