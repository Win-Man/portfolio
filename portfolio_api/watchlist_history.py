"""观察池历史快照回填：把「真实历史行情 × 当日 TTM 分红」补成逐日快照。

**为什么需要**
观察池只从「加入当天」开始每天写一条快照。新加的标的展开走势只有一个点，
既看不出历史水位，也没法和目标股息率对照 —— 需要把加入之前的历史补上。

**补多长**
默认补到**上市日**（`settings.watchlist_backfill_full=True`）：
走势图既能选 30/90/180 天，也能选「全部」或任意自定义区间，
没有全历史的话长区间就只有一小段线，右半边全是空白。
置 `watchlist_backfill_full=False` 可退回只补最近 `watchlist_backfill_days` 天。

**补什么**
对区间内的**每个交易日**写一条快照，口径与每日定时任务完全一致：

    price        = 当日收盘价（腾讯日K，个股 / ETF 同一接口）
    ttm_dividend = 该交易日往前滚动 12 个月的每股分红合计
    ttm_yield    = ttm_dividend / price * 100（仅当两者都 > 0，否则 null）
    price_source = "real"

TTM 复用 `routers.assets._sum_trailing_12m`（按除权日过滤，与数据源返回顺序无关），
把「今天」换成历史某天即可得到当天的滚动 12 个月分红 —— 不是估算，是同一口径回放。

**三条硬约束**
1. 只用真实数据：日K 拉不到就整段放弃，绝不插值、绝不用 mock 补点（D7 / D15）；
2. 只补缺、不覆盖：已存在的 trade_date 一律跳过，当天那条实时快照
   不会被日K收盘价覆盖（收盘价是 15:00 的，实时价可能更新）；
3. 不引入新依赖、不碰 akshare：日K 是纯 HTTP（httpx 已在用），
   不需要抢 `_akshare_lock`，也没有 mini_racer 多线程崩溃的风险。

**一次请求装不下全历史**
腾讯日K单次最多回 2000 根（3000 即 `param error`），交通银行这种 2007 年上市的
标的有 4700+ 个交易日，因此从 `end` 往前翻页，直到翻到上市日或没有更多数据。
"""
import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from config import settings
from market import market_code, normalize_code
from models import WatchlistItem, WatchlistSnapshot
from routers.assets import _parse_dividend_date, _sum_trailing_12m
from routers.stocks import fetch_dividend_from_eastmoney

logger = logging.getLogger(__name__)

SHANGHAI = ZoneInfo("Asia/Shanghai")

# 腾讯日K（与实时行情同一个数据源，只是路径不同）。
# 用 `kline/kline` 而不是 `fqkline/get`：后者忽略 start/end，永远只回「最近 N 根」，
# 翻不到 20 年前的数据；前者支持 `code,day,start,end,count` 按区间取。
KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"

# 单次上限 2000 根（3000 → param error），≈ 8 年交易日
KLINE_PAGE_BARS = 2000
# 翻页上限：8 × 2000 = 16000 根 ≈ 65 年，够覆盖 A 股全部历史，同时防止死循环
KLINE_MAX_PAGES = 8
# A 股最早交易日 1990-12-19，起点取 1990-01-01 即可视为「从头拉」
KLINE_FLOOR = date(1990, 1, 1)

# 进程内「已经拉到哪一天」标记：归一化代码 → 已取到的最早 bar 日期。
#
# 为什么不落库：加列需要迁移，而这里只是一条**性能缓存**，丢了不影响正确性。
# 作用是避免「每次刷新 / 每天定时任务」都把 20 年日K重拉一遍 ——
# 首次（进程启动后的第一次刷新）探测一次，之后同一进程内直接跳过。
_EARLIEST_BAR: Dict[str, date] = {}


def reset_history_cache() -> None:
    """清空进程内日K缓存（测试隔离用；正常运行时不需要调）。"""
    _EARLIEST_BAR.clear()


def today_in_shanghai() -> date:
    """「今天」按 Asia/Shanghai 取（与 routers/watchlist 同口径）。"""
    return datetime.now(SHANGHAI).date()


def _parse_bars(
    payload: dict,
    mc: str,
    start: date,
    end: date,
) -> List[Tuple[date, float]]:
    """从腾讯日K响应里解析 (交易日, 收盘价)，按日期升序，过滤掉区间外与非法行。

    不复权（`day` 而非 `qfqday`）：股息率 = 当日实际每股分红 / 当日实际收盘价，
    用前复权会把历史价按分红下折，算出偏高的股息率。
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    node = data.get(mc) or {}
    if not isinstance(node, dict):
        return []
    bars = node.get("day") or node.get("qfqday") or []

    out: List[Tuple[date, float]] = []
    for bar in bars:
        try:
            d = datetime.strptime(str(bar[0])[:10], "%Y-%m-%d").date()
            close = float(bar[2])  # [日期, 开, 收, 高, 低, 量]
        except (ValueError, TypeError, IndexError):
            continue
        if close <= 0 or d < start or d > end:
            continue
        out.append((d, close))
    out.sort(key=lambda x: x[0])
    return out


async def fetch_daily_closes(
    code: str,
    *,
    days: Optional[int] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
    today: Optional[date] = None,
) -> List[Tuple[date, float]]:
    """取 [start, end] 区间内的日K收盘价（按日期升序）。

    区间缺省为「从上市日到 end」；只给 `days` 时退化为「最近 days 个自然日」。
    腾讯单次最多 2000 根，因此从 end 往前翻页，直到翻到 start / 无更多数据。

    **永不抛异常**：网络失败 / 代码不存在 / 响应结构变化都返回空列表（或已翻到的部分），
    调用方据此跳过回填（宁可没有历史，也不写假数据）。
    """
    mc = market_code(code)
    key = normalize_code(code)
    end = end or today or today_in_shanghai()

    if start is None:
        start = (end - timedelta(days=int(days))) if days is not None else KLINE_FLOOR
    if start > end:
        return []

    collected: Dict[date, float] = {}
    reached_floor = False
    cursor = end

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for _ in range(KLINE_MAX_PAGES):
                resp = await client.get(
                    KLINE_URL,
                    params={"param": f"{mc},day,{start.isoformat()},{cursor.isoformat()},{KLINE_PAGE_BARS},"},
                )
                resp.raise_for_status()
                bars = _parse_bars(resp.json(), mc, start, cursor)

                if not bars:
                    # 空页：要么已翻过头，要么该 code 没有日K
                    break

                for d, close in bars:
                    collected[d] = close

                page_first = bars[0][0]
                if page_first <= start:
                    reached_floor = True
                    break
                if len(bars) < KLINE_PAGE_BARS:
                    # 整页都没装满 → 已经回到上市首日，没有更早的数据了
                    reached_floor = True
                    break
                cursor = page_first - timedelta(days=1)
    except Exception as e:
        logger.warning("观察池历史行情获取失败 [%s]: %s", code, e)
        if not collected:
            return []

    if reached_floor and collected:
        _EARLIEST_BAR[key] = min(collected)

    closes = sorted(collected.items())
    if not closes:
        logger.info("观察池历史行情为空 [%s]（可能停牌 / 新上市 / 代码无日K）", code)
    return closes


def build_history_rows(
    closes: Sequence[Tuple[date, float]],
    dividend_records: Sequence[dict],
) -> List[dict]:
    """纯函数：把 (交易日, 收盘价) 序列 + 分红记录 → 快照字段列表。

    单独抽成纯函数是为了可单测：不碰数据库、不发网络请求。
    """
    rows: List[dict] = []
    for d, close in closes:
        ttm = _sum_trailing_12m(list(dividend_records), today=d) if dividend_records else 0.0
        rows.append(
            {
                "trade_date": d,
                "price": close,
                "ttm_dividend": ttm,
                "ttm_yield": (ttm / close * 100) if (ttm > 0 and close > 0) else None,
                "price_source": "real",
            }
        )
    return rows


def extract_ex_dividends(
    dividend_records: Sequence[dict],
    *,
    days: Optional[int] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
    today: Optional[date] = None,
) -> List[dict]:
    """纯函数：分红记录 → 区间内的除权日列表（按除权日升序）。

    只保留「已实施 + 金额 > 0 + 落在区间内」的记录，
    与 `_sum_trailing_12m` 的过滤口径一致，保证图上每条竖线都能解释 TTM 的变动。

    区间：`start`/`end` 优先；只给 `days` 时取 [today-days, today]；
    两个都给时就是「全部历史」（走势图切到「全部」/自定义区间时不再缺竖线）。

    为什么单独给前端：TTM 股息率是「滚动 12 个月合计 / 现价」，
    除权日当天会有一期分红进入窗口（或一年前那期滚出），曲线出现台阶。
    把除权日画成竖线，台阶就从「看不懂的跳变」变成「有明确成因的台阶」。
    """
    ref = end or today or today_in_shanghai()
    if start is not None:
        low = start
    elif days is not None:
        low = ref - timedelta(days=int(days))
    else:
        low = KLINE_FLOOR
    high = ref

    out: List[dict] = []
    for record in dividend_records or []:
        if record.get("IMPLEMENT_STATUS") not in ("已实施", "实施"):
            continue
        ex_date = _parse_dividend_date(record.get("EX_DIVIDEND_DATE"))
        if ex_date is None or ex_date < low or ex_date > high:
            continue
        amount = float(record.get("DIVIDEND_PER_SHARE") or 0)
        if amount <= 0:
            continue
        out.append({"ex_date": ex_date, "dividend_per_share": amount})

    out.sort(key=lambda row: row["ex_date"])
    return out


async def backfill_item_history(
    db: Session,
    item: WatchlistItem,
    *,
    days: Optional[int] = None,
    today: Optional[date] = None,
) -> int:
    """给单个观察项补齐历史快照，返回**新写入**的行数。

    - 范围：默认补到上市日（`watchlist_backfill_full`），否则只补最近 N 天；
    - 已存在的日期全部跳过（不改、不覆盖），当天那条实时快照永远优先；
    - 分红拉取失败时 TTM 记 0、股息率为 null，但价格历史照写 ——
      价格本身是真实的，只是那几天算不出股息率。
    """
    if not settings.watchlist_backfill_enabled:
        return 0

    today = today or today_in_shanghai()
    full = bool(getattr(settings, "watchlist_backfill_full", True))

    existing = {
        row[0]
        for row in db.query(WatchlistSnapshot.trade_date)
        .filter(WatchlistSnapshot.item_id == item.id)
        .all()
    }
    first_existing = min(existing) if existing else None

    # 已经拉到过上市日、且库里最早的一条就在那一天或更早 → 没有缺口，直接跳过。
    # （新标的没有快照 → first_existing is None → 必然走下面的回填）
    known_earliest = _EARLIEST_BAR.get(normalize_code(item.code or ""))
    if full and known_earliest is not None and first_existing is not None:
        if first_existing <= known_earliest:
            return 0

    span = int(days if days is not None else settings.watchlist_backfill_days)
    if full:
        closes = await fetch_daily_closes(item.code, today=today)
    else:
        closes = await fetch_daily_closes(item.code, days=span, today=today)
    if not closes:
        return 0

    records = await fetch_dividend_from_eastmoney(item.code) or []
    rows = build_history_rows(closes, records)

    inserted = 0
    for row in rows:
        if row["trade_date"] in existing:
            continue  # 只补缺，当天实时快照优先
        db.add(WatchlistSnapshot(item_id=item.id, **row))
        inserted += 1

    if inserted:
        db.commit()
        logger.info(
            "观察池历史回填 [%s] %d 条（%s ~ %s）",
            item.code,
            inserted,
            rows[0]["trade_date"],
            rows[-1]["trade_date"],
        )
    return inserted
