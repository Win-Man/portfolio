"""
股票数据 API 路由
提供股票搜索、实时价格查询、分红历史查询功能
"""
import logging

from fastapi import APIRouter, HTTPException
from typing import List, Optional
import asyncio
import httpx
import re
import json
import time
from urllib.parse import quote

from config import settings

logger = logging.getLogger(__name__)

# 导入akshare获取分红数据
import akshare as ak
import pandas as pd

router = APIRouter(prefix="/api/stocks", tags=["stocks"])


# ========== 分红数据 in-memory 缓存 ==========
# 设计：按 (stock_code) -> (fetched_at, result_list) 缓存分红数据。
# 理由：akshare.stock_dividend_cninfo 每次 200-800ms，且同一只股票在一次请求内
#       可能被多个端点命中（get_assets + AssetItem 展开 + refresh-price）。
# TTL：6 小时（盘中偶尔会有"权益分派实施公告"后追加的分红记录，6h 平衡时效与抖动）。
# 失效：refresh-price 成功后，调用 invalidate_dividend_cache(code) 强制下一次重拉。
import threading
from dataclasses import dataclass

DIVIDEND_CACHE_TTL_SECONDS = settings.dividend_cache_ttl_seconds
_dividend_cache: dict[str, tuple[float, list[dict]]] = {}
_dividend_cache_lock = threading.Lock()
# 串行化 akshare 调用：mini_racer (V8) 在多线程下会崩溃
_akshare_lock = asyncio.Lock()


def invalidate_dividend_cache(stock_code: str | None = None) -> None:
    """清除分红缓存。
    不传 code：清空全部（手动刷新用）。
    传 code：只清该股票（refresh-price 成功后用，下次访问立即拿新数据）。
    """
    with _dividend_cache_lock:
        if stock_code is None:
            _dividend_cache.clear()
        else:
            _dividend_cache.pop(stock_code, None)

# ========== 工具函数 ==========

def get_market_code(stock_code: str) -> str:
    """根据股票代码判断市场前缀"""
    if stock_code.startswith('6') or stock_code.startswith('9'):
        return f"sh{stock_code}"
    else:
        return f"sz{stock_code}"


async def fetch_price_from_tencent(stock_code: str) -> dict:
    """从腾讯行情获取实时价格"""
    market_code = get_market_code(stock_code)
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"https://qt.gtimg.cn/q={market_code}")
            text = response.text
        
        match = re.search(r'"([^"]+)"', text)
        if match:
            parts = match.group(1).split('~')
            if len(parts) > 30:
                name = parts[1]
                price = float(parts[3])
                yesterday_close = float(parts[4])
                
                return {
                    "success": True,
                    "name": name,
                    "price": price,
                    "yesterday_close": yesterday_close,
                    "market_code": market_code
                }
        
        raise Exception("解析股票数据失败")
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


async def fetch_dividend_from_eastmoney(stock_code: str) -> List[dict]:
    """从东方财富获取历史分红数据（使用akshare）

    带 in-memory TTL 缓存：同一只股票 6 小时内重复请求直接走缓存。

    ⚠ akshare 底层依赖 mini_racer (V8 isolate)，多线程并发访问同一进程
    会触发 libmini_racer.dylib FATAL crash（已复现过 SIGABRT exit 133）。
    因此实际拉取用 _akshare_lock 串行化，缓存命中则完全绕过。
    缓存命中是常态，所以这个锁几乎不会等待。
    """
    # 1. 缓存命中
    cached = _dividend_cache.get(stock_code)
    if cached is not None and time.time() - cached[0] < DIVIDEND_CACHE_TTL_SECONDS:
        return cached[1]

    # 2. 串行拉取（持锁期间其它协程等待）
    async with _akshare_lock:
        # 进入临界区后再次检查，避免 N 只股票同 code 时重复拉
        cached = _dividend_cache.get(stock_code)
        if cached is not None and time.time() - cached[0] < DIVIDEND_CACHE_TTL_SECONDS:
            return cached[1]
        results = _fetch_dividend_blocking(stock_code)
    with _dividend_cache_lock:
        _dividend_cache[stock_code] = (time.time(), results)
    return results


def _fetch_dividend_blocking(stock_code: str) -> List[dict]:
    """同步调用 akshare。**只能从持 _akshare_lock 的协程调用**，否则会触发 V8 崩溃。"""
    try:
        df = ak.stock_dividend_cninfo(symbol=stock_code)
    except Exception as e:
        logger.warning("获取分红数据失败 [%s]: %s", stock_code, e)
        return []

    if df is None or df.empty:
        return []

    results = []
    for _, row in df.iterrows():
        ex_date = row.get('除权日', '')
        if pd.isna(ex_date) or ex_date is None:
            ex_date = ''

        raw_dividend = row.get('派息比例', 0)
        if pd.isna(raw_dividend):
            dividend_per_share = 0.0
        else:
            dividend_per_share = float(raw_dividend) / 10.0

        raw_bonus = row.get('送股比例', 0)
        if pd.isna(raw_bonus):
            bonus_shares = 0.0
        else:
            bonus_shares = float(raw_bonus) / 10.0

        raw_transfer = row.get('转增比例', 0)
        if pd.isna(raw_transfer):
            transfer_shares = 0.0
        else:
            transfer_shares = float(raw_transfer) / 10.0

        announce_date = row.get('实施方案公告日期', '')
        status = '已实施' if announce_date else '待实施'

        results.append({
            'EX_DIVIDEND_DATE': ex_date,
            'DIVIDEND_PER_SHARE': dividend_per_share,
            'BONUS_IT_RATIO': bonus_shares,
            'TRANSFER_RATIO': transfer_shares,
            'IMPLEMENT_STATUS': status,
            'REPORT_TIME': row.get('报告时间', '')
        })
    return results


# ========== API 端点 ==========

@router.get("/{stock_code}/price")
async def get_stock_price(stock_code: str):
    """获取股票实时价格"""
    result = await fetch_price_from_tencent(stock_code)
    
    if result["success"]:
        return {
            "code": stock_code,
            "name": result["name"],
            "price": result["price"],
            "yesterday_close": result["yesterday_close"],
            "change": round(result["price"] - result["yesterday_close"], 2),
            "change_percent": round((result["price"] - result["yesterday_close"]) / result["yesterday_close"] * 100, 2)
        }
    else:
        raise HTTPException(status_code=400, detail=result.get("error", "获取价格失败"))


@router.get("/{stock_code}/dividend-history")
async def get_dividend_history(stock_code: str):
    """获取股票历史分红数据"""
    # 获取真实分红数据
    dividend_data = await fetch_dividend_from_eastmoney(stock_code)
    
    if dividend_data:
        # 获取股票名称
        stock_info = await fetch_price_from_tencent(stock_code)
        stock_name = stock_info.get("name", stock_code) if stock_info.get("success") else stock_code
        
        # 转换为前端需要的格式
        history = []
        for item in dividend_data:
            # 处理除权除息日（akshare返回datetime.date对象或空字符串）
            ex_date = item.get("EX_DIVIDEND_DATE", "")
            if ex_date:
                # 如果是date对象，转换为字符串
                if hasattr(ex_date, 'strftime'):
                    ex_date = ex_date.strftime("%Y-%m-%d")
                # 如果是字符串但长度为8（格式如20240630），转换
                elif isinstance(ex_date, str) and len(ex_date) == 8:
                    ex_date = f"{ex_date[:4]}-{ex_date[4:6]}-{ex_date[6:]}"
            
            # 提取年份
            try:
                year = int(ex_date[:4]) if ex_date else 0
            except:
                year = 0
            
            history.append({
                "year": year,
                "ex_dividend_date": ex_date,
                "dividend_per_share": float(item.get("DIVIDEND_PER_SHARE") or 0),
                "bonus_shares": float(item.get("BONUS_IT_RATIO") or 0),
                "transfer_shares": float(item.get("TRANSFER_RATIO") or 0),
                "status": item.get("IMPLEMENT_STATUS", "已实施")
            })
        
        # 按年份+日期排序（最新的在前）
        history.sort(key=lambda x: (x["year"], x["ex_dividend_date"]), reverse=True)
        
        return history
    
    # 如果没有真实数据，返回空数组
    return []


# ========== 股票搜索缓存机制 ==========
import pandas as pd
import os
import json

# 本地缓存文件路径
STOCK_CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "stock_cache.json")
STOCK_CACHE_FILE = os.path.abspath(STOCK_CACHE_FILE)

# 模块级缓存
_stock_cache = None
_cache_time = 0
_cache_date = ""  # 存储缓存的日期

def _get_today_date():
    from datetime import date
    return date.today().isoformat()

def _load_cache_from_file():
    """从本地文件加载缓存"""
    global _stock_cache, _cache_time, _cache_date
    if os.path.exists(STOCK_CACHE_FILE):
        try:
            with open(STOCK_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                _stock_cache = pd.DataFrame(data['stocks'])
                _cache_time = data.get('cache_time', 0)
                _cache_date = data.get('cache_date', '')
                logger.info("从本地文件加载股票缓存: %d 条, 日期: %s", len(_stock_cache), _cache_date)
                return True
        except Exception as e:
            logger.warning("加载缓存失败: %s", e)

def _save_cache_to_file():
    """保存缓存到本地文件"""
    global _stock_cache, _cache_time, _cache_date
    try:
        data = {
            'cache_time': _cache_time,
            'cache_date': _cache_date,
            'stocks': _stock_cache.to_dict('records') if _stock_cache is not None else []
        }
        with open(STOCK_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("股票缓存已保存到本地: %s", STOCK_CACHE_FILE)
    except Exception as e:
        logger.warning("保存缓存失败: %s", e)

def _refresh_stock_cache():
    """从 akshare 刷新股票缓存"""
    global _stock_cache, _cache_time, _cache_date
    
    logger.info("正在从 akshare 获取全量A股数据...")
    try:
        df = ak.stock_info_a_code_name()
        _stock_cache = df
        _cache_time = time.time()
        _cache_date = _get_today_date()

        # 保存到本地文件
        _save_cache_to_file()

        logger.info("股票缓存刷新完成: %d 条数据", len(df))
        return True
    except Exception as e:
        logger.warning("刷新股票缓存失败: %s", e)
        return False

def get_stock_cache():
    """获取股票缓存（懒加载机制）"""
    global _stock_cache, _cache_time, _cache_date
    today = _get_today_date()

    # 如果缓存为空或日期不是今天，先尝试从文件加载
    if _stock_cache is None:
        if not _load_cache_from_file():
            # 文件也没有，刷新缓存
            _refresh_stock_cache()
    elif _cache_date != today:
        # 日期变了，刷新缓存
        logger.info("缓存日期(%s)不是今天(%s)，刷新缓存...", _cache_date, today)
        _refresh_stock_cache()
    
    return _stock_cache


@router.get("/search")
async def search_stocks(keyword: str):
    """搜索股票（代码或名称），使用akshare获取全量A股数据后本地搜索"""
    if not keyword or len(keyword.strip()) < 1:
        return []
    
    try:
        # 使用缓存获取数据
        df = get_stock_cache()
        if df is None or df.empty:
            return []
        
        # 本地搜索（模糊匹配代码或名称）
        keyword_upper = keyword.strip().upper()
        mask = (
            df['code'].str.contains(keyword_upper, na=False) |
            df['name'].str.contains(keyword, na=False)
        )
        results = df[mask].head(20)
        
        # 返回格式
        stock_list = []
        for _, row in results.iterrows():
            code = row['code']
            # 判断市场
            if code.startswith('6') or code.startswith('9'):
                market = '沪市'
            else:
                market = '深市'
            
            stock_list.append({
                "code": code,
                "name": row['name'],
                "market": market,
                "type": "股票"
            })
        
        return stock_list
        
    except Exception as e:
        logger.warning("股票搜索失败: %s", e)
        return []


@router.post("/search/refresh")
async def refresh_stock_cache():
    """手动触发刷新股票缓存"""
    success = _refresh_stock_cache()
    if success:
        return {"message": "股票缓存刷新成功", "count": len(_stock_cache) if _stock_cache is not None else 0}
    else:
        raise HTTPException(status_code=500, detail="股票缓存刷新失败")


@router.get("/search/cache-info")
async def get_cache_info():
    """获取缓存状态信息"""
    global _cache_time, _cache_date
    df = get_stock_cache()
    return {
        "cache_date": _cache_date,
        "cache_time": _cache_time,
        "stock_count": len(df) if df is not None else 0,
        "cache_file": STOCK_CACHE_FILE,
        "dividend_cache": _dividend_cache_stats(),
    }


@router.get("/dividend-cache/info")
async def dividend_cache_info():
    """分红缓存状态（仅供调试）"""
    return _dividend_cache_stats()


@router.post("/dividend-cache/clear")
async def dividend_cache_clear(code: str | None = None):
    """清空分红缓存。可指定 code，否则清空全部。"""
    invalidate_dividend_cache(code)
    return _dividend_cache_stats()


def _dividend_cache_stats() -> dict:
    now = time.time()
    entries = []
    for k, (ts, data) in _dividend_cache.items():
        age = now - ts
        entries.append({
            "code": k,
            "age_seconds": round(age, 1),
            "ttl_remaining": round(DIVIDEND_CACHE_TTL_SECONDS - age, 1),
            "records": len(data),
        })
    entries.sort(key=lambda e: e["code"])
    return {
        "size": len(_dividend_cache),
        "ttl_seconds": DIVIDEND_CACHE_TTL_SECONDS,
        "entries": entries,
    }