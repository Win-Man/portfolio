"""每日日报：采集 → 计算 → 落库（`daily_reports`）。

## 两份日报分别是什么

| 类型 | scope_id | 内容 |
|---|---|---|
| `portfolio` | 组合 id | 该组合资产列表的今日盈亏、盈亏明细、涨跌榜、收入结构、集中度、目标进度 |
| `watchlist` | 0（全局） | 观察池标的的等权今日盈亏、明细、股息率达标 / 目标价达标 / 接近达标 |

## 今日盈亏的「昨收」从哪来

- **投资组合**：资产表只存「当前价」，没有历史序列，所以生成时按代码拉一次行情
  （腾讯返回里带昨收）。行情失败时退而取「上一份日报里同一代码的价格」——
  日报本身就是每日快照，昨天的日报就是今天的昨收；
- **观察池**：`watchlist_snapshots` 本来就是按交易日一天一条，
  直接取「最近一条 < 今天」的价作为昨收，**不打任何外部接口**。

## 幂等

(report_type, scope_id, report_date) 唯一，重复生成走 UPSERT：同一天只留一份，
重跑只是让当天数据变新（15:40 用收盘价重算盘中手动生成的那份）。

## 为什么生成时会把真实价写回 assets

日报里的价格来自「生成那一刻的行情」，若不写回，页面上（DB 里的价）与日报里的价
会是两个数，用户会以为日报算错了。写回规则与 `scheduler.run_asset_price_refresh`
完全一致：只写 `source == "real"` 的价。
"""

import asyncio
import json
import logging
from datetime import date, datetime, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from config import settings
from models import Asset, DailyReport, Goal, Portfolio, WatchlistItem, WatchlistSnapshot
from portfolio_math import goal_gap, weighted_ttm_yield
from report_math import (
    TOP_N,
    change_amount,
    change_percent,
    concentration,
    day_pnl,
    income_breakdown,
    notional_pnl,
    signal_buckets,
    summarize_holdings,
    summarize_watchlist,
    top_movers,
)
from watchlist_math import derive_metrics
from watchlist_math import staleness as snapshot_staleness

logger = logging.getLogger(__name__)

REPORT_TYPES = ("portfolio", "watchlist")
WATCHLIST_SCOPE_ID = 0

# 生成日报时的行情并发度：与 refresh-all-prices 保持一致，避免被行情源限流
QUOTE_CONCURRENCY = 5


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _now_naive() -> datetime:
    """落库/展示都用 Asia/Shanghai 的墙上时间（naive），与 job_runs 的口径一致。

    刻意不用 `datetime.utcnow()`：日报标题里的「今天」必须是上海的今天，
    否则 22 点之后生成的日报会写成明天的日期。
    """
    return datetime.now(SHANGHAI).replace(tzinfo=None)


def _round6(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 6)


# ========== 行情采集 ==========

async def collect_quotes(codes: List[str]) -> Dict[str, dict]:
    """按代码拉行情（同一代码只请求一次），返回 {code: quote}。

    只收 `source == "real"` 的结果：mock 是 ±3% 随机价，拿它算今日盈亏等于编数字。
    单只失败/超时不影响其它标的，失败的代码不会出现在返回字典里。
    """
    from routers.assets import fetch_real_time_price

    unique = []
    seen = set()
    for code in codes:
        if code and code not in seen:
            seen.add(code)
            unique.append(code)
    if not unique:
        return {}

    sem = asyncio.Semaphore(QUOTE_CONCURRENCY)
    result: Dict[str, dict] = {}

    async def one(code: str) -> None:
        async with sem:
            try:
                quote = await fetch_real_time_price(code)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("daily report: quote failed code=%s", code, exc_info=True)
                return
        if (quote or {}).get("source") == "real" and (quote or {}).get("price"):
            result[code] = quote

    await asyncio.gather(*(one(code) for code in unique))
    return result


def prev_close_from_previous_report(db: Session, portfolio_id: int, today: date) -> Dict[str, float]:
    """从「上一份组合日报」里取出各代码的价格，作为今天的昨收兜底。

    行情接口挂掉时这条兜底让日报依然能算出今日盈亏（前提是那份报告的价确实是
    上一交易日的收盘价）。找不到上一份报告 → 空字典。
    """
    row = (
        db.query(DailyReport)
        .filter(
            DailyReport.report_type == "portfolio",
            DailyReport.scope_id == portfolio_id,
            DailyReport.report_date < today,
        )
        .order_by(DailyReport.report_date.desc())
        .first()
    )
    if row is None:
        return {}
    try:
        payload = json.loads(row.payload or "{}")
    except (TypeError, ValueError):
        return {}
    prices: Dict[str, float] = {}
    for item in payload.get("items") or []:
        code = item.get("code")
        price = item.get("price")
        if code and isinstance(price, (int, float)) and price > 0:
            prices[str(code)] = float(price)
    return prices


# ========== 投资组合日报 ==========

def _build_portfolio_item(asset: Asset, quote: Optional[dict], fallback_prev_close: Optional[float]) -> dict:
    """单只资产的明细行。价格优先用行情返回的真实价，其次用库里的价。"""
    real = bool(quote) and quote.get("source") == "real"
    price = float(quote["price"]) if real else float(asset.price or 0)
    prev_close = quote.get("yesterday_close") if real else fallback_prev_close

    qty = float(asset.quantity or 0)
    return {
        "id": asset.id,
        "code": asset.code,
        "name": asset.name,
        "asset_type": asset.asset_type or "股票",
        "quantity": qty,
        "price": price,
        "prev_close": prev_close,
        # 逐项也抹掉浮点尾巴（0.35999999999999943）：落库的 JSON 会被前端原样展示，
        # 「¥72.00」不该以 71.99999999999989 的形状存下来
        "change": _round6(change_amount(price, prev_close)),
        "change_percent": _round6(change_percent(price, prev_close)),
        "day_pnl": _round6(day_pnl(qty, price, prev_close)),
        "market_value": _round6(qty * price),
        "cost_price": asset.cost_price,
        "holding_pnl": (
            _round6((price - float(asset.cost_price)) * qty)
            if asset.cost_price is not None else None
        ),
        "holding_pnl_percent": (
            _round6((price - float(asset.cost_price)) / float(asset.cost_price) * 100)
            if asset.cost_price is not None and float(asset.cost_price) > 0 else None
        ),
        "dividend_per_share": float(asset.dividend_per_share or 0),
        "ttm_yield": _round6(asset.ttm_yield or 0),
        "annual_income": _round6(qty * float(asset.dividend_per_share or 0)),
        # 这一份日报**用到的价格**从哪来（不是资产表里那个历史标记）：
        #   real   = 本次生成时行情返回的真实价
        #   cached = 库里的价（行情没拉到，可能是 15:05 刷新任务写的，也可能是几天前的）
        #   mock   = 库里那份价本身是模拟价（±3% 随机数）
        "price_source": (
            "real" if real
            else ("mock" if (asset.price_source or "stale") == "mock" else "cached")
        ),
    }


def _portfolio_notes(payload: dict) -> List[str]:
    summary = payload["summary"]
    notes: List[str] = []
    if summary["asset_count"] == 0:
        notes.append("该组合还没有资产，日报只包含空快照")
        return notes
    if summary["missing_prev_close_count"] > 0:
        notes.append(
            f"{summary['missing_prev_close_count']} 只标的没取到昨收，"
            f"今日盈亏只统计其余 {summary['day_pnl_covered_count']} 只"
        )
    if summary["cost_covered_count"] < summary["asset_count"]:
        notes.append(
            f"{summary['asset_count'] - summary['cost_covered_count']} 只标的未填成本，"
            f"持仓盈亏只统计已填成本的 {summary['cost_covered_count']} 只"
        )
    odd = [i for i in payload["items"] if i["price_source"] != "real"]
    if odd:
        names = "、".join(f"{i['name']}({i['code']})" for i in odd[:3])
        more = "" if len(odd) <= 3 else f" 等 {len(odd)} 只"
        notes.append(f"{names}{more} 的价格不是本次拉到的当日行情，相关盈亏仅供参考")
    return notes


async def build_portfolio_payload(
    db: Session,
    portfolio: Portfolio,
    today: date,
    quotes: Optional[Dict[str, dict]] = None,
) -> dict:
    """组装一份组合日报的 payload（不落库）。

    `quotes` 可由调用方预先拉好并复用（生成流程要拿同一批行情写回资产价，
    再拉一遍等于把行情接口打两倍）。
    """
    assets = (
        db.query(Asset)
        .filter(Asset.portfolio_id == portfolio.id)
        .order_by(Asset.id.asc())
        .all()
    )

    if quotes is None:
        quotes = await collect_quotes([a.code for a in assets])
    fallback = prev_close_from_previous_report(db, portfolio.id, today)

    items = [
        _build_portfolio_item(asset, quotes.get(asset.code), fallback.get(str(asset.code)))
        for asset in assets
    ]
    # 市值 / 收入 / 盈亏全部来自 items（与明细表逐行相加完全相等）：
    # 若这里改用 portfolio_math 的 asset.price 版本，行情价与库里的价相差一分钱时
    # 「汇总金额 ≠ 明细之和」，用户一眼就能看出对不上。
    summary = summarize_holdings(items)
    summary["weighted_ttm_yield"] = _round6(weighted_ttm_yield(assets))

    gainers, losers = top_movers(items, TOP_N)

    # 目标进度：与目标卡片共用 portfolio_math.goal_gap，口径不会漂移
    goals = []
    for goal in (
        db.query(Goal)
        .filter(Goal.portfolio_id == portfolio.id)
        .order_by(Goal.created_at.asc(), Goal.id.asc())
        .all()
    ):
        brief = goal_gap(summary["annual_income"], goal.annual_income_target)
        goals.append(
            {
                "id": goal.id,
                "name": goal.name,
                "target_annual_income": float(goal.annual_income_target),
                "current_annual_income": summary["annual_income"],
                "gap_annual": _round6(brief["gap_annual"]),
                "progress_percent": _round6(brief["progress_percent"]),
                "is_achieved": brief["is_achieved"],
            }
        )

    payload = {
        "type": "portfolio",
        "date": today.isoformat(),
        "generated_at": _now_naive().strftime("%Y-%m-%d %H:%M:%S"),
        "portfolio": {"id": portfolio.id, "name": portfolio.name},
        "summary": summary,
        "items": items,
        "top_gainers": gainers,
        "top_losers": losers,
        "income_top": income_breakdown(items, TOP_N),
        "concentration": concentration(items),
        "goals": goals,
        "notes": [],
    }
    payload["notes"] = _portfolio_notes(payload)
    return payload


def _write_back_prices(db: Session, assets: List[Asset], quotes: Dict[str, dict]) -> None:
    """把真实行情价写回资产（与 scheduler 的刷新任务同口径：只写 real）。

    mock 价一律不写 —— ±3% 的随机数写进库会污染页面上的盈亏。
    """
    for asset in assets:
        quote = quotes.get(asset.code)
        if not quote or quote.get("source") != "real":
            continue
        price = float(quote["price"])
        if price <= 0:
            continue
        asset.price = price
        asset.price_source = "real"
        asset.price_fetched_at = datetime.now(timezone.utc)
        if (asset.dividend_per_share or 0) > 0:
            asset.ttm_yield = round(asset.dividend_per_share / price * 100, 2)


# ========== 观察池日报 ==========

def _snapshots_by_item(db: Session, item_ids: List[int]) -> Dict[int, List[WatchlistSnapshot]]:
    """一次查出这些标的的全部快照，按 item_id 分组、日期倒序。

    不做 N+1 查询：观察池几十只标的，逐只查「最近两条」会打几十次库。
    """
    if not item_ids:
        return {}
    rows = (
        db.query(WatchlistSnapshot)
        .filter(WatchlistSnapshot.item_id.in_(item_ids))
        .order_by(WatchlistSnapshot.item_id.asc(), WatchlistSnapshot.trade_date.desc())
        .all()
    )
    grouped: Dict[int, List[WatchlistSnapshot]] = {}
    for snap in rows:
        grouped.setdefault(snap.item_id, []).append(snap)
    return grouped


def _build_watchlist_item(
    item: WatchlistItem, snaps: List[WatchlistSnapshot], today: date, notional: float
) -> dict:
    """单只观察标的的明细行。价格与昨收全部来自快照序列，不打外部接口。"""
    latest = snaps[0] if snaps else None
    prev = snaps[1] if len(snaps) > 1 else None

    price = float(latest.price) if latest else None
    prev_close = float(prev.price) if prev and prev.price else None
    ttm = float(latest.ttm_dividend or 0) if latest else 0.0

    metrics = derive_metrics(
        price=price,
        ttm_dividend=ttm,
        target_yield=item.target_yield,
        target_price=item.target_price,
    )
    is_stale, stale_days = snapshot_staleness(latest.trade_date if latest else None, today)
    pct = change_percent(price, prev_close)

    return {
        "id": item.id,
        "code": item.code,
        "name": item.name,
        "asset_type": item.asset_type or "股票",
        "price": price,
        "prev_close": prev_close,
        "change": _round6(change_amount(price, prev_close)),
        "change_percent": _round6(pct),
        "notional": notional,
        "notional_pnl": _round6(notional_pnl(pct, notional)),
        "ttm_dividend": _round6(ttm),
        "current_yield": _round6(metrics["current_yield"]),
        "target_yield": float(item.target_yield),
        "yield_gap_points": _round6(metrics["yield_gap_points"]),
        "yield_trigger_price": _round6(metrics["yield_trigger_price"]),
        "yield_signal": metrics["yield_signal"],
        "target_price": item.target_price,
        "price_gap_percent": _round6(metrics["price_gap_percent"]),
        "price_signal": metrics["price_signal"],
        "snapshot_date": latest.trade_date.isoformat() if latest else None,
        "is_today": bool(latest and latest.trade_date == today),
        "is_stale": is_stale,
        "stale_days": stale_days,
    }


def _watchlist_notes(payload: dict) -> List[str]:
    items = payload["items"]
    summary = payload["summary"]
    notes: List[str] = []
    if not items:
        notes.append("观察池为空，日报只包含空快照")
        return notes

    not_today = [i for i in items if not i["is_today"]]
    if not_today:
        names = "、".join(f"{i['name']}({i['code']})" for i in not_today[:3])
        more = "" if len(not_today) <= 3 else f" 等 {len(not_today)} 只"
        notes.append(
            f"{names}{more} 今日还没有快照，未计入今日涨跌"
            "（可点观察池的「刷新」或等 15:30 的定时任务）"
        )
    if summary["missing_prev_close_count"] > 0:
        notes.append(
            f"{summary['missing_prev_close_count']} 只标的缺少上一交易日快照，算不出今日涨跌"
        )
    stale = [i for i in items if i["is_stale"]]
    if stale:
        notes.append(f"{len(stale)} 只标的的快照已超过 5 天，信号可能过期")
    return notes


async def build_watchlist_payload(db: Session, today: date) -> dict:
    """组装观察池日报的 payload（只读库里的快照，不发任何外部请求）。"""
    items_db = (
        db.query(WatchlistItem)
        .order_by(WatchlistItem.created_at.asc(), WatchlistItem.id.asc())
        .all()
    )
    notional = float(settings.daily_report_watchlist_notional)
    grouped = _snapshots_by_item(db, [i.id for i in items_db])
    items = [
        _build_watchlist_item(item, grouped.get(item.id, []), today, notional)
        for item in items_db
    ]

    # 今日口径只统计「今天有快照」的标的，避免几天前的旧数据冒充今天的涨跌
    today_items = [i for i in items if i["is_today"]]
    summary = summarize_watchlist(today_items, notional)
    summary["item_count"] = len(items)          # 总标的数（含今日无快照的）
    summary["today_count"] = len(today_items)   # 计入今日统计的标的数

    buckets = signal_buckets(items)
    summary["yield_hit_count"] = len(buckets["hit_yield"])
    summary["price_hit_count"] = len(buckets["hit_price"])
    summary["yield_near_count"] = len(buckets["near_yield"])
    summary["price_near_count"] = len(buckets["near_price"])

    payload = {
        "type": "watchlist",
        "date": today.isoformat(),
        "generated_at": _now_naive().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "items": items,
        "yield_hits": buckets["hit_yield"],
        "price_hits": buckets["hit_price"],
        "yield_near": buckets["near_yield"],
        "price_near": buckets["near_price"],
        "notes": [],
    }
    payload["notes"] = _watchlist_notes(payload)
    return payload


# ========== 落库 ==========

def upsert_report(
    db: Session,
    report_type: str,
    scope_id: int,
    report_date: date,
    title: str,
    payload: dict,
) -> DailyReport:
    """按 (类型, 范围, 日期) UPSERT 一份日报，返回落库后的行。

    冗余汇总字段从 payload 里提出来，列表页因此不必解析 payload。
    """
    if report_type not in REPORT_TYPES:
        raise ValueError(f"unknown report type: {report_type}")

    summary = payload.get("summary") or {}
    body = json.dumps(payload, ensure_ascii=False)

    row = (
        db.query(DailyReport)
        .filter(
            DailyReport.report_type == report_type,
            DailyReport.scope_id == scope_id,
            DailyReport.report_date == report_date,
        )
        .first()
    )
    if row is None:
        row = DailyReport(
            report_type=report_type,
            scope_id=scope_id,
            report_date=report_date,
            title=title,
            payload=body,
        )
        db.add(row)

    row.title = title
    row.payload = body
    row.total_pnl = float(summary.get("day_pnl") or 0)
    row.total_pnl_percent = (
        None if summary.get("day_pnl_percent") is None else float(summary["day_pnl_percent"])
    )
    row.item_count = int(summary.get("item_count") or summary.get("asset_count") or 0)
    row.signal_count = int(summary.get("yield_hit_count") or 0) + int(
        summary.get("price_hit_count") or 0
    )

    db.commit()
    db.refresh(row)
    return row


async def generate_portfolio_reports(
    db: Session, today: Optional[date] = None, portfolio_id: Optional[int] = None
) -> List[DailyReport]:
    """给（一个或全部）组合生成今天的日报。返回落库的行。"""
    today = today or _now_naive().date()
    query = db.query(Portfolio).order_by(Portfolio.id.asc())
    if portfolio_id is not None:
        query = query.filter(Portfolio.id == portfolio_id)
    portfolios = query.all()

    reports: List[DailyReport] = []
    for portfolio in portfolios:
        assets = (
            db.query(Asset)
            .filter(Asset.portfolio_id == portfolio.id)
            .order_by(Asset.id.asc())
            .all()
        )
        # 一次行情两用：先写回库（页面上与日报里是同一个价），再用来拼明细
        quotes = await collect_quotes([a.code for a in assets])
        _write_back_prices(db, assets, quotes)
        db.commit()

        payload = await build_portfolio_payload(db, portfolio, today, quotes=quotes)
        title = f"{portfolio.name} · {today.isoformat()} 组合日报"
        reports.append(
            upsert_report(db, "portfolio", portfolio.id, today, title, payload)
        )
        logger.info(
            "daily report: portfolio=%s date=%s day_pnl=%s",
            portfolio.name, today, payload["summary"].get("day_pnl"),
        )
    return reports


async def generate_watchlist_report(
    db: Session, today: Optional[date] = None
) -> Optional[DailyReport]:
    """生成今天的观察池日报（全局一份）。观察池为空时也生成，保留「今天确实看过了」。"""
    today = today or _now_naive().date()
    payload = await build_watchlist_payload(db, today)
    title = f"观察池 · {today.isoformat()} 日报"
    row = upsert_report(db, "watchlist", WATCHLIST_SCOPE_ID, today, title, payload)
    logger.info(
        "daily report: watchlist date=%s day_pnl=%s", today, payload["summary"].get("day_pnl")
    )
    return row


def report_payload(row: DailyReport) -> dict:
    """读出 payload；脏数据不能让详情接口 500。"""
    try:
        data = json.loads(row.payload or "{}")
    except (TypeError, ValueError):
        logger.warning("daily report %s: payload 不是合法 JSON", row.id)
        return {}
    return data if isinstance(data, dict) else {}


def available_dates(db: Session, report_type: str, scope_id: int, days: int = 30) -> List[date]:
    """最近生成过日报的日期（供前端日历/列表定位）。"""
    rows = (
        db.query(DailyReport.report_date)
        .filter(DailyReport.report_type == report_type, DailyReport.scope_id == scope_id)
        .order_by(DailyReport.report_date.desc())
        .limit(days)
        .all()
    )
    return [r[0] for r in rows]


__all__ = [
    "REPORT_TYPES",
    "WATCHLIST_SCOPE_ID",
    "build_portfolio_payload",
    "build_watchlist_payload",
    "collect_quotes",
    "generate_portfolio_reports",
    "generate_watchlist_report",
    "prev_close_from_previous_report",
    "report_payload",
    "upsert_report",
]
