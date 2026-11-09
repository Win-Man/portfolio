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

# 观察池每日快照定时任务：测试期间必须关闭 —— 否则 lifespan 会起后台协程，
# 对观察池里的标的发真实网络请求（spec 003 §6.5 / §8-12）。
os.environ.setdefault("WATCHLIST_SNAPSHOT_ENABLED", "false")

# 投资组合价格每日刷新任务：同理关闭，避免后台任务打真实行情接口。
os.environ.setdefault("ASSET_PRICE_REFRESH_ENABLED", "false")

# 两个每日日报任务：同理关闭（组合日报会拉真实行情）。
os.environ.setdefault("DAILY_REPORT_PORTFOLIO_ENABLED", "false")
os.environ.setdefault("DAILY_REPORT_WATCHLIST_ENABLED", "false")

# 日报邮件推送：关闭 —— 否则调度会真的往外发信。
os.environ.setdefault("DAILY_REPORT_MAIL_ENABLED", "false")

# 观察池历史回填：默认关闭 —— 否则「首次刷新」会去拉真实日K。
# 需要验回填的用例自己 monkeypatch settings 打开（见 test_watchlist.py）。
os.environ.setdefault("WATCHLIST_BACKFILL_ENABLED", "false")

# 顶层 import（必须在设 env var 之后，但 conftest 加载在 import test module 之前）
from config import settings  # noqa: E402
from database import engine, SessionLocal, Base  # noqa: E402
from main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _assume_trading_day(request, monkeypatch):
    """测试里默认「每天都是交易日」。

    真实日历既依赖网络又依赖当前日期：周六跑测试，所有调度用例会因为
    「非交易日」全被跳过而红；周一跑又全绿。用例结果不该随日历漂移。

    日历判断本身由 `test_trading_calendar.py` 覆盖，那个文件打了
    `real_trading_calendar` 标记，会跳过本夹具、直接调真实实现。
    """
    if request.node.get_closest_marker("real_trading_calendar"):
        yield
        return

    import trading_calendar as tc

    def fake_day_status(day=None, db=None):
        from datetime import date as _date
        target = day or _date.today()
        return tc.DayStatus(target, True, "交易日", "calendar")

    monkeypatch.setattr(tc, "day_status", fake_day_status)
    monkeypatch.setattr(tc, "is_trading_day", lambda day=None, db=None: True)
    # 连同联网入口一起掐断：lifespan 会预热日历，不拦住的话**每个用例**
    # 都会真的去打一次 akshare（测试要能离线跑、要能在 CI 上不抖）。
    monkeypatch.setattr(tc, "fetch_trade_dates", lambda: [])
    monkeypatch.setattr(tc, "refresh_calendar", lambda db, force=False: None)
    monkeypatch.setattr(tc, "ensure_calendar", lambda db, target: None)
    yield


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
    # 清日K「已补到哪一天」的进程内缓存，避免用例间互相影响
    from watchlist_history import reset_history_cache
    reset_history_cache()
    # 清「今天这个任务试过几次」的内存计数
    from scheduler import reset_attempt_state
    reset_attempt_state()
    # 清交易日历的进程内缓存：drop_all 把 trading_days 清空了，
    # 若缓存还留着上一个用例的「最小/最大日期」，判断会串味
    import trading_calendar
    trading_calendar.reset_cache()
    yield
    # 测试后清理分红缓存
    invalidate_dividend_cache()
    reset_history_cache()
    reset_attempt_state()
    trading_calendar.reset_cache()


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
    """让 fetch_dividend_from_eastmoney 返回固定分红（并写缓存，模拟真实行为）。

    返回四期，覆盖两类口径，且**与系统日期无关**：
      - today-30d 的一期（0.5）   → 落在「滚动 12 个月」窗口内，TTM = 0.5
      - 2025-06-15 的一期（0.5）  → y2025_dividend
      - 2024-06-15 的一期（0.4）  → y2024_dividend

    早期版本只给了写死的 2025/2024 两期：一旦系统日期推进到 2026-10 之后
    这两期都掉出 12 个月窗口，TTM 会算成 0 —— 夹具本身有时间脆弱性。
    """
    from datetime import date, timedelta
    import time as _time
    today = date.today()
    async def fake_fetch(code):
        data = [
            # 30 天前：落在 TTM 窗口内 → TTM += 0.5
            {"EX_DIVIDEND_DATE": today - timedelta(days=30), "DIVIDEND_PER_SHARE": 0.5,
             "BONUS_IT_RATIO": 0, "TRANSFER_RATIO": 0,
             "IMPLEMENT_STATUS": "已实施", "REPORT_TIME": ""},
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