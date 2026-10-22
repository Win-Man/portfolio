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
from market import (
    is_etf,
    market_code as build_market_code,
    market_label,
    normalize_code,
)

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
    """根据代码返回带市场前缀的行情代码（如 sh561580）。

    规则集中在 market.py —— 支持个股(0/3/6/9)与 ETF/基金(5/1 开头)。
    """
    return build_market_code(stock_code)


async def fetch_price_from_tencent(stock_code: str) -> dict:
    """从腾讯行情获取实时价格"""
    market_code = get_market_code(stock_code)
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{settings.tencent_quote_url}={market_code}")
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
    """获取历史分红数据（个股走 cninfo，ETF/基金走 sina）

    带 in-memory TTL 缓存：同一只标的 6 小时内重复请求直接走缓存。

    ⚠ 只缓存**成功**的结果。拉取失败（网络抖动 / SSL 断连 / akshare 异常）
    绝不写缓存 —— 否则一次瞬时故障会让该标的分红数据"空"6 小时。
    失败时若存在过期的旧缓存，宁可返回过期数据也不返回空。

    ⚠ akshare 底层依赖 mini_racer (V8 isolate)，多线程并发访问同一进程
    会触发 libmini_racer.dylib FATAL crash（已复现过 SIGABRT exit 133）。
    因此实际拉取用 _akshare_lock 串行化，缓存命中则完全绕过。
    """
    key = normalize_code(stock_code)

    def _fresh(entry):
        return entry is not None and time.time() - entry[0] < DIVIDEND_CACHE_TTL_SECONDS

    # 1. 缓存命中
    cached = _dividend_cache.get(key)
    if _fresh(cached):
        return cached[1]

    # 2. 串行拉取（持锁期间其它协程等待）
    async with _akshare_lock:
        # 进入临界区后再次检查，避免 N 只标的同 code 时重复拉
        cached = _dividend_cache.get(key)
        if _fresh(cached):
            return cached[1]

        if is_etf(key):
            ok, results = _fetch_etf_dividend_blocking(key)
        else:
            ok, results = _fetch_stock_dividend_blocking(key)

        if not ok:
            # 拉取失败：不写缓存。有旧值就用旧值（stale-while-error），否则返回空
            if cached is not None:
                age = int(time.time() - cached[0])
                logger.warning(
                    "分红拉取失败 [%s]，回退到 %ds 前的缓存数据", key, age
                )
                return cached[1]
            return []

    with _dividend_cache_lock:
        _dividend_cache[key] = (time.time(), results)
    return results


async def fetch_all_dividend_records(stock_code: str) -> List[dict]:
    """获取该标的**全部历史**分红原始记录（供「收入日历」按 ex_date 归月使用）。

    与 fetch_dividend_from_eastmoney 是同一份数据、同一个 in-memory 缓存
    （``_dividend_cache``，TTL 6h）—— 这里只是语义化的读入口，**不另起缓存**：
    日历需要全部历史（1970 年至今所有期），而 TTM 口径只关心滚动 12 个月。

    契约：**永不抛异常**。akshare 网络失败时沿用 fetch_dividend_from_eastmoney
    的「回退到 6h 旧缓存」逻辑；完全无缓存则返回空列表，调用方跳过该标的即可，
    不能让单只标的的抓取失败把整个 calendar 接口拖成 500。
    """
    try:
        records = await fetch_dividend_from_eastmoney(stock_code)
    except Exception as e:  # pragma: no cover - 兜底，正常路径不会走到
        logger.warning("获取全部分红记录失败 [%s]: %s", stock_code, e)
        return []
    return records or []


def _fetch_stock_dividend_blocking(stock_code: str) -> tuple[bool, List[dict]]:
    """个股分红：akshare.stock_dividend_cninfo。

    返回 (是否成功, 记录列表)。ok=False 表示拉取失败，调用方不应缓存。
    **只能从持 _akshare_lock 的协程调用**，否则会触发 V8 崩溃。
    """
    try:
        df = ak.stock_dividend_cninfo(symbol=stock_code)
    except Exception as e:
        logger.warning("获取个股分红数据失败 [%s]: %s", stock_code, e)
        return False, []

    if df is None or df.empty:
        return True, []

    try:
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
    except Exception as e:
        # 字段结构变化（akshare 升级）等：视为失败，不缓存空结果
        logger.warning("解析个股分红数据失败 [%s]: %s", stock_code, e)
        return False, []
    return True, results


def _fetch_etf_dividend_blocking(fund_code: str) -> tuple[bool, List[dict]]:
    """ETF/基金分红：akshare.fund_etf_dividend_sina。

    ⚠ 数据模型与个股**完全不同**：sina 返回的是「累计分红」，不是每期金额。

        日期          累计分红
        2025-05-19    0.005     ← 首期，当期分红 = 0.005
        2025-06-16    0.010     ← 当期分红 = 0.010 - 0.005 = 0.005
        2025-07-14    0.015     ← 当期分红 = 0.015 - 0.010 = 0.005
        2025-08-18    0.035     ← 当期分红 = 0.035 - 0.015 = 0.020

    因此这里做**相邻差分**还原每期金额，并统一成与个股相同的字段结构，
    使下游（y2024/y2025 汇总、dividend-history、refresh-price）无需感知差异。

    红利 ETF 多为**月度分红**（561580 一年 12 期），与个股 1-2 期/年差异很大。

    返回 (是否成功, 记录列表)。sina 偶发 SSL 断连，失败时不缓存。
    **只能从持 _akshare_lock 的协程调用**，否则会触发 V8 崩溃。
    """
    symbol = build_market_code(fund_code)  # sina 需要带前缀，如 sh561580
    try:
        df = ak.fund_etf_dividend_sina(symbol=symbol)
    except Exception as e:
        logger.warning("获取 ETF 分红数据失败 [%s]: %s", fund_code, e)
        return False, []

    if df is None or df.empty:
        # 无分红记录是合法结果（该基金确实没分过红），可缓存
        return True, []

    try:
        # 按日期升序，保证差分的先后顺序正确
        try:
            df = df.sort_values('日期')
        except Exception:
            pass

        results = []
        prev_cumulative = 0.0
        for _, row in df.iterrows():
            raw_cum = row.get('累计分红', 0)
            try:
                cumulative = float(raw_cum)
            except (TypeError, ValueError):
                continue
            if pd.isna(cumulative):
                continue

            # 每期分红 = 本次累计 - 上次累计
            per_period = round(cumulative - prev_cumulative, 6)
            prev_cumulative = cumulative
            if per_period <= 0:
                # 累计值未增长（数据异常或重复行），跳过以免污染汇总
                continue

            ex_date = row.get('日期', '')
            if pd.isna(ex_date):
                ex_date = ''

            results.append({
                'EX_DIVIDEND_DATE': ex_date,
                'DIVIDEND_PER_SHARE': per_period,
                'BONUS_IT_RATIO': 0.0,      # ETF 无送股
                'TRANSFER_RATIO': 0.0,      # ETF 无转增
                'IMPLEMENT_STATUS': '已实施',  # sina 只记录已发生的分红
                'REPORT_TIME': '',
            })
    except Exception as e:
        logger.warning("解析 ETF 分红数据失败 [%s]: %s", fund_code, e)
        return False, []
    return True, results


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
    """从本地文件加载缓存（兼容旧格式：旧文件只有 code/name 两列）"""
    global _stock_cache, _cache_time, _cache_date
    if os.path.exists(STOCK_CACHE_FILE):
        try:
            with open(STOCK_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                df = pd.DataFrame(data['stocks'])
                # 旧缓存没有 asset_type / market 列，按代码补齐
                if 'asset_type' not in df.columns:
                    df['asset_type'] = df['code'].map(
                        lambda c: 'ETF' if is_etf(c) else '股票'
                    )
                if 'market' not in df.columns:
                    df['market'] = df['code'].map(market_label)
                _stock_cache = df
                _cache_time = data.get('cache_time', 0)
                _cache_date = data.get('cache_date', '')
                logger.info("从本地文件加载标的缓存: %d 条, 日期: %s", len(_stock_cache), _cache_date)
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
    """从 akshare 刷新可搜索标的缓存：A 股个股 + ETF/LOF/封闭式基金。

    合并来源：
      - ak.stock_info_a_code_name()           → 个股（0/3/6/9 开头，约 5500 只）
      - ak.fund_etf_category_sina('ETF基金')   → ETF（5/1 开头，约 1700 只）
      - ak.fund_etf_category_sina('LOF基金')   → LOF
      - ak.fund_etf_category_sina('封闭式基金') → 封闭式

    任一来来源失败不影响其它来源（graceful degradation）。
    """
    global _stock_cache, _cache_time, _cache_date

    frames = []

    # 1. 个股
    logger.info("正在从 akshare 获取全量A股数据...")
    try:
        df_stock = ak.stock_info_a_code_name()
        if df_stock is not None and not df_stock.empty:
            df_stock = df_stock[['code', 'name']].copy()
            df_stock['asset_type'] = '股票'
            frames.append(df_stock)
            logger.info("个股: %d 条", len(df_stock))
    except Exception as e:
        logger.warning("获取个股列表失败: %s", e)

    # 2. 基金（ETF / LOF / 封闭式）
    for kind in ("ETF基金", "LOF基金", "封闭式基金"):
        try:
            df_fund = ak.fund_etf_category_sina(symbol=kind)
            if df_fund is None or df_fund.empty:
                continue
            df_fund = df_fund[['代码', '名称']].copy()
            df_fund.columns = ['code', 'name']
            # sina 返回带 sh/sz 前缀，规整成 6 位纯代码
            df_fund['code'] = df_fund['code'].map(normalize_code)
            df_fund['asset_type'] = 'ETF'
            frames.append(df_fund)
            logger.info("%s: %d 条", kind, len(df_fund))
        except Exception as e:
            logger.warning("获取 %s 列表失败: %s", kind, e)

    if not frames:
        logger.warning("刷新标的缓存失败：所有来源都不可用")
        return False

    merged = pd.concat(frames, ignore_index=True)
    # 同一代码可能同时出现在多个来源（如 ETF 也在 LOF 列表），去重保留首次出现
    merged = merged.drop_duplicates(subset=['code'], keep='first')
    merged['market'] = merged['code'].map(market_label)

    _stock_cache = merged.reset_index(drop=True)
    _cache_time = time.time()
    _cache_date = _get_today_date()

    _save_cache_to_file()
    logger.info("标的缓存刷新完成: 共 %d 条", len(_stock_cache))
    return True

def get_stock_cache():
    """获取标的缓存（懒加载机制）"""
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
    """搜索标的（代码或名称），覆盖个股 + ETF/LOF/封闭式基金"""
    if not keyword or len(keyword.strip()) < 1:
        return []
    
    try:
        # 使用缓存获取数据
        df = get_stock_cache()
        if df is None or df.empty:
            return []
        
        # 本地搜索（模糊匹配代码或名称）
        kw = keyword.strip()
        kw_upper = kw.upper()
        mask = (
            df['code'].str.contains(kw_upper, na=False) |
            df['name'].str.contains(kw, na=False)
        )
        results = df[mask].head(20)
        
        # 返回格式
        stock_list = []
        for _, row in results.iterrows():
            code = row['code']
            asset_type = row.get('asset_type') or ('ETF' if is_etf(code) else '股票')
            stock_list.append({
                "code": code,
                "name": row['name'],
                "market": row.get('market') or market_label(code),
                "type": asset_type,
            })
        
        return stock_list
        
    except Exception as e:
        logger.warning("标的搜索失败: %s", e)
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