"""观察池（watchlist）：CRUD + 每日快照刷新（spec 003）。

要点：
- 观察池是**全局**的：不绑定组合、不参与组合汇总（D1）；
- 价格与 TTM 分红全部按 code 直接拉，**不需要**先建资产记录，
  因此不动 `assets` 表与 `AssetResponse`；
- 抓取只走 `routers.assets.fetch_real_time_price` / `fetch_ttm_dividend_per_share`，
  绝不直接调 akshare（底层 mini_racer 不支持多线程，已被 `_akshare_lock` 串行化）；
- 派生数值与两条信号全部由 `watchlist_math` 现算（D9），前端不做阈值比较；
- 刷新接口**永不因外部数据源失败返回 5xx**：降级为「不写库 + refresh_error」（D7）。
"""
import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from market import guess_asset_type, normalize_code
from models import Asset, Portfolio, WatchlistItem, WatchlistSnapshot
from routers.assets import fetch_real_time_price, fetch_ttm_dividend_per_share
from routers.stocks import fetch_dividend_from_eastmoney
from schemas import (
    WatchlistCreate,
    WatchlistDividendResponse,
    WatchlistItemResponse,
    WatchlistSnapshotResponse,
    WatchlistUpdate,
)
from watchlist_history import backfill_item_history, extract_ex_dividends
from watchlist_math import derive_metrics, staleness

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["watchlist"])

SHANGHAI = ZoneInfo("Asia/Shanghai")

# D7：行情降级 mock 时的统一文案（前端直接展示，不写库）
REFRESH_ERROR_TEXT = "行情获取失败，已保留上次数据"


# ---------- 内部工具 ----------

def today_in_shanghai() -> date:
    """「今天」一律按 Asia/Shanghai 取，避免服务器 UTC 时区在 16:00 后跨日。"""
    return datetime.now(SHANGHAI).date()


def _get_item_or_404(db: Session, item_id: int) -> WatchlistItem:
    item = db.query(WatchlistItem).filter(WatchlistItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    return item


def _latest_snapshot(db: Session, item_id: int) -> Optional[WatchlistSnapshot]:
    return (
        db.query(WatchlistSnapshot)
        .filter(WatchlistSnapshot.item_id == item_id)
        .order_by(WatchlistSnapshot.trade_date.desc())
        .first()
    )


def _held_map(db: Session) -> Dict[str, List[str]]:
    """归一化代码 → 持有它的组合名列表（按组合 id 升序去重）。

    同一标的可能同时出现在多个组合里（D11），也可能在一个组合里出现两次
    （同一只标的建了两条资产记录），后者只记一次组合名。
    """
    rows = (
        db.query(Asset.code, Portfolio.id, Portfolio.name)
        .join(Portfolio, Asset.portfolio_id == Portfolio.id)
        .order_by(Portfolio.id.asc())
        .all()
    )
    held: Dict[str, List[str]] = {}
    for code, _pid, portfolio_name in rows:
        key = normalize_code(code or "")
        if not key:
            continue
        names = held.setdefault(key, [])
        if not names or names[-1] != portfolio_name:
            names.append(portfolio_name)
    return held


def _normalize_name(raw: str) -> str:
    """去首尾空白；strip 后为空 → 422（不依赖 Pydantic 的空白校验）。"""
    name = (raw or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="名称不能为空")
    if len(name) > 50:
        raise HTTPException(status_code=422, detail="名称不能超过 50 个字符")
    return name


def _normalize_stock_code(raw: str) -> str:
    """归一化 + 6 位数字校验（`sh600900` / `SH600900` / `600900` → `600900`）。"""
    code = normalize_code(raw or "")
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(status_code=422, detail="代码格式不正确")
    return code


def _to_response(
    item: WatchlistItem,
    snapshot: Optional[WatchlistSnapshot],
    held: Dict[str, List[str]],
    today: date,
    refresh_error: Optional[str] = None,
) -> WatchlistItemResponse:
    """把 ORM 行 + 最新快照组装成响应（派生字段全部现算）。"""
    price = snapshot.price if snapshot else None
    ttm_dividend = (snapshot.ttm_dividend or 0) if snapshot else 0
    snapshot_date = snapshot.trade_date if snapshot else None

    metrics = derive_metrics(
        price=price,
        ttm_dividend=ttm_dividend,
        target_yield=item.target_yield,
        target_price=item.target_price,
    )
    is_stale, stale_days = staleness(snapshot_date, today)
    held_names = held.get(normalize_code(item.code or ""), [])

    return WatchlistItemResponse(
        id=item.id,
        code=item.code,
        name=item.name,
        asset_type=item.asset_type or "股票",
        target_yield=item.target_yield,
        target_price=item.target_price,
        note=item.note or "",
        created_at=item.created_at,
        updated_at=item.updated_at,
        price=price,
        ttm_dividend=ttm_dividend,
        current_yield=metrics["current_yield"],
        snapshot_date=snapshot_date,
        price_source=snapshot.price_source if snapshot else None,
        is_stale=is_stale,
        stale_days=stale_days,
        yield_trigger_price=metrics["yield_trigger_price"],
        yield_gap_points=metrics["yield_gap_points"],
        yield_trigger_gap_percent=metrics["yield_trigger_gap_percent"],
        price_gap_percent=metrics["price_gap_percent"],
        yield_signal=metrics["yield_signal"],
        price_signal=metrics["price_signal"],
        is_held=bool(held_names),
        held_portfolios=list(held_names),
        refresh_error=refresh_error,
    )


def _list_items(db: Session) -> List[WatchlistItem]:
    return (
        db.query(WatchlistItem)
        .order_by(WatchlistItem.created_at.asc(), WatchlistItem.id.asc())
        .all()
    )


# ---------- 快照写入（定时任务与两个 refresh 端点共用这一份实现） ----------

async def _refresh_one(db: Session, item: WatchlistItem) -> Optional[str]:
    """抓取并 UPSERT 当天快照。返回 refresh_error（None 表示成功）。

    顺序：**先分红、后价格**。因为只有最后一步才写库，分红拉取一旦抛异常，
    当天就不会落任何快照；反过来先写价格再补分红，会留下 `ttm_dividend=0`
    的脏快照，把当天的股息率信号直接打成 unknown。
    """
    prev = _latest_snapshot(db, item.id)

    # 1. TTM 每股分红（滚动 12 个月合计，内部走分红缓存）
    ttm = await fetch_ttm_dividend_per_share(item.code)
    ttm = float(ttm or 0)

    # 2. D8 分红抖动保护：数据源偶尔某天抓回 0（接口异常/缓存穿透），
    #    而「真·突然不分红」相对罕见 —— 沿用历史值，避免当天信号被误判成 unknown
    if ttm == 0 and prev is not None and (prev.ttm_dividend or 0) > 0:
        ttm = float(prev.ttm_dividend)

    # 3. 实时价：current_price 只用于行情失败时的 mock 兜底基准
    prev_price = float(prev.price) if prev and prev.price else 0.0
    price_data = await fetch_real_time_price(item.code, current_price=prev_price)

    # 4. D7 只落真实价：mock 是 ±3% 随机价，写进曲线会污染信号，一律不写库
    if (price_data or {}).get("source") != "real":
        return REFRESH_ERROR_TEXT
    price = price_data.get("price")
    if price is None or float(price) <= 0:
        # 防御：real 但价格缺失/为 0（停牌、解析异常）同样不写，保留上次数据
        return REFRESH_ERROR_TEXT
    price = float(price)

    # 5. UPSERT：(item_id, trade_date) 唯一 → 先查后写（不依赖 SQLite 方言的 ON CONFLICT）
    today = today_in_shanghai()
    ttm_yield = (ttm / price * 100) if (ttm > 0 and price > 0) else None

    snapshot = (
        db.query(WatchlistSnapshot)
        .filter(
            WatchlistSnapshot.item_id == item.id,
            WatchlistSnapshot.trade_date == today,
        )
        .first()
    )
    if snapshot is None:
        snapshot = WatchlistSnapshot(
            item_id=item.id,
            trade_date=today,
            price=price,
            ttm_dividend=ttm,
            ttm_yield=ttm_yield,
            price_source="real",
        )
        db.add(snapshot)
    else:
        snapshot.price = price
        snapshot.ttm_dividend = ttm
        snapshot.ttm_yield = ttm_yield
        snapshot.price_source = "real"

    db.commit()

    # 6. 历史回填：新加入的标的还没有任何历史快照，走势图只有一个点。
    #    这里用真实日K + 历史分红补一段（只补缺、不覆盖，当天这条实时快照优先）。
    #    回填失败绝不影响刷新结果 —— 今天的数据已经落库，异常就地吞掉。
    try:
        await backfill_item_history(db, item, today=today)
    except Exception:
        logger.exception("watchlist backfill failed: code=%s", item.code)
        db.rollback()

    return None


# ---------- CRUD ----------

@router.get("/watchlist", response_model=List[WatchlistItemResponse])
def list_watchlist(db: Session = Depends(get_db)):
    """列表：**只读库，绝不触发外部抓取**（否则每次打开页面都在打 akshare）。"""
    items = _list_items(db)
    today = today_in_shanghai()
    held = _held_map(db)
    return [_to_response(item, _latest_snapshot(db, item.id), held, today) for item in items]


@router.post(
    "/watchlist", response_model=WatchlistItemResponse, status_code=201
)
def create_watchlist_item(payload: WatchlistCreate, db: Session = Depends(get_db)):
    """新增：不自动抓价（首次数据由定时任务或手动刷新产生）。"""
    code = _normalize_stock_code(payload.code)
    name = _normalize_name(payload.name)

    exists = db.query(WatchlistItem).filter(WatchlistItem.code == code).first()
    if exists:
        raise HTTPException(status_code=409, detail="该标的已在观察池中")

    # target_price=0 与 None 同义：未设（D2）
    target_price = payload.target_price
    if target_price is not None and target_price <= 0:
        target_price = None

    item = WatchlistItem(
        code=code,
        name=name,
        asset_type=payload.asset_type or guess_asset_type(code),
        target_yield=payload.target_yield,
        target_price=target_price,
        note=(payload.note or "").strip(),
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    today = today_in_shanghai()
    return _to_response(item, None, _held_map(db), today)


@router.put("/watchlist/{item_id}", response_model=WatchlistItemResponse)
def update_watchlist_item(
    item_id: int, payload: WatchlistUpdate, db: Session = Depends(get_db)
):
    """部分更新：字段为 None 则不改；target_price=0 表示清空（存 NULL）。"""
    item = _get_item_or_404(db, item_id)

    if payload.name is not None:
        item.name = _normalize_name(payload.name)
    if payload.asset_type is not None:
        item.asset_type = payload.asset_type
    if payload.target_yield is not None:
        item.target_yield = payload.target_yield
    if payload.target_price is not None:
        # 0 → 清空；其余（schema 已保证 >= 0）直接写入
        item.target_price = None if payload.target_price == 0 else payload.target_price
    if payload.note is not None:
        item.note = payload.note.strip()

    db.commit()
    db.refresh(item)

    today = today_in_shanghai()
    return _to_response(item, _latest_snapshot(db, item.id), _held_map(db), today)


@router.delete("/watchlist/{item_id}")
def delete_watchlist_item(item_id: int, db: Session = Depends(get_db)):
    """删除观察项：级联删其快照（组合与资产完全不受影响）。"""
    item = _get_item_or_404(db, item_id)
    db.delete(item)
    db.commit()
    return {"message": "Watchlist item deleted"}


# ---------- 刷新 ----------

@router.post("/watchlist/refresh", response_model=List[WatchlistItemResponse])
async def refresh_all_watchlist(db: Session = Depends(get_db)):
    """逐条刷新整张清单：单条异常吞掉继续下一条，整体永远 200。"""
    items = _list_items(db)
    errors: Dict[int, str] = {}

    for item in items:
        try:
            error = await _refresh_one(db, item)
        except Exception:  # 单条失败不能把整批拖崩，也不能整体 500
            logger.exception("watchlist refresh failed: code=%s", item.code)
            db.rollback()
            error = REFRESH_ERROR_TEXT
        if error:
            errors[item.id] = error

    today = today_in_shanghai()
    held = _held_map(db)
    return [
        _to_response(item, _latest_snapshot(db, item.id), held, today, errors.get(item.id))
        for item in items
    ]


@router.post("/watchlist/{item_id}/refresh", response_model=WatchlistItemResponse)
async def refresh_watchlist_item(item_id: int, db: Session = Depends(get_db)):
    """只刷一条。外部数据源失败时返回上次数据 + refresh_error，不返回 5xx。"""
    item = _get_item_or_404(db, item_id)
    try:
        error = await _refresh_one(db, item)
    except Exception:
        logger.exception("watchlist refresh failed: code=%s", item.code)
        db.rollback()
        error = REFRESH_ERROR_TEXT

    today = today_in_shanghai()
    return _to_response(item, _latest_snapshot(db, item.id), _held_map(db), today, error)


# ---------- 快照序列（画曲线） ----------

@router.get(
    "/watchlist/{item_id}/snapshots",
    response_model=List[WatchlistSnapshotResponse],
)
def list_watchlist_snapshots(
    item_id: int,
    days: int = Query(default=90, ge=1, le=730),
    start: Optional[date] = Query(default=None),
    end: Optional[date] = Query(default=None),
    all_dates: bool = Query(default=False, alias="all"),
    db: Session = Depends(get_db),
):
    """快照序列，按 trade_date 升序。缺日就是缺数据（不插值、不回填，D15）。

    三种取法，优先级从高到低：
      1. `?all=true`            → 全部历史（走势图「全部」档 / 上市日至今）
      2. `?start=&end=`         → 自定义区间（闭区间，任一端可省略）
      3. `?days=N`              → 最近 N 天（默认 90，兼容既有调用）

    `start` / `end` 与 `all` 都只做**区间筛选**，不改变任何口径：
    每条快照的 price / ttm_dividend / ttm_yield 仍是写入时的原始值。
    """
    _get_item_or_404(db, item_id)

    query = db.query(WatchlistSnapshot).filter(WatchlistSnapshot.item_id == item_id)

    if not all_dates:
        if start is not None or end is not None:
            if start is not None and end is not None and start > end:
                raise HTTPException(status_code=422, detail="起始日期不能晚于结束日期")
            if start is not None:
                query = query.filter(WatchlistSnapshot.trade_date >= start)
            if end is not None:
                query = query.filter(WatchlistSnapshot.trade_date <= end)
        else:
            query = query.filter(
                WatchlistSnapshot.trade_date >= today_in_shanghai() - timedelta(days=days)
            )

    return query.order_by(WatchlistSnapshot.trade_date.asc()).all()


@router.get("/watchlist/{item_id}/history-range")
def get_watchlist_history_range(item_id: int, db: Session = Depends(get_db)):
    """该标的在库里已有的快照区间（前端用于自定义区间的 min/max 与「全部」提示）。

    只读库、不抓取；没有任何快照时三个字段分别为 null / null / 0。
    """
    _get_item_or_404(db, item_id)
    first_date, last_date, count = (
        db.query(
            func.min(WatchlistSnapshot.trade_date),
            func.max(WatchlistSnapshot.trade_date),
            func.count(WatchlistSnapshot.id),
        )
        .filter(WatchlistSnapshot.item_id == item_id)
        .one()
    )
    return {
        "first_date": first_date,
        "last_date": last_date,
        "count": int(count or 0),
    }


# ---------- 除权日（画竖线，解释股息率曲线的台阶） ----------

@router.get(
    "/watchlist/{item_id}/dividends",
    response_model=List[WatchlistDividendResponse],
)
async def list_watchlist_dividends(
    item_id: int,
    days: int = Query(default=365, ge=7, le=730),
    start: Optional[date] = Query(default=None),
    end: Optional[date] = Query(default=None),
    all_dates: bool = Query(default=False, alias="all"),
    db: Session = Depends(get_db),
):
    """区间内的除权（派息）记录，按 ex_date 升序。

    参数语义与 `/snapshots` 一致：`all=true` 取全部历史，`start`/`end` 取自定义区间，
    否则取最近 `days` 天。

    只用于给走势图打竖线，**绝不写库**：除权日本身是外部事实，不随观察池状态变化。
    分红查询走 `fetch_dividend_from_eastmoney` 的既有 6 小时缓存，
    因此同一标的连续切换区间不会重复打外部接口；
    外部数据源失败时返回空列表（图照常画，只是没有竖线），不返回 5xx。
    """
    item = _get_item_or_404(db, item_id)
    try:
        records = await fetch_dividend_from_eastmoney(item.code) or []
    except Exception:
        logger.exception("watchlist dividends fetch failed: code=%s", item.code)
        return []

    today = today_in_shanghai()
    if all_dates:
        return extract_ex_dividends(records, start=date(1990, 1, 1), end=today)
    if start is not None or end is not None:
        if start is not None and end is not None and start > end:
            raise HTTPException(status_code=422, detail="起始日期不能晚于结束日期")
        return extract_ex_dividends(records, start=start, end=end or today)
    return extract_ex_dividends(records, days=days, today=today)
