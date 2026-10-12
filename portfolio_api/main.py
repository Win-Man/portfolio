import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from config import settings
from database import engine, SessionLocal, Base
from models import Portfolio, Asset
from routers import portfolios, assets, stocks

# 配置日志：开发期人类可读，生产可改 JSON formatter
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger(__name__)

# 应用启动时建表 + 跑轻量迁移
Base.metadata.create_all(bind=engine)


def run_lightweight_migrations():
    """轻量迁移：SQLAlchemy 的 create_all 不会 ALTER 已有表，
    启动时检查并补齐缺失的列（幂等，重复执行安全）。
    长期建议接入 Alembic。
    """
    migrations = [
        ("assets", "note", "VARCHAR DEFAULT ''"),
        ("assets", "price_source", "VARCHAR DEFAULT 'stale'"),
        ("assets", "price_fetched_at", "TIMESTAMP NULL"),
    ]
    with engine.begin() as conn:
        for table, column, ddl in migrations:
            # pragma_table_info 的参数是标识符而非值，直接字符串拼接；table/column 来自白名单，安全
            rows = conn.execute(
                text(f"SELECT 1 FROM pragma_table_info('{table}') WHERE name='{column}'")
            ).fetchone()
            if not rows:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                logging.info("migration: added %s.%s", table, column)


run_lightweight_migrations()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭钩子（替代已弃用的 @app.on_event）。"""
    init_sample_data()
    logger.info("portfolio_api startup complete (db=%s)", settings.database_url)
    yield
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