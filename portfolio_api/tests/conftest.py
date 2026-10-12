"""测试配置：用临时 SQLite，避免污染 dev DB。
所有路由的外部数据源（akshare / 腾讯行情）通过 monkeypatch 跳过。
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 让 tests/ 目录能 import 顶层模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 在 conftest 阶段就 patch 掉 settings.database_url，避免 import 时锁死
# 用 env var 注入，pydantic-settings 会自动读取
_db_tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_db_tmpfile.close()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_tmpfile.name}")

# 顶层 import（必须在设 env var 之后，但 conftest 加载在 import test module 之前）
from config import settings  # noqa: E402
from database import engine, SessionLocal, Base  # noqa: E402
from main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_db():
    """每个测试前清表 + 重新种子，保证隔离。"""
    # 删所有表重建
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    # 跑一次种子
    from main import init_sample_data
    init_sample_data()
    # 清分红缓存
    from routers.stocks import invalidate_dividend_cache
    invalidate_dividend_cache()
    yield
    # 测试后清理分红缓存
    invalidate_dividend_cache()


@pytest.fixture
def client(_reset_db):
    """FastAPI TestClient。"""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db_session(_reset_db):
    """直接拿 session 的 fixture（不走 HTTP）。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def mock_tencent_ok(monkeypatch):
    """让 fetch_price_from_tencent 返回固定成功结果。
    routers.assets 在 import 时把函数绑到本地名字，需要双 patch。
    """
    async def fake_fetch(code, dividend_per_share=0):
        return {
            "success": True,
            "name": f"测试股{code}",
            "price": 10.0,
            "yesterday_close": 9.5,
            "market_code": f"sh{code}",
            "ttm_yield": 5.0,
            "source": "real",
        }
    import routers.stocks as stocks_mod
    import routers.assets as assets_mod
    monkeypatch.setattr(stocks_mod, "fetch_price_from_tencent", fake_fetch)
    monkeypatch.setattr(assets_mod, "fetch_real_time_price", fake_fetch)
    return fake_fetch


@pytest.fixture
def mock_dividend(monkeypatch):
    """让 fetch_dividend_from_eastmoney 返回固定分红（并写缓存，模拟真实行为）。"""
    from datetime import date
    import time as _time
    async def fake_fetch(code):
        data = [
            {"EX_DIVIDEND_DATE": date(2025, 6, 15), "DIVIDEND_PER_SHARE": 0.5,
             "BONUS_IT_RATIO": 0, "TRANSFER_RATIO": 0,
             "IMPLEMENT_STATUS": "已实施", "REPORT_TIME": ""},
            {"EX_DIVIDEND_DATE": date(2024, 6, 15), "DIVIDEND_PER_SHARE": 0.4,
             "BONUS_IT_RATIO": 0, "TRANSFER_RATIO": 0,
             "IMPLEMENT_STATUS": "已实施", "REPORT_TIME": ""},
        ]
        # 写缓存以模拟真实函数的行为
        import routers.stocks as stocks_mod
        with stocks_mod._dividend_cache_lock:
            stocks_mod._dividend_cache[code] = (_time.time(), data)
        return data
    import routers.stocks as stocks_mod
    import routers.assets as assets_mod
    monkeypatch.setattr(stocks_mod, "fetch_dividend_from_eastmoney", fake_fetch)
    monkeypatch.setattr(assets_mod, "fetch_dividend_from_eastmoney", fake_fetch)
    return fake_fetch


def pytest_sessionfinish(session, exitstatus):
    """测试结束后删临时 db。"""
    try:
        os.unlink(_db_tmpfile.name)
    except OSError:
        pass