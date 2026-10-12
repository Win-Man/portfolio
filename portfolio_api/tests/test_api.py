"""端到端 API 测试：覆盖核心 CRUD + 刷新 + 缓存 + 校验。"""


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert body["db"] is True
    assert "version" in body


def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "docs" in r.json()


def test_portfolios_seeded(client):
    """启动时应自动种子 3 个示例组合"""
    r = client.get("/api/portfolios")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 3
    names = {p["name"] for p in data}
    assert names == {"投资组合", "养老金", "备用金"}


def test_portfolio_crud(client):
    """组合的完整 CRUD"""
    # create
    r = client.post("/api/portfolios", json={"name": "测试组合"})
    assert r.status_code == 200
    pid = r.json()["id"]

    # read
    r = client.get(f"/api/portfolios/{pid}")
    assert r.status_code == 200
    assert r.json()["name"] == "测试组合"

    # update
    r = client.put(f"/api/portfolios/{pid}", json={"name": "测试组合改"})
    assert r.status_code == 200
    assert r.json()["name"] == "测试组合改"

    # delete
    r = client.delete(f"/api/portfolios/{pid}")
    assert r.status_code == 200

    # 404 after delete
    r = client.get(f"/api/portfolios/{pid}")
    assert r.status_code == 404


def test_portfolio_summary(client):
    """summary 应正确计算总市值 + 资产数"""
    r = client.get("/api/portfolios/1/summary")
    assert r.status_code == 200
    data = r.json()
    # 投资组合 seed 时有 3 条资产
    assert data["asset_count"] == 3
    assert data["total_market_value"] > 0
    assert 0 < data["weighted_ttm_yield"] < 100


def test_create_asset_with_null_dividend(client):
    """回归测试：dividend_per_share/ttm_yield 为 null 时不应 422（Task C2 fix）"""
    r = client.post(
        "/api/portfolios/1/assets",
        json={
            "name": "测试资产",
            "code": "688999",
            "asset_type": "股票",
            "quantity": 100,
            "price": 10.0,
            # dividend_per_share 和 ttm_yield 不传
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dividend_per_share"] == 0
    assert body["ttm_yield"] == 0
    assert body["note"] == ""


def test_create_asset_validation(client):
    """缺必填字段（name/code/quantity/price）应 422"""
    r = client.post("/api/portfolios/1/assets", json={"name": "x"})
    assert r.status_code == 422


def test_asset_update_exclude_unset(client):
    """update 应只更新传了的字段（exclude_unset）"""
    # 先建一条
    r = client.post(
        "/api/portfolios/1/assets",
        json={"name": "orig", "code": "688100", "quantity": 100, "price": 5.0},
    )
    aid = r.json()["id"]
    # 只更新 price
    r = client.put(f"/api/assets/{aid}", json={"price": 8.0})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "orig"  # 未变
    assert body["price"] == 8.0


def test_asset_delete(client):
    r = client.post(
        "/api/portfolios/1/assets",
        json={"name": "tmp", "code": "688101", "quantity": 1, "price": 1.0},
    )
    aid = r.json()["id"]
    r = client.delete(f"/api/assets/{aid}")
    assert r.status_code == 200


def test_get_assets_returns_dividend(client, mock_dividend):
    """GET /assets 应返回 y2025/y2024 字段（来自 mock 的 akshare）"""
    r = client.get("/api/portfolios/1/assets")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 3
    for a in data:
        # mock 分红 y2025=0.5, y2024=0.4
        assert a["y2025_dividend"] == 0.5
        assert a["y2024_dividend"] == 0.4
        # price_source 字段存在
        assert a["price_source"] in ("real", "mock", "stale")
        assert "price_source_label" in a


def test_refresh_price_writes_source(client, mock_tencent_ok, mock_dividend):
    r = client.post("/api/assets/1/refresh-price")
    assert r.status_code == 200, r.text
    body = r.json()
    # 真实价格 + 模拟分红应触发 price_source=real 写入
    assert body["price"] == 10.0
    assert body["price_source"] == "real"
    assert body["price_fetched_at"] is not None
    assert body["dividend_per_share"] == 0.5


def test_refresh_all_concurrent(client, mock_tencent_ok, mock_dividend):
    r = client.post("/api/portfolios/1/assets/refresh-all-prices")
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 3
    for x in results:
        assert x["success"] is True
        assert x["source"] == "real"


def test_dividend_cache_endpoints(client, mock_dividend):
    """调试端点：info + clear"""
    # 触发一次实际访问（mock_dividend 会让 fetch_dividend_from_eastmoney 写缓存）
    client.get("/api/portfolios/1/assets")
    r = client.get("/api/stocks/dividend-cache/info")
    assert r.status_code == 200
    info = r.json()
    assert info["size"] >= 1
    # 清空
    r = client.post("/api/stocks/dividend-cache/clear")
    assert r.status_code == 200
    assert r.json()["size"] == 0


def test_stock_search(client):
    """搜索端点（依赖 akshare 缓存；没缓存时返回空数组）"""
    r = client.get("/api/stocks/search?keyword=600519")
    # 视环境：akshare 缓存可能可用也可能不可用；只验证端点存在且 200
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_get_nonexistent_404(client):
    r = client.get("/api/portfolios/9999")
    assert r.status_code == 404
    r = client.get("/api/portfolios/9999/summary")
    assert r.status_code == 404
    r = client.get("/api/assets/9999")
    assert r.status_code == 404
