/**
 * 观察池的**展示用**纯函数（D9：所有口径与信号都在后端算）。
 *
 * 这里**没有**任何阈值比较 —— 不做「股息率是否 ≥ 目标 × 0.97」之类的判断，
 * 那是 `watchlist_math.py` 的职责。前端只做四类事情：
 *
 *   1. 信号 → 文案 / 颜色（来自 utils/watchlistColors.js）
 *   2. 列表排序（默认把已达标顶到前面；表头可改按 6 个数值列排序）
 *   3. 曲线序列构建（日期升序、null 原样保留 = 断线，不插值）
 *   4. 展示格式化（2 位小数、正负号、无意义值给「—」）
 *
 * 所有函数对 null / undefined / 非数组入参都必须安全（不抛异常）。
 */
import { SIGNAL_COLORS, SIGNAL_LABELS } from './watchlistColors.js'

/** 「陈旧」阈值：与后端 watchlist_math.STALE_DAYS_THRESHOLD 保持一致（5 个自然日） */
export const STALE_DAYS_THRESHOLD = 5

const MS_PER_DAY = 24 * 60 * 60 * 1000

/** 转数字；null / undefined / 空串 / NaN / Infinity → null（null 表示「不适用」） */
export function toNumberOrNull(value) {
  if (value === null || value === undefined || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

/**
 * 信号 → 中文文案。未知取值统一显示「数据不足」，不返回 undefined。
 * @param {string} signal
 * @returns {string}
 */
export function signalLabel(signal) {
  return SIGNAL_LABELS[signal] ?? SIGNAL_LABELS.unknown
}

/**
 * 信号 → 主色（#rrggbb）。未知取值退化成 unknown 的浅灰。
 * @param {string} signal
 * @returns {string}
 */
export function signalTone(signal) {
  return SIGNAL_COLORS[signal] ?? SIGNAL_COLORS.unknown
}

/** 排序档位：hit < near < watch < unknown/not_set（数字越小越靠前） */
const SIGNAL_RANK = { hit: 0, near: 1, watch: 2, unknown: 3, not_set: 3 }

/** 一条观察项的「最佳信号档位」：两条线取更好的那条（任一达标即排前） */
function bestRank(item) {
  const a = SIGNAL_RANK[item?.yield_signal] ?? 3
  const b = SIGNAL_RANK[item?.price_signal] ?? 3
  return Math.min(a, b)
}

/**
 * 列表排序：已达标在前 → 接近 → 观察中 → 数据不足 / 未设目标价。
 * 同档按「距目标（百分点）」降序（越接近甚至超过目标越靠前），都为空时按 id 升序。
 *
 * 返回新数组，**不修改入参**。
 * @param {Array<object>} items
 * @returns {Array<object>}
 */
export function sortForDisplay(items) {
  const list = Array.isArray(items) ? [...items] : []
  return list.sort((a, b) => {
    const rankDiff = bestRank(a) - bestRank(b)
    if (rankDiff !== 0) return rankDiff

    const gapA = toNumberOrNull(a?.yield_gap_points)
    const gapB = toNumberOrNull(b?.yield_gap_points)
    if (gapA === null && gapB === null) return (a?.id ?? 0) - (b?.id ?? 0)
    if (gapA === null) return 1
    if (gapB === null) return -1
    if (gapB !== gapA) return gapB - gapA
    return (a?.id ?? 0) - (b?.id ?? 0)
  })
}

/**
 * 表头可排序的 6 个数值列。`key` 直接就是接口返回的字段名，`label` 是表头文案 ——
 * 放在一处定义，避免表头改了名字、排序却还在读旧字段（或反过来）。
 *
 * 这 6 列正是「观察标的」表里从「当前股息率」到「目标价」的连续区间；
 * 标的名称、现价、TTM 分红、信号、数据日期、操作不参与排序。
 */
export const SORTABLE_COLUMNS = [
  { key: 'current_yield', label: '当前股息率' },
  { key: 'target_yield', label: '目标股息率' },
  { key: 'yield_gap_points', label: '距目标' },
  { key: 'yield_trigger_price', label: '触发价' },
  { key: 'yield_trigger_gap_percent', label: '距触发价' },
  { key: 'target_price', label: '目标价' },
]

const SORTABLE_KEYS = new Set(SORTABLE_COLUMNS.map((col) => col.key))

/**
 * 按指定数值列排序（表头点击时用）。与 `sortForDisplay` 的关系：
 *
 * - **基准顺序是默认排序**。值相等时靠 JS 稳定排序保持「已达标优先」的次序，
 *   而不是退回按 id —— 用户按「当前股息率」排完，同息率的两只依然是达标的在前。
 * - **空值永远沉底**：`null` / 非数字代表「未设目标价」「无分红算不出触发价」，
 *   它不是 0。升序时若按 0 处理会集体冲到最前，降序时又会被真实价格盖住，
 *   两种方向都不该让「— / 未设」混进有效数据里。
 * - 只认 `SORTABLE_COLUMNS` 里的列，未知 key 退回默认排序（不悄悄按任意字段排）。
 *
 * 返回新数组，**不修改入参**。
 *
 * @param {Array<object>} items
 * @param {string} key
 * @param {'asc'|'desc'} [order='asc']
 * @returns {Array<object>}
 */
export function sortByColumn(items, key, order = 'asc') {
  if (!SORTABLE_KEYS.has(key)) return sortForDisplay(items)

  const direction = order === 'desc' ? -1 : 1
  return sortForDisplay(items).sort((a, b) => {
    const valueA = toNumberOrNull(a?.[key])
    const valueB = toNumberOrNull(b?.[key])
    if (valueA === null && valueB === null) return 0
    if (valueA === null) return 1
    if (valueB === null) return -1
    if (valueA !== valueB) return (valueA - valueB) * direction
    return 0
  })
}

/**
 * 快照 → 双折线序列。
 *
 * - 日期升序（后端已升序，这里再排一次以免调用方乱序）；
 * - `ttm_yield` / `price` 为 null 时**原样保留 null**：ECharts 会断线，
 *   缺日就是缺数据，绝不插值、不回填（D15）；
 * - 空数组 → 三个空数组，不抛错。
 *
 * @param {Array<{trade_date:string, price:number|null, ttm_yield:number|null}>} snapshots
 * @returns {{dates: string[], prices: Array<number|null>, yields: Array<number|null>}}
 */
export function buildTrendSeries(snapshots) {
  const rows = Array.isArray(snapshots)
    ? snapshots.filter((row) => row && row.trade_date)
    : []
  rows.sort((a, b) => String(a.trade_date).localeCompare(String(b.trade_date)))

  const dates = []
  const prices = []
  const yields = []
  for (const row of rows) {
    dates.push(String(row.trade_date))
    prices.push(toNumberOrNull(row.price))
    yields.push(toNumberOrNull(row.ttm_yield))
  }
  return { dates, prices, yields }
}

/**
 * 带正负号的差值：正数补 "+"，负数用 Unicode 减号 "−"，小数固定 2 位。
 * null / 非数字 → 「—」（不要显示 +0.00）。
 *
 * @param {number|string|null|undefined} value
 * @param {string} [unit] 数值有效时追加的单位（如 " pct" / "%"）
 * @returns {string}
 */
export function formatGapPercent(value, unit = '') {
  const n = toNumberOrNull(value)
  if (n === null) return '—'
  const abs = Math.abs(n).toFixed(2)
  return `${n >= 0 ? '+' : '−'}${abs}${unit}`
}

/**
 * 股息率：2 位小数 + %。null → 「—」（无分红时不能显示 0.00%）。
 * @param {number|string|null|undefined} value
 * @returns {string}
 */
export function formatYield(value) {
  const n = toNumberOrNull(value)
  if (n === null) return '—'
  return `${n.toFixed(2)}%`
}

/**
 * 走势图 tooltip 的数值文本：统一 2 位小数。
 *
 * 后端刻意**不四舍五入**（D10：库里存全精度原始值），所以 3.762456546929316
 * 这类数字会原样进来 —— 精度收口只在展示层做，别让 tooltip 裸露 15 位小数。
 * null → 「—」（不适用，不是 0）。
 *
 * @param {number|string|null|undefined} value
 * @returns {string}
 */
export function formatChartTip(value) {
  const n = toNumberOrNull(value)
  return n === null ? '—' : n.toFixed(2)
}

/**
 * 除权日对齐到曲线横轴：把分红记录映射成「图上能画竖线的日期」。
 *
 * 两个必要性：
 *   1. 只保留落在当前窗口内的除权日 —— 切 30 / 90 / 180 天时不用重新请求，
 *      一个窗口的除权日列表可以直接裁剪复用；
 *   2. 除权日本身是交易日，但那天**可能缺快照**（回填缺日、停牌），
 *      category 轴上不存在的值画不出竖线 —— 顺延到之后最近的一个有数据的日期，
 *      保证「每条竖线都能落在曲线上」。都找不到就丢弃，不硬造。
 *
 * 返回 `[{ date, amount }]`，date 升序、amount 是当期每股分红（纯展示用）。
 *
 * @param {Array<string>} dates 曲线横轴（"YYYY-MM-DD"，升序）
 * @param {Array<{ex_date:string, dividend_per_share:number}>} dividends
 * @returns {Array<{date:string, amount:number}>}
 */
export function alignExDividends(dates, dividends) {
  const axis = Array.isArray(dates) ? dates.map((d) => String(d).slice(0, 10)) : []
  if (axis.length === 0) return []

  const first = axis[0]
  const last = axis[axis.length - 1]
  const rows = Array.isArray(dividends) ? dividends : []
  const out = []
  const seen = new Set()

  for (const row of rows) {
    const raw = row?.ex_date ? String(row.ex_date).slice(0, 10) : ''
    if (!raw) continue
    // 窗口外的除权日**直接丢弃**：早于起点的若也顺延，会在曲线最左端
    // 凭空多出一条竖线，等于伪造一个除权事实
    if (raw < first || raw > last) continue
    // 顺延到 ≥ ex_date 的第一个有数据的日期（缺快照 / 停牌那天画不出线）
    const snapped = axis.find((d) => d >= raw)
    if (!snapped || seen.has(snapped)) continue
    seen.add(snapped)
    out.push({ date: snapped, amount: toNumberOrNull(row?.dividend_per_share) ?? 0 })
  }

  out.sort((a, b) => a.date.localeCompare(b.date))
  return out
}

/** "YYYY-MM-DD" / Date → 天序号（UTC 零点，避免时区与夏令时把天数差算歪） */
function toDayNumber(value) {
  if (value === null || value === undefined || value === '') return null
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) return null
    return Math.floor(
      Date.UTC(value.getFullYear(), value.getMonth(), value.getDate()) / MS_PER_DAY
    )
  }
  const matched = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!matched) return null
  const [, year, month, day] = matched
  return Math.floor(Date.UTC(Number(year), Number(month) - 1, Number(day)) / MS_PER_DAY)
}

/**
 * 两个日期之间的自然日差（to − from）。任一不可解析 → null。
 * @param {string|Date|null|undefined} from
 * @param {string|Date|null|undefined} to
 * @returns {number|null}
 */
export function daysBetween(from, to) {
  const a = toDayNumber(from)
  const b = toDayNumber(to)
  if (a === null || b === null) return null
  return b - a
}

/**
 * 曲线首尾跨度（自然日）。不足两个点 → 0。
 * 用来决定横轴标签粒度：跨度很长时 `MM-DD` 会退化成一堆看不出年份的刻度。
 *
 * @param {Array<string>} dates 升序日期序列
 * @returns {number}
 */
export function spanDays(dates) {
  const axis = Array.isArray(dates) ? dates.filter(Boolean) : []
  if (axis.length < 2) return 0
  return daysBetween(axis[0], axis[axis.length - 1]) ?? 0
}

/** 千分位（固定实现，不用 toLocaleString —— 它依赖运行环境的 locale） */
function groupThousands(value) {
  return String(Math.trunc(Number(value) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

/**
 * 区间概要：给展开行显示「这段曲线到底覆盖了什么」。
 *
 * 长区间最容易出的错觉是「图上怎么只有一条直线」——把首尾日期与点数摊开写，
 * 用户一眼能确认「全部历史」是否真的取到了上市日。
 *
 * @param {Array<string>} dates
 * @returns {{first: string|null, last: string|null, count: number, days: number, label: string}}
 */
export function summarizeRange(dates) {
  const axis = Array.isArray(dates)
    ? dates.filter((d) => d !== null && d !== undefined && d !== '').map((d) => String(d).slice(0, 10))
    : []
  if (axis.length === 0) {
    return { first: null, last: null, count: 0, days: 0, label: '' }
  }
  const first = axis[0]
  const last = axis[axis.length - 1]
  const days = daysBetween(first, last) ?? 0
  return {
    first,
    last,
    count: axis.length,
    days,
    label: `${first} ~ ${last} · ${groupThousands(axis.length)} 个交易日`,
  }
}

/**
 * 自定义区间的默认起止：以最后一个数据日为终点，往前 `years` 年；
 * 历史不足 `years` 年时直接给全部。日期不可解析时返回空串（交给用户自己选）。
 *
 * @param {string|null} first 该标的最早可用日期
 * @param {string|null} last  该标的最近可用日期
 * @param {number} [years=3]
 * @returns {{start: string, end: string}}
 */
export function defaultCustomRange(first, last, years = 3) {
  const firstDay = toDayNumber(first)
  const lastDay = toDayNumber(last)
  if (firstDay === null || lastDay === null || firstDay > lastDay) {
    return { start: '', end: '' }
  }
  const startDay = Math.max(firstDay, lastDay - Math.round(365 * years))
  return {
    start: new Date(startDay * MS_PER_DAY).toISOString().slice(0, 10),
    end: new Date(lastDay * MS_PER_DAY).toISOString().slice(0, 10),
  }
}

/**
 * 时间维度 → 查询串（`/snapshots` 与 `/dividends` 共用同一套参数语义）。
 *
 *   `30`             → `days=30`
 *   `{ days: 30 }`   → `days=30`
 *   `{ all: true }`  → `all=true`
 *   `{ start, end }` → `start=…&end=…`（任一端可省）
 *
 * ⚠ 必须认识 `{ days }`：早期版本只认裸数字，传 `{days: 30}` 会静默退化成
 * 默认的 90 天 —— 切到「30 天」却画出 90 天的曲线，而且没有任何报错。
 *
 * @param {number|{days?:number, all?:boolean, start?:string, end?:string}} range
 * @returns {string}
 */
export function buildRangeQuery(range) {
  if (typeof range === 'number' && Number.isFinite(range)) {
    return `days=${Math.trunc(range)}`
  }

  const opts = range && typeof range === 'object' ? range : {}
  if (opts.all) return 'all=true'

  const params = new URLSearchParams()
  if (opts.start) params.set('start', String(opts.start).slice(0, 10))
  if (opts.end) params.set('end', String(opts.end).slice(0, 10))
  const ranged = params.toString()
  if (ranged) return ranged

  const days = Number(opts.days)
  return `days=${Number.isFinite(days) && days >= 1 ? Math.trunc(days) : 90}`
}

/**
 * 距今天几天（无快照 → null）。后端已给 stale_days，这里只用于本地前后一致的校验/兜底。
 * @param {string|Date|null|undefined} snapshotDate
 * @param {Date} [today=new Date()]
 * @returns {number|null}
 */
export function staleDays(snapshotDate, today = new Date()) {
  const from = toDayNumber(snapshotDate)
  const to = toDayNumber(today)
  if (from === null || to === null) return null
  return to - from
}

/**
 * 数据是否陈旧：与今天相差 **> 5 个自然日**（5 天不算，6 天才算）。
 * 无快照 → false（没数据 ≠ 数据陈旧，前端显示「待抓取」）。
 *
 * @param {string|Date|null|undefined} snapshotDate
 * @param {Date} [today=new Date()]
 * @returns {boolean}
 */
export function isDataStale(snapshotDate, today = new Date()) {
  const days = staleDays(snapshotDate, today)
  return days !== null && days > STALE_DAYS_THRESHOLD
}

/**
 * 汇总卡数据：
 *   - total              标的总数
 *   - yieldHitCount      股息率达标数（yield_signal === 'hit'）
 *   - priceHitCount      价格达标数（price_signal === 'hit'）
 *   - priceTargetedCount 价格线的分母：**未设目标价的不计入**
 *   - staleCount         陈旧项数
 *   - latestSnapshotDate 最新快照日期（"YYYY-MM-DD"，全无快照 → null）
 *
 * @param {Array<object>} items
 * @returns {{total:number, yieldHitCount:number, priceHitCount:number, priceTargetedCount:number, staleCount:number, latestSnapshotDate:string|null}}
 */
export function summarize(items) {
  const list = Array.isArray(items) ? items : []
  let yieldHitCount = 0
  let priceHitCount = 0
  let priceTargetedCount = 0
  let staleCount = 0
  let latestSnapshotDate = null

  for (const item of list) {
    if (!item) continue
    if (item.yield_signal === 'hit') yieldHitCount += 1
    if (item.price_signal !== 'not_set') {
      priceTargetedCount += 1
      if (item.price_signal === 'hit') priceHitCount += 1
    }
    if (item.is_stale) staleCount += 1

    const snapshotDate = item.snapshot_date ? String(item.snapshot_date) : null
    if (snapshotDate && (!latestSnapshotDate || snapshotDate > latestSnapshotDate)) {
      latestSnapshotDate = snapshotDate
    }
  }

  return {
    total: list.length,
    yieldHitCount,
    priceHitCount,
    priceTargetedCount,
    staleCount,
    latestSnapshotDate,
  }
}
