from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import asyncio
import httpx
import re

from database import get_db
from models import Asset, Portfolio
from schemas import AssetCreate, AssetUpdate, AssetResponse
from routers.stocks import fetch_dividend_from_eastmoney, invalidate_dividend_cache

router = APIRouter(prefix="/api", tags=["assets"])

# 腾讯股票行情API
TENCENT_STOCK_URL = "https://qt.gtimg.cn/q"


def get_tencent_market_code(stock_code: str) -> str:
    """根据股票代码判断市场前缀
    上海: sh
    深圳: sz
    """
    if stock_code.startswith('6') or stock_code.startswith('9'):
        return f"sh{stock_code}"  # 上海
    else:
        return f"sz{stock_code}"  # 深圳


async def fetch_latest_dividend_per_share(stock_code: str) -> float:
    """从东方财富获取最新每股分红金额"""
    dividend_data = await fetch_dividend_from_eastmoney(stock_code)
    if not dividend_data:
        return 0.0
    
    # 取最近一次已实施的分红（按 EX_DIVIDEND_DATE 降序，第一条即最新）
    for item in dividend_data:
        # 只取已实施的分红
        if item.get("IMPLEMENT_STATUS") not in ("已实施", "实施"):
            continue
        dividend = item.get("DIVIDEND_PER_SHARE")
        if dividend is not None and float(dividend) > 0:
            return float(dividend)
    
    # 如果没有已实施的，返回第一条
    first = dividend_data[0]
    dividend = first.get("DIVIDEND_PER_SHARE")
    return float(dividend) if dividend else 0.0


async def fetch_real_time_price(stock_code: str, current_price: float = 0, dividend_per_share: float = 0) -> dict:
    """从腾讯行情获取实时价格，失败时返回模拟价格

    返回值携带 source 字段：real / mock，调用方应据此决定是否更新 DB 的
    price_source 列，避免把模拟数据当成真实价写入。
    """
    market_code = get_tencent_market_code(stock_code)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{TENCENT_STOCK_URL}={market_code}")
            text = response.text

        # 解析腾讯行情数据格式
        # v_sh600900="1~名称~代码~当前价~昨收~今开~成交量~..."
        match = re.search(r'"([^"]+)"', text)
        if match:
            parts = match.group(1).split('~')
            if len(parts) > 30:
                name = parts[1]
                price = float(parts[3])  # 当前价
                yesterday_close = float(parts[4])  # 昨收

                # TTM 收益率计算：每股分红/当前价格*100
                ttm_yield = (dividend_per_share / price * 100) if price > 0 and dividend_per_share > 0 else 0

                return {
                    "price": price,
                    "yesterday_close": yesterday_close,
                    "name": name,
                    "ttm_yield": round(ttm_yield, 2),
                    "success": True,
                    "source": "real",
                }

        raise Exception("解析股票数据失败")

    except Exception as e:
        # API失败时使用模拟价格（基于当前价格±3%波动）
        import random
        fluctuation = (random.random() - 0.5) * 0.06  # ±3%
        mock_price = current_price * (1 + fluctuation) if current_price > 0 else 10.0
        mock_ttm = (dividend_per_share / mock_price * 100) if mock_price > 0 and dividend_per_share > 0 else 0
        return {
            "price": round(mock_price, 2),
            "name": stock_code,
            "ttm_yield": round(mock_ttm, 2),
            "success": True,
            "mock": True,  # 兼容旧字段
            "source": "mock",  # 新字段
            "error": str(e),
        }


@router.get("/portfolios/{portfolio_id}/assets", response_model=List[AssetResponse])
async def get_assets(portfolio_id: int, db: Session = Depends(get_db)):
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    assets = db.query(Asset).filter(Asset.portfolio_id == portfolio_id).all()

    # 拉分红走 fetch_dividend_from_eastmoney（已自带 in-memory TTL 缓存），
    # 由于 akshare 底层 mini_racer 不支持多线程，并发会被 _akshare_lock 串行化。
    # 缓存命中时锁是空过，所以日常体感是 O(1)。
    result = []
    for asset in assets:
        data = await fetch_dividend_from_eastmoney(asset.code)
        y2025 = 0.0
        y2024 = 0.0
        for item in data:
            ex_date = item.get("EX_DIVIDEND_DATE", "")
            if hasattr(ex_date, 'strftime'):
                year = ex_date.strftime("%Y")
            elif isinstance(ex_date, str) and ex_date:
                year = ex_date[:4]
            else:
                year = ""
            dividend = float(item.get("DIVIDEND_PER_SHARE") or 0)
            if year == "2025":
                y2025 += dividend
            elif year == "2024":
                y2024 += dividend
        response = AssetResponse.from_orm_with_calc(asset)
        response.y2025_dividend = y2025
        response.y2024_dividend = y2024
        result.append(response)

    return result


@router.post("/portfolios/{portfolio_id}/assets", response_model=AssetResponse)
def create_asset(portfolio_id: int, asset: AssetCreate, db: Session = Depends(get_db)):
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    db_asset = Asset(portfolio_id=portfolio_id, **asset.model_dump())
    db.add(db_asset)
    db.commit()
    db.refresh(db_asset)
    return AssetResponse.from_orm_with_calc(db_asset)


@router.get("/assets/{asset_id}", response_model=AssetResponse)
def get_asset(asset_id: int, db: Session = Depends(get_db)):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return AssetResponse.from_orm_with_calc(asset)


@router.put("/assets/{asset_id}", response_model=AssetResponse)
def update_asset(asset_id: int, asset: AssetUpdate, db: Session = Depends(get_db)):
    db_asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not db_asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    update_data = asset.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_asset, key, value)

    db.commit()
    db.refresh(db_asset)
    return AssetResponse.from_orm_with_calc(db_asset)


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: int, db: Session = Depends(get_db)):
    db_asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not db_asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    db.delete(db_asset)
    db.commit()
    return {"message": "Asset deleted"}


@router.post("/assets/{asset_id}/refresh-price")
async def refresh_asset_price(asset_id: int, db: Session = Depends(get_db)):
    """刷新股票价格，同时从东方财富获取最新分红数据并更新 ttm_yield"""

    db_asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not db_asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    # 1. 获取最新每股分红
    dividend_per_share = await fetch_latest_dividend_per_share(db_asset.code)

    # 2. 获取实时价格（可能 real / mock）
    price_data = await fetch_real_time_price(db_asset.code, dividend_per_share=dividend_per_share)

    if price_data["success"]:
        # real 才覆盖 DB 的价格；mock 时仅更新来源标记，避免把假数据当真的
        if price_data["source"] == "real":
            db_asset.price = price_data["price"]
            if dividend_per_share > 0:
                db_asset.dividend_per_share = dividend_per_share
                db_asset.ttm_yield = round(dividend_per_share / price_data["price"] * 100, 2)
            db_asset.price_fetched_at = datetime.now(timezone.utc)
        db_asset.price_source = price_data["source"]  # real / mock 都更新
        # real 价更新意味着 akshare 的分红可能也变了，让下一次 get_assets 看到新数据
        if price_data["source"] == "real":
            invalidate_dividend_cache(db_asset.code)

        db.commit()
        db.refresh(db_asset)
        return AssetResponse.from_orm_with_calc(db_asset)
    else:
        raise HTTPException(status_code=400, detail=price_data.get("error", "获取价格失败"))


@router.post("/portfolios/{portfolio_id}/assets/refresh-all-prices")
async def refresh_all_prices(portfolio_id: int, db: Session = Depends(get_db)):
    """批量刷新组合内所有股票价格，同时更新分红和 ttm_yield

    性能优化（vs 之前）：
    - 5 路并发（asyncio.Semaphore）抓取，避免单只股票阻塞后续所有
    - 同一只股票在本次请求内只发一次分红接口（其它用同一缓存）
    - 失败的单只资产不影响其它
    """
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    assets = db.query(Asset).filter(Asset.portfolio_id == portfolio_id).all()

    # 把每只资产的处理逻辑提到一个独立协程，外面统一 gather
    async def refresh_one(asset: Asset) -> dict:
        try:
            dividend_per_share = await fetch_latest_dividend_per_share(asset.code)
            price_data = await fetch_real_time_price(asset.code, dividend_per_share=dividend_per_share)
            if price_data["success"]:
                source = price_data["source"]
                asset.price_source = source
                if source == "real":
                    asset.price = price_data["price"]
                    if dividend_per_share > 0:
                        asset.dividend_per_share = dividend_per_share
                        asset.ttm_yield = round(dividend_per_share / asset.price * 100, 2)
                    asset.price_fetched_at = datetime.now(timezone.utc)
                return {
                    "code": asset.code,
                    "name": asset.name,
                    "price": asset.price,
                    "dividend_per_share": asset.dividend_per_share,
                    "ttm_yield": asset.ttm_yield,
                    "success": True,
                    "source": source,
                }
            return {"code": asset.code, "name": asset.name, "success": False, "error": price_data.get("error")}
        except Exception as e:
            # 单只失败不能把整批拖崩
            return {"code": asset.code, "name": asset.name, "success": False, "error": str(e)}

    # 5 路并发：50 只股票 ≈ 10s；100 只 ≈ 20s（每只 akshare + 腾讯各 ~600ms）
    sem = asyncio.Semaphore(5)

    async def bounded(asset: Asset) -> dict:
        async with sem:
            return await refresh_one(asset)

    results = await asyncio.gather(*(bounded(a) for a in assets))

    # 任何 real 价的更新都让分红缓存失效，下一次列表请求能拿到最新 y2024/y2025
    for asset, r in zip(assets, results):
        if r.get("success") and r.get("source") == "real":
            invalidate_dividend_cache(asset.code)

    db.commit()
    return {"results": list(results)}
