"""定时任务引擎：注册表 + 每日调度循环 + 两个内置任务。

## 有哪两个任务

| key | 干什么 | 默认时刻 |
|---|---|---|
| `watchlist_snapshot` | 观察池：把每只标的的「当天快照」写进 `watchlist_snapshots` | 15:30 |
| `asset_price_refresh` | 投资组合：把所有资产的最新价刷成收盘价 | 15:05 |

时刻都在 `.env` 里改，且**收盘后再跑**：15:00 收盘，15:05 取到的是当日收盘价；
15:30 留出一点间隔，避免两个任务同时打行情接口。

## 怎么跑

- `main.py` 的 lifespan 用 `asyncio.create_task` 启动，关闭时 cancel + await 收尾；
- 循环体**先 tick 一次再 sleep 60 秒**（服务启动即补跑，不等一分钟）；
- 每次 tick：对每个任务判断「已启用 → 到了今天该跑的时刻 → 今天还没成功跑过 →
  今天尝试次数未超上限」，满足才执行；
- 同一任务同一天最多尝试 3 轮（外部源整体挂掉时，不至于每分钟重试一整天）。

## 运行记录

每次真正执行都写一条 `job_runs`；后台管理页的「最后运行时间 / 历史」全部来自这张表。
「到点但没有活干」（例如观察池今天已经齐了）**每天只留一条 skipped**：
既让人在后台看得到「今天跑过、确实无需更新」，又不会每分钟攒一条垃圾记录。
手动触发一定落库 —— 用户点了按钮，就该看到一条记录。

## 单 worker 假设

`--workers N > 1` 时每个进程都会跑这个循环，写库是幂等的（快照 UPSERT、价格重复写同一个值），
但会重复打接口。README 已注明，本期不额外处理。
"""
import asyncio
import json
import logging
import time as time_module
from dataclasses import dataclass, field
from datetime import date, datetime, time as time_of_day, timedelta, timezone
from typing import Awaitable, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from config import settings
from database import SessionLocal
from models import Asset, JobRun, WatchlistItem, WatchlistSnapshot
from routers.assets import fetch_real_time_price
from routers.watchlist import _refresh_one

logger = logging.getLogger(__name__)

SHANGHAI = ZoneInfo("Asia/Shanghai")

# 任务状态白名单（与前端 utils/jobs.js 的 STATUS_META 一一对应）
JOB_STATUSES = ("running", "success", "partial", "failed", "skipped")

MAX_ATTEMPTS_PER_DAY = 3
POLL_INTERVAL_SECONDS = 60

DEFAULT_WATCHLIST_TIME = "15:30"
DEFAULT_ASSET_PRICE_TIME = "15:05"

# (job_key, 日期) -> 今天已尝试轮数。模块级，避免外部源挂掉时每分钟重试。
_attempt_state: Dict[tuple, int] = {}


# ========== 时间工具 ==========

def now_local() -> datetime:
    return datetime.now(SHANGHAI)


def now_local_naive() -> datetime:
    """存库用：SQLite 不保存时区，统一存 Asia/Shanghai 的墙上时间。"""
    return now_local().replace(tzinfo=None)


def parse_hhmm(raw: str, fallback: str, label: str = "") -> time_of_day:
    """解析 "HH:MM"。任何非法输入都回退默认值（并记 warning），**绝不抛异常**。

    启动即崩是最糟的失败模式：用户改错一个字符，整个服务起不来，
    而正确行为是「用默认时间跑，日志里提醒一句」。
    """
    text = (raw or "").strip()
    try:
        hour_text, minute_text = text.split(":")
        hour, minute = int(hour_text), int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("out of range")
        return time_of_day(hour, minute)
    except Exception:
        logger.warning("%s=%r 解析失败，回退 %s", label or "时间配置", raw, fallback)
        hour_text, minute_text = fallback.split(":")
        return time_of_day(int(hour_text), int(minute_text))


def next_run_at(spec: "JobSpec", now: Optional[datetime] = None) -> datetime:
    """下一次计划运行时刻：今天的时刻没过就是今天，否则明天。"""
    current = now or now_local()
    candidate = datetime.combine(current.date(), spec.schedule_time, tzinfo=SHANGHAI)
    if candidate <= current:
        candidate += timedelta(days=1)
    return candidate


# ========== 任务结果与定义 ==========

@dataclass
class JobResult:
    """一次任务的原始结果，由引擎翻译成 job_runs 的一行。"""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    message: str = ""
    errors: List[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.total == 0:
            return "skipped"
        if self.failed == 0:
            return "success"
        if self.succeeded == 0:
            return "failed"
        return "partial"


@dataclass(frozen=True)
class JobSpec:
    key: str
    name: str
    description: str
    enabled_attr: str        # settings 上的开关字段名
    time_attr: str           # settings 上的时刻字段名
    default_time: str
    runner: Callable[[Session], Awaitable[JobResult]]

    @property
    def enabled(self) -> bool:
        return bool(getattr(settings, self.enabled_attr))

    @property
    def schedule_time(self) -> time_of_day:
        return parse_hhmm(
            getattr(settings, self.time_attr), self.default_time, self.time_attr.upper()
        )

    @property
    def schedule_label(self) -> str:
        return f"每天 {self.schedule_time.strftime('%H:%M')}（Asia/Shanghai）"


# ========== 任务 1：观察池每日快照 ==========

def items_missing_snapshot(db: Session, today: date) -> List[WatchlistItem]:
    """今天还没有快照的标的 = 所有标的 − 已有 today 快照的 item_id。"""
    done = {
        row[0]
        for row in db.query(WatchlistSnapshot.item_id)
        .filter(WatchlistSnapshot.trade_date == today)
        .all()
    }
    return [
        item
        for item in db.query(WatchlistItem).order_by(WatchlistItem.id.asc()).all()
        if item.id not in done
    ]


async def run_watchlist_snapshot(db: Session) -> JobResult:
    """给每只「今天还没有快照」的观察标的抓一次行情并落库。

    只补缺、不覆盖：已有今天的快照就跳过（当天手动刷新过的不会被收盘价覆盖）。
    """
    today = now_local().date()
    missing = items_missing_snapshot(db, today)
    result = JobResult(total=len(missing))
    if not missing:
        result.message = f"{today} 的快照已齐，无需更新"
        return result

    for item in missing:
        try:
            error = await _refresh_one(db, item)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                     # 单条失败不能把整批拖崩
            logger.exception("watchlist job: refresh failed code=%s", item.code)
            db.rollback()
            result.failed += 1
            result.errors.append(f"{item.code} {item.name}: {type(exc).__name__} {exc}")
            continue
        if error:
            result.failed += 1
            result.errors.append(f"{item.code} {item.name}: {error}")
        else:
            result.succeeded += 1

    result.message = f"{today} 快照 {result.succeeded}/{len(missing)} 成功"
    return result


# ========== 任务 2：投资组合资产价格 ==========

async def run_asset_price_refresh(db: Session) -> JobResult:
    """把投资组合里所有资产的最新价刷成当前行情价（只改价，不碰分红口径）。

    设计取舍：
    - **同一代码只请求一次**：同一只票可能同时出现在多个组合里，重复打接口既慢
      又容易被行情源限流；
    - **只写真实价**（`source == "real"`）：mock 价是 ±3% 随机数，写进库会污染
      盈亏与仓位占比，宁可这一轮算失败、保留上次的价格；
    - **不动 `dividend_per_share`**：这是「TTM 分红」，由打开资产时/手动刷新按分红
      历史计算，与每日收盘价无关。这里只用它重算 `ttm_yield`（纯派生，不落事实）。
    """
    assets = db.query(Asset).order_by(Asset.id.asc()).all()
    result = JobResult(total=len(assets))
    if not assets:
        result.message = "没有任何资产，跳过"
        return result

    by_code: Dict[str, List[Asset]] = {}
    for asset in assets:
        by_code.setdefault(asset.code, []).append(asset)

    for code, group in by_code.items():
        try:
            quote = await fetch_real_time_price(code)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("asset price job: quote failed code=%s (%s)", code, exc)
            result.failed += len(group)
            result.errors.append(f"{code}: 行情请求异常 {type(exc).__name__} {exc}")
            continue

        price = (quote or {}).get("price")
        if (quote or {}).get("source") != "real" or not price or float(price) <= 0:
            result.failed += len(group)
            reason = (quote or {}).get("error") or "未取到真实行情"
            result.errors.append(f"{code}: {reason}")
            continue

        price = float(price)
        for asset in group:
            asset.price = price
            asset.price_source = "real"
            asset.price_fetched_at = datetime.now(timezone.utc)
            if (asset.dividend_per_share or 0) > 0:
                asset.ttm_yield = round(asset.dividend_per_share / price * 100, 2)
        result.succeeded += len(group)

    db.commit()
    result.message = (
        f"更新 {result.succeeded}/{result.total} 个资产"
        f"（{len(by_code)} 只标的）"
    )
    return result


# ========== 注册表 ==========

def get_job_specs() -> Dict[str, JobSpec]:
    """任务注册表。时刻与开关都现读 settings，测试可以 monkeypatch。"""
    specs = (
        JobSpec(
            key="asset_price_refresh",
            name="投资组合价格刷新",
            description=(
                "把投资组合里全部资产的最新价刷成当日行情价（只写真实价，"
                "同代码只请求一次）。收盘后运行，避免拿到盘中抖动价。"
            ),
            enabled_attr="asset_price_refresh_enabled",
            time_attr="asset_price_refresh_time",
            default_time=DEFAULT_ASSET_PRICE_TIME,
            runner=run_asset_price_refresh,
        ),
        JobSpec(
            key="watchlist_snapshot",
            name="观察池每日快照",
            description=(
                "给每只观察标的抓一次行情与 TTM 分红，写入当天快照；"
                "只补缺不覆盖，已有的今天快照保持原样。"
            ),
            enabled_attr="watchlist_snapshot_enabled",
            time_attr="watchlist_snapshot_time",
            default_time=DEFAULT_WATCHLIST_TIME,
            runner=run_watchlist_snapshot,
        ),
    )
    return {spec.key: spec for spec in specs}


def get_job_spec(key: str) -> Optional[JobSpec]:
    return get_job_specs().get(key)


# ========== 执行与记录 ==========

def _today_start(today: date) -> datetime:
    return datetime.combine(today, time_of_day.min)


def ran_successfully_today(db: Session, job_key: str, today: date) -> bool:
    """今天是否已有「结论性」的运行，有就不再重复调度。

    结论性 = success（跑成功）或 skipped（到点了、但本来就没活干）。
    后者也要算：否则每天 15:05 之后的每一分钟都会重新判断一次「有没有活」，
    白白消耗 3 次重试额度，后台管理页也看不到「今天跑过、无需更新」这件事。
    partial / failed 不算 —— 留给 MAX_ATTEMPTS_PER_DAY 次重试。
    """
    return (
        db.query(JobRun.id)
        .filter(
            JobRun.job_key == job_key,
            JobRun.status.in_(("success", "skipped")),
            JobRun.started_at >= _today_start(today),
        )
        .first()
        is not None
    )


def _prune_attempt_state(today: date) -> None:
    for key in [k for k in _attempt_state if k[1] != today]:
        _attempt_state.pop(key, None)


def attempts_today(job_key: str, today: date) -> int:
    return _attempt_state.get((job_key, today), 0)


def _bump_attempts(job_key: str, today: date) -> int:
    _attempt_state[(job_key, today)] = attempts_today(job_key, today) + 1
    return _attempt_state[(job_key, today)]


def reset_attempt_state() -> None:
    """测试用：清掉「今天试过几次」的内存计数。"""
    _attempt_state.clear()


async def execute_job(
    key: str,
    trigger: str = "schedule",
    db: Optional[Session] = None,
) -> Optional[JobRun]:
    """执行一个任务并落一条 `job_runs`。

    返回 None 表示「调度触发但没有活干」，刻意不落库（见模块 docstring）。
    """
    spec = get_job_spec(key)
    if spec is None:
        raise KeyError(key)

    own_session = db is None
    session = db or SessionLocal()
    run = JobRun(
        job_key=spec.key,
        trigger=trigger,
        status="running",
        started_at=now_local_naive(),
        message="",
    )
    started = time_module.perf_counter()
    try:
        session.add(run)
        session.commit()
        session.refresh(run)

        try:
            result = await spec.runner(session)
        except asyncio.CancelledError:
            run.status = "failed"
            run.message = "任务被取消（服务关闭）"
            raise
        except Exception as exc:                  # runner 自己没兜住的异常
            logger.exception("job %s crashed", spec.key)
            session.rollback()
            run.status = "failed"
            run.message = f"{type(exc).__name__}: {exc}"[:500]

        if run.status == "running":
            run.total = result.total
            run.succeeded = result.succeeded
            run.failed = result.failed
            run.status = result.status
            run.message = (result.message or "")[:500]
            run.errors = json.dumps(result.errors[:20], ensure_ascii=False)
    finally:
        run.finished_at = now_local_naive()
        run.duration_ms = int((time_module.perf_counter() - started) * 1000)
        try:
            session.commit()
        except Exception:                          # 记录写失败不能反过来炸掉调度
            logger.exception("job %s: 运行记录写入失败", key)
            session.rollback()

    if run.status == "skipped" and trigger == "schedule":
        # 每天**最多留一条**「跳过」：后台管理页要看得到「到点跑了、确实没活干」，
        # 但绝不能每分钟一条（一天 1000+ 条垃圾）。
        today = run.started_at.date()
        has_earlier = (
            session.query(JobRun.id)
            .filter(
                JobRun.job_key == key,
                JobRun.id != run.id,
                JobRun.started_at >= _today_start(today),
            )
            .first()
            is not None
        )
        if has_earlier:
            try:
                session.delete(run)
                session.commit()
            except Exception:
                logger.exception("job %s: 清理空转记录失败", key)
                session.rollback()
            if own_session:
                session.close()
            return None

    if own_session:
        # 返回前把对象从 session 上摘下来，调用方（路由）序列化时不会触发懒加载
        session.refresh(run)
        session.expunge(run)
        session.close()
    return run


# ========== 调度循环 ==========

async def tick(now: Optional[datetime] = None) -> List[JobRun]:
    """执行一轮：把「该跑且没跑过」的任务跑掉，返回本轮真正落库的运行记录。"""
    current = now or now_local()
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI)

    today = current.date()
    _prune_attempt_state(today)
    ran: List[JobRun] = []

    db = SessionLocal()
    try:
        for spec in get_job_specs().values():
            if not spec.enabled:
                continue
            if current.time() < spec.schedule_time:
                continue
            if attempts_today(spec.key, today) >= MAX_ATTEMPTS_PER_DAY:
                continue
            if ran_successfully_today(db, spec.key, today):
                continue

            attempt = _bump_attempts(spec.key, today)
            logger.info(
                "scheduler: running %s (attempt %d/%d)", spec.key, attempt, MAX_ATTEMPTS_PER_DAY
            )
            run = await execute_job(spec.key, trigger="schedule", db=db)
            if run is not None:
                ran.append(run)
                logger.info(
                    "scheduler: %s -> %s (%dms) %s",
                    spec.key, run.status, run.duration_ms, run.message,
                )
    finally:
        db.close()
    return ran


async def run_scheduler() -> None:
    """无限循环：启动立刻 tick 一次，之后每 60 秒一次。"""
    specs = get_job_specs()
    logger.info(
        "scheduler started: %s",
        "; ".join(
            f"{s.key}@{s.schedule_time.strftime('%H:%M')}(enabled={s.enabled})"
            for s in specs.values()
        ),
    )
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception:                          # 任何异常都不能让后台任务静默死掉
            logger.exception("scheduler tick failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
