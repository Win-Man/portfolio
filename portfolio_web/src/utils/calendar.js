/**
 * 「收入日历」纯函数口径层（spec §10.2 唯一能被单测锁定的部分）。
 *
 * 设计原则：
 *   1. **和后端算同一套口径**：status / source 的映射规则与 `portfolio_math.classify_*` 一一对应，
 *      前端只在「后端没给 / 需要兜底」时用得上，保证两边不会各说各话；
 *   2. **纯函数**：不碰 DOM、不碰 fetch、不碰 localStorage，node --test 可直接跑；
 *   3. **永远返回 12 个槽位**：日历是「12 个月」的固定形状，缺数据的月份补 0，
 *      避免调用方到处写 `?? 0` 与越界判断。
 *
 * 颜色 / 文案常量在 `utils/calendarColors.js`（不要在计算函数里硬编码颜色）。
 */
import { SOURCE_COLORS, SOURCE_LABELS, SOURCE_ORDER } from './calendarColors.js'

/** 来源大类 key（顺序 = 展示顺序，和后端 by_source 的 key 一致） */
export const SOURCE_KEYS = [...SOURCE_ORDER]

/** 状态 key（spec D4：predicted / announced / paid / manual） */
export const STATUS_KEYS = ['predicted', 'announced', 'paid', 'manual']

/** 保留 2 位小数，消掉 0.1 + 0.2 这类浮点噪声（金额口径） */
function round2(n) {
  const v = Number(n)
  if (!Number.isFinite(v)) return 0
  return Math.round((v + Number.EPSILON) * 100) / 100
}

function num(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

/** 4 类状态的全 0 字典 */
function zeroStatus() {
  const out = {}
  for (const key of STATUS_KEYS) out[key] = 0
  return out
}

/** 8 类来源的全 0 字典 */
function zeroSource() {
  const out = {}
  for (const key of SOURCE_KEYS) out[key] = 0
  return out
}

/**
 * 取一笔记录的金额：优先用后端给的 `amount`，缺失时回退 `quantity × dividend_per_share`。
 * 注意不能用 `Number(x) || 0` 判断——`amount: 0` 是合法值（quantity=0 的资产）。
 * @param {object|null|undefined} record
 * @returns {number}
 */
export function recordAmount(record) {
  if (!record || typeof record !== 'object') return 0
  if (record.amount !== null && record.amount !== undefined) {
    const v = Number(record.amount)
    if (Number.isFinite(v)) return v
  }
  const quantity = Number(record.quantity)
  const perShare = Number(record.dividend_per_share)
  if (Number.isFinite(quantity) && Number.isFinite(perShare)) return quantity * perShare
  return 0
}

/**
 * 解析派息日 → `{ year, month, day }`。支持 `YYYY-MM-DD` / ISO 时间串 / Date 对象。
 * 解析不出来返回 null（调用方跳过该条，spec §6.4：ex_date 解析失败不影响其他记录）。
 * @param {string|Date|null|undefined} value
 * @returns {{year:number, month:number, day:number}|null}
 */
export function parseExDate(value) {
  if (value === null || value === undefined || value === '') return null
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) return null
    return { year: value.getFullYear(), month: value.getMonth() + 1, day: value.getDate() }
  }
  const text = String(value).trim()
  const matched = text.match(/^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?/)
  if (matched) {
    const month = Number(matched[2])
    const day = matched[3] === undefined ? 1 : Number(matched[3])
    if (month >= 1 && month <= 12) return { year: Number(matched[1]), month, day }
    return null
  }
  const parsed = new Date(text)
  if (Number.isNaN(parsed.getTime())) return null
  return { year: parsed.getFullYear(), month: parsed.getMonth() + 1, day: parsed.getDate() }
}

/**
 * 派息日展示为 `10月16日`（月 Sheet 行内用，spec §7.4.4）。
 * @param {string|Date|null|undefined} exDate
 * @returns {string} 解析失败返回空串
 */
export function formatDayLabel(exDate) {
  const parsed = parseExDate(exDate)
  return parsed ? `${parsed.month}月${parsed.day}日` : ''
}

/**
 * `YYYY年MM月`（spec §10.2-8）：月不补 0，`formatMonthLabel(2026, 10) === '2026年10月'`。
 * @param {number|string} year
 * @param {number|string} month
 * @returns {string}
 */
export function formatMonthLabel(year, month) {
  return `${Number(year)}年${Number(month)}月`
}

/**
 * 该月是否属于「未来、且 akshare 尚未收录」的月份（spec D2：不外推）。
 *
 * 用途：年视图把这类月份的 ¥0.00 标成「待收录」，而不是让人误以为「这个月真的没有分红」，
 * 也用于提示「自然年合计」为什么小于概览的「预计年收入」。
 * 只有**严格晚于当前年月**才算待收录：往年没记录的月份是「确实没有」，不是「还没收录」。
 *
 * @param {number|string} year
 * @param {number|string} month 1..12
 * @param {Date} [today]
 * @returns {boolean}
 */
export function isPendingMonth(year, month, today = new Date()) {
  const y = Number(year)
  const m = Number(month)
  if (!Number.isFinite(y) || !Number.isFinite(m) || m < 1 || m > 12) return false
  const nowYear = today.getFullYear()
  const nowMonth = today.getMonth() + 1
  return y > nowYear || (y === nowYear && m > nowMonth)
}

/**
 * 某个年份里「待收录」的月份列表（1..12，升序）。
 * @param {number|string} year
 * @param {Date} [today]
 * @returns {number[]}
 */
export function pendingMonthsOfYear(year, today = new Date()) {
  const out = []
  for (let month = 1; month <= 12; month++) {
    if (isPendingMonth(year, month, today)) out.push(month)
  }
  return out
}

/**
 * 把若干笔分红记录按**派息日月份**分箱，永远返回 12 个 CalendarMonth 形状的对象。
 *
 * @param {Array<{ex_date?:string, amount?:number, quantity?:number, dividend_per_share?:number,
 *                status?:string, source?:string}>} records
 * @param {number|string} [year] 只统计该年；不传 / 非法时不按年过滤
 * @returns {Array<{year:number|null, month:number, total_amount:number, count:number,
 *                  by_status:Record<string,number>, by_source:Record<string,number>}>}
 */
export function bucketByMonth(records, year) {
  const list = Array.isArray(records) ? records : []
  const yearNumber = Number(year)
  const targetYear = year !== null && year !== undefined && year !== '' && Number.isFinite(yearNumber)
    ? yearNumber
    : null

  const months = []
  for (let month = 1; month <= 12; month++) {
    months.push({
      year: targetYear,
      month,
      total_amount: 0,
      count: 0,
      by_status: zeroStatus(),
      by_source: zeroSource(),
    })
  }

  for (const record of list) {
    const parsed = parseExDate(record?.ex_date)
    if (!parsed) continue
    if (targetYear !== null && parsed.year !== targetYear) continue
    const bucket = months[parsed.month - 1]
    if (!bucket) continue

    const amount = recordAmount(record)
    const status = STATUS_KEYS.includes(record?.status) ? record.status : 'predicted'
    const source = SOURCE_KEYS.includes(record?.source) ? record.source : 'stock'

    bucket.count += 1
    bucket.total_amount = round2(bucket.total_amount + amount)
    bucket.by_status[status] = round2(bucket.by_status[status] + amount)
    bucket.by_source[source] = round2(bucket.by_source[source] + amount)
  }

  return months
}

/**
 * 按状态合计金额（4 类状态全部零填充；未知状态忽略，不猜）。
 * @param {Array<{amount?:number, quantity?:number, dividend_per_share?:number, status?:string}>} items
 * @returns {Record<'predicted'|'announced'|'paid'|'manual', number>}
 */
export function sumByStatus(items) {
  const out = zeroStatus()
  for (const item of Array.isArray(items) ? items : []) {
    const status = STATUS_KEYS.includes(item?.status) ? item.status : null
    if (!status) continue
    out[status] = round2(out[status] + recordAmount(item))
  }
  return out
}

/**
 * 按来源合计金额（8 类来源全部零填充；未知来源忽略）。
 * @param {Array<{amount?:number, quantity?:number, dividend_per_share?:number, source?:string}>} items
 * @returns {Record<string, number>}
 */
export function sumBySource(items) {
  const out = zeroSource()
  for (const item of Array.isArray(items) ? items : []) {
    const source = SOURCE_KEYS.includes(item?.source) ? item.source : null
    if (!source) continue
    out[source] = round2(out[source] + recordAmount(item))
  }
  return out
}

/**
 * akshare 的 `IMPLEMENT_STATUS` → 内部 status（spec D5 / §8-5）。
 *
 * 规则：
 *   - 含「已实施」→ paid（已到账）
 *   - 含「已发布」/「实施中」→ announced（待到账）
 *   - 其他（董事会预案 / 股东大会通过 / 缺失 / 不识别）→ predicted（保守）
 *   - 只要含「未」（未实施 / 尚未实施）→ 一律 predicted，绝不误判成已到账
 *
 * @param {string|null|undefined} implStatus
 * @returns {'paid'|'announced'|'predicted'}
 */
export function classifyStatusFromImpl(implStatus) {
  if (implStatus === null || implStatus === undefined) return 'predicted'
  const text = String(implStatus).trim()
  if (!text) return 'predicted'
  if (text.includes('未')) return 'predicted'
  if (text.includes('已实施')) return 'paid'
  if (text.includes('已发布') || text.includes('实施中')) return 'announced'
  return 'predicted'
}

/**
 * 资产 → 来源大类（与后端 `classify_source(asset, is_manual)` 同口径，spec §6.2）。
 *
 *   - 手动补录 → manual
 *   - ETF / 基金代码（5 / 15 / 16 / 18 开头）→ etf
 *   - asset_type 含「债」→ bond；含 OTC → otc；含 REITs → reit；
 *     含「自建」→ self；含「自定义」→ custom
 *   - 其余（含缺字段 / 不识别）→ stock（兜底）
 *
 * @param {{code?:string, asset_type?:string, type?:string}|null|undefined} asset
 * @param {boolean} [isManual]
 * @returns {'stock'|'etf'|'bond'|'otc'|'reit'|'custom'|'self'|'manual'}
 */
export function classifySource(asset, isManual = false) {
  if (isManual) return 'manual'
  const code = String(asset?.code ?? '').trim()
  if (/^(5|15|16|18)/.test(code)) return 'etf'
  const assetType = String(asset?.asset_type ?? asset?.type ?? '').trim()
  const upper = assetType.toUpperCase()
  if (assetType.includes('债')) return 'bond'
  if (upper.includes('OTC')) return 'otc'
  if (upper.includes('REITS') || upper.includes('REIT')) return 'reit'
  if (assetType.includes('自建')) return 'self'
  if (assetType.includes('自定义')) return 'custom'
  return 'stock'
}

/** 单月合计：兼容「直接给数字」与「给 CalendarMonth 对象」两种入参 */
function monthValue(month) {
  if (month === null || month === undefined) return 0
  if (typeof month === 'object') return num(month.total_amount)
  return num(month)
}

/**
 * 年度累计折线：返回 12 个累计值，最后一项 = 全年合计（spec D10 / §10.2-6）。
 * 入参可以是 12 个数字，也可以是 12 个 CalendarMonth 对象。
 * @param {Array<number|{total_amount?:number}>} monthTotals
 * @returns {number[]} 长度恒为 12
 */
export function cumulativeLine(monthTotals) {
  const list = Array.isArray(monthTotals) ? monthTotals : []
  const out = []
  let acc = 0
  for (let i = 0; i < 12; i++) {
    acc = round2(acc + monthValue(list[i]))
    out.push(acc)
  }
  return out
}

/**
 * ECharts 堆叠柱 series 数据（spec D10 / §10.2-7）。
 *
 * @param {Array<{by_source?:Record<string, number>}>} monthTotals 12 个月的 CalendarMonth
 * @param {string[]} [sources] 固定图例顺序，默认 8 类来源全量
 * @returns {Array<{name:string, data:number[], stack:'total', itemStyle:{color:string}}>}
 */
export function stackedBarData(monthTotals, sources = SOURCE_ORDER) {
  const list = Array.isArray(monthTotals) ? monthTotals : []
  const keys = Array.isArray(sources) && sources.length > 0 ? sources : SOURCE_ORDER
  return keys.map((key) => ({
    name: SOURCE_LABELS[key] ?? key,
    data: Array.from({ length: 12 }, (_, i) => {
      const month = list[i]
      const value = month && typeof month === 'object' ? month.by_source?.[key] : 0
      return round2(num(value))
    }),
    stack: 'total',
    itemStyle: { color: SOURCE_COLORS[key] ?? SOURCE_COLORS.stock },
  }))
}
