"""组合管理（新增 / 重命名 / 删除）：名称校验 + 级联删除。

既有的组合 CRUD 断言在 test_api.py 里，这里只补「名称校验」与「删除的副作用」，
不重复既有用例、也不修改任何既有断言。
"""
from models import Asset, Goal, Portfolio


def test_create_portfolio_strips_name(client):
    """新增：名称去首尾空白后落库，返回给前端的就是干净的名字"""
    r = client.post("/api/portfolios", json={"name": "  孩子教育金  "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "孩子教育金"

    names = [p["name"] for p in client.get("/api/portfolios").json()]
    assert "孩子教育金" in names


def test_create_portfolio_blank_name_422(client):
    """新增：名称为空 / 纯空白 → 422（前端表单也会拦，这里锁后端）"""
    for bad in ("", "   ", "\t\n"):
        r = client.post("/api/portfolios", json={"name": bad})
        assert r.status_code == 422, f"name={bad!r} 应为 422"


def test_create_portfolio_name_too_long_422(client):
    """新增：名称超过 50 字 → 422"""
    r = client.post("/api/portfolios", json={"name": "长" * 51})
    assert r.status_code == 422


def test_update_portfolio_name_validation(client):
    """重命名：正常改名成功；纯空白 → 422「组合名称不能为空」；不存在 → 404"""
    pid = client.post("/api/portfolios", json={"name": "改名前"}).json()["id"]

    r = client.put(f"/api/portfolios/{pid}", json={"name": "  改名后  "})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "改名后"

    r = client.put(f"/api/portfolios/{pid}", json={"name": "   "})
    assert r.status_code == 422
    assert r.json()["detail"] == "组合名称不能为空"
    # 校验失败不应改掉原名称
    assert client.get(f"/api/portfolios/{pid}").json()["name"] == "改名后"

    # 部分更新：不传 name 不改名
    r = client.put(f"/api/portfolios/{pid}", json={})
    assert r.status_code == 200
    assert r.json()["name"] == "改名后"

    assert client.put("/api/portfolios/9999", json={"name": "x"}).status_code == 404


def test_delete_portfolio_cascades_assets_and_goals(client, db_session):
    """删除组合：其资产与年收入目标一并删除，不留孤儿行；再查 → 404"""
    pid = client.post("/api/portfolios", json={"name": "待删除组合"}).json()["id"]
    client.post(f"/api/portfolios/{pid}/assets", json={
        "name": "标的", "code": "688999", "quantity": 100, "price": 10.0,
    })
    client.post(f"/api/portfolios/{pid}/goals", json={
        "name": "目标", "annual_income_target": 12000,
    })
    assert db_session.query(Asset).filter(Asset.portfolio_id == pid).count() == 1
    assert db_session.query(Goal).filter(Goal.portfolio_id == pid).count() == 1

    r = client.delete(f"/api/portfolios/{pid}")
    assert r.status_code == 200
    assert r.json() == {"message": "Portfolio deleted"}

    db_session.expire_all()
    assert db_session.query(Asset).filter(Asset.portfolio_id == pid).count() == 0
    assert db_session.query(Goal).filter(Goal.portfolio_id == pid).count() == 0
    assert client.get(f"/api/portfolios/{pid}").status_code == 404

    # 删除不存在的组合 → 404
    assert client.delete("/api/portfolios/9999").status_code == 404


def test_seed_portfolios_unaffected_by_name_validation(client):
    """回归：种子的 3 个组合仍能正常列出（响应模型不受写入约束影响）"""
    data = client.get("/api/portfolios").json()
    assert {p["name"] for p in data} == {"投资组合", "养老金", "备用金"}
    assert db_count() == 3


def db_count():
    from database import SessionLocal
    db = SessionLocal()
    try:
        return db.query(Portfolio).count()
    finally:
        db.close()
