import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from config import settings
import net
from database import engine, SessionLocal, Base
from models import Portfolio, Asset
from routers import portfolios, assets, stocks, goals, calendar, watchlist, admin, reports
from scheduler import get_job_specs, run_scheduler

# 配置日志：开发期人类可读，生产可改 JSON formatter
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger(__name__)

# 行情请求绕过本机代理：把行情域名并进 NO_PROXY（akshare 只认环境变量）。
# 必须在任何外呼之前执行；后台快照任务与本进程共用同一份环境。
net.ensure_setup()

# 应用启动时建表 + 跑轻量迁移
Base.metadata.create_all(bind=engine)


def run_lightweight_migrations():
    """轻量迁移：SQLAlchemy 的 create_all 不会 ALTER 已有表，
    启动时检查并补齐缺失的列（幂等，重复执行安全）。
    长期建议接入 Alembic。

    每项为 (表, 列, DDL, 回填 SQL 或 None)。
    回填**只在列是本次新增时**执行一次 —— 若每次启动都跑，
    会覆盖用户手动清空的成本值。
    """
    migrations = [
        ("assets", "note", "VARCHAR DEFAULT ''", None),
        ("assets", "price_source", "VARCHAR DEFAULT 'stale'", None),
        ("assets", "price_fetched_at", "TIMESTAMP NULL", None),
        # 成本列：存量资产的成本未知，按「未填则默认=当前价」的规则回填现价，
        # 之后由用户按实际建仓成本逐条修正
        (
            "assets",
            "cost_price",
            "FLOAT NULL",
            "UPDATE assets SET cost_price = price WHERE cost_price IS NULL",
        ),
    ]
    with engine.begin() as conn:
        for table, column, ddl, backfill in migrations:
            # pragma_table_info 的参数是标识符而非值，直接字符串拼接；table/column 来自白名单，安全
            rows = conn.execute(
                text(f"SELECT 1 FROM pragma_table_info('{table}') WHERE name='{column}'")
            ).fetchone()
            if not rows:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                logger.info("migration: added %s.%s", table, column)
                if backfill:
                    conn.execute(text(backfill))
                    logger.info("migration: backfilled %s.%s", table, column)


run_lightweight_migrations()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭钩子（替代已弃用的 @app.on_event）。"""
    init_sample_data()

    # 定时任务：观察池快照 + 投资组合价格刷新，共用一个后台协程，
    # 关闭时 cancel 并 await 收尾。测试环境（conftest 把两个开关都设为 false）不启动。
    scheduler_task = None
    if any(spec.enabled for spec in get_job_specs().values()):
        scheduler_task = asyncio.create_task(run_scheduler())

    logger.info("portfolio_api startup complete (db=%s)", settings.database_url)
    try:
        yield
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass
            except Exception:  # 收尾阶段的异常只记日志，不阻塞关闭
                logger.exception("scheduler shutdown failed")
        logger.info("portfolio_api shutting down")


app = FastAPI(
    title="投资组合管理 API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(portfolios.router)
app.include_router(assets.router)
app.include_router(stocks.router)
app.include_router(goals.router)
app.include_router(calendar.router)
app.include_router(watchlist.router)
app.include_router(admin.router)
app.include_router(reports.router)


# ---------- 种子数据 ----------

SAMPLE_PORTFOLIOS = [
    {
        "name": "投资组合",
        "assets": [
            {"name": "长江电力", "code": "600900", "quantity": 1000, "price": 28.5,
             "dividend_per_share": 0.82, "ttm_yield": 2.88, "static_yield": 2.95},
            {"name": "中国神华", "code": "601088", "quantity": 500, "price": 38.2,
             "dividend_per_share": 2.0, "ttm_yield": 5.24, "static_yield": 5.10},
            {"name": "交通银行", "code": "601328", "quantity": 2000, "price": 7.15,
             "dividend_per_share": 0.355, "ttm_yield": 4.97, "static_yield": 5.10},
        ],
    },
    {
        "name": "养老金",
        "assets": [
            {"name": "农业银行", "code": "601288", "quantity": 3000, "price": 3.85,
             "dividend_per_share": 0.222, "ttm_yield": 5.77, "static_yield": 5.82},
            {"name": "中国石化", "code": "600028", "quantity": 1500, "price": 6.45,
             "dividend_per_share": 0.3, "ttm_yield": 4.65, "static_yield": 4.70},
        ],
    },
    {"name": "备用金", "assets": []},
]


def init_sample_data():
    """表为空时种入示例数据。已存在则跳过。"""
    db: Session = SessionLocal()
    try:
        if db.query(Portfolio).count() > 0:
            return
        for spec in SAMPLE_PORTFOLIOS:
            p = Portfolio(name=spec["name"])
            db.add(p)
            db.commit()
            db.refresh(p)
            for a in spec["assets"]:
                db.add(Asset(portfolio_id=p.id, asset_type="股票", **a))
            db.commit()
        logging.info("Sample data initialized: %d portfolios", len(SAMPLE_PORTFOLIOS))
    finally:
        db.close()


# ---------- 健康检查 ----------


@app.get("/health")
def health():
    """存活探针：服务 + DB + 缓存（轻量检查）。"""
    db: Session = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    finally:
        db.close()
    return {
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
        "version": app.version,
    }


@app.get("/")
def root():
    return {"message": "投资组合管理 API", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload,
    )