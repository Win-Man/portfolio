/**
 * 日报的**展示层**纯函数（口径计算全在后端，这里只负责「怎么说」）。
 *
 * 约定：
 * - 金额一律千分位 + 2 位小数，百分比 2 位小数；
 * - 无意义的值（null / undefined / NaN）显示「—」，**绝不显示 0.00**
 *   —— 「今天没涨没跌」和「不知道昨收是多少」是两件事；
 * - 涨跌配色遵循 A 股惯例：**红涨绿跌**（与欧美相反），返回的是 class 名，
 *   具体颜色在组件的样式里。
 */

import { priceDigits } from './format.js'

const DASH = '—'

function toNumber(value) {
  if (value === null || value === undefined || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

/** 千分位 + 固定小数位 */
function formatFixed(value, digits) {
  const n = toNumber(value)
  if (n === null) return DASH
  return n.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

/**
 * 带符号金额：正数 "+¥1,234.00"、负数 "−¥1,234.00"（Unicode 减号）、0/缺失 "—"。
 *
 * 0 也显示「—」：日报里「今日盈亏 0」几乎总是「没数据」的伪装，
 * 真正的「一分不涨不跌」用 0.00 没有信息量，反而让人以为算错了。
 *
 * 「负数但四舍五入到 0.00」（如 ETF 每股跌 0.003 元）同样按 0 处理 ——
 * 否则会出现刺眼的「−¥0.00」。
 */
export function signedAmount(value) {
  const n = toNumber(value)
  if (n === null) return DASH
  const rounded = Number(n.toFixed(2))
  if (rounded === 0) return DASH
  const sign = rounded > 0 ? '+' : '−'
  return `${sign}¥${formatFixed(Math.abs(rounded), 2)}`
}

/**
 * 带符号的「每股涨跌额」：ETF 要保留 3 位小数。
 *
 * 单独一个函数是因为全站金额统一 2 位，而价格的最小变动单位随标的类型不同
 * （股票 0.01 / 场内基金 0.001）：ETF 涨 0.003 元用金额格式会显示成「+¥0.00」，
 * 等于把一个真实波动抹掉了。
 */
export function signedPrice(value, assetType) {
  const n = toNumber(value)
  if (n === null) return DASH
  const digits = assetType ? priceDigits(assetType) : 2
  const rounded = Number(n.toFixed(digits))
  if (rounded === 0) return DASH
  const sign = rounded > 0 ? '+' : '−'
  return (
    sign +
    Math.abs(rounded).toLocaleString('zh-CN', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    })
  )
}

/** 带符号百分比：+1.23% / −1.23% / — */
export function signedPercent(value, digits = 2) {
  const n = toNumber(value)
  if (n === null) return DASH
  const sign = n > 0 ? '+' : n < 0 ? '−' : ''
  return `${sign}${formatFixed(Math.abs(n), digits)}%`
}

/** 普通百分比（不带符号）：4.82% / — */
export function plainPercent(value, digits = 2) {
  const n = toNumber(value)
  if (n === null) return DASH
  return `${formatFixed(n, digits)}%`
}

/** 金额（不带符号）：11,422.50 / — */
export function plainAmount(value, digits = 2) {
  return formatFixed(value, digits)
}

/** 数量：不强制小数位（0.5 份基金照样显示 0.5） */
export function plainQuantity(value) {
  const n = toNumber(value)
  if (n === null) return DASH
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 3 })
}

/** 涨跌配色 class：A 股惯例红涨绿跌 */
export function pnlTone(value) {
  const n = toNumber(value)
  if (n === null || n === 0) return 'is-flat'
  return n > 0 ? 'is-up' : 'is-down'
}

/** 股息率线信号 → 文案与色调（与 watchlist_math 的白名单一一对应） */
const YIELD_SIGNAL_META = {
  hit: { label: '达标', tone: 'hit' },
  near: { label: '接近', tone: 'near' },
  watch: { label: '观察', tone: 'watch' },
  unknown: { label: '无数据', tone: 'unknown' },
}

/** 价格线信号 → 文案与色调（not_set 是「没设目标价」，不是「数据不足」） */
const PRICE_SIGNAL_META = {
  hit: { label: '达标', tone: 'hit' },
  near: { label: '接近', tone: 'near' },
  watch: { label: '等待', tone: 'watch' },
  not_set: { label: '未设', tone: 'unknown' },
  unknown: { label: '无数据', tone: 'unknown' },
}

export function yieldSignalMeta(signal) {
  return YIELD_SIGNAL_META[signal] || YIELD_SIGNAL_META.unknown
}

export function priceSignalMeta(signal) {
  return PRICE_SIGNAL_META[signal] || PRICE_SIGNAL_META.unknown
}

/**
 * 日报里价格来源的文案。
 * real/cached/mock 是后端 `items[].price_source` 的白名单。
 */
export function priceSourceMeta(source) {
  if (source === 'real') return { label: '当日行情', tone: 'ok' }
  if (source === 'mock') return { label: '模拟价', tone: 'warn' }
  return { label: '缓存价', tone: 'warn' }
}

/** 报告类型（tab / 标题用） */
export function reportTypeMeta(type) {
  if (type === 'watchlist') {
    return {
      key: 'watchlist',
      label: '观察池日报',
      icon: '👀',
      emptyHint: '还没有观察池日报。收盘后会自动生成，也可以点「立即生成」。',
    }
  }
  return {
    key: 'portfolio',
    label: '组合日报',
    icon: '📈',
    emptyHint: '还没有这个组合的日报。收盘后会自动生成，也可以点「立即生成」。',
  }
}

/**
 * 列表行的一句话摘要。
 *
 * 组合：「10 只标的 · 涨 6 跌 4」；观察池：「12 只 · 达标 3」
 * 摘要只陈述**能算出来的部分**，算不出来就不提，不硬凑。
 */
export function reportHeadline(payload) {
  if (!payload || typeof payload !== 'object') return ''
  const summary = payload.summary || {}
  if (payload.type === 'watchlist') {
    const parts = []
    const total = toNumber(summary.item_count)
    if (total !== null) parts.push(`${total} 只标的`)
    const hits = (toNumber(summary.yield_hit_count) || 0) + (toNumber(summary.price_hit_count) || 0)
    if (hits > 0) parts.push(`达标 ${hits}`)
    return parts.join(' · ')
  }

  const parts = []
  const count = toNumber(summary.asset_count)
  if (count !== null) parts.push(`${count} 只标的`)
  const up = (payload.items || []).filter((i) => (toNumber(i.change_percent) || 0) > 0).length
  const down = (payload.items || []).filter((i) => (toNumber(i.change_percent) || 0) < 0).length
  if (up + down > 0) parts.push(`涨 ${up} 跌 ${down}`)
  return parts.join(' · ')
}

/**
 * 「今天有没有可看的信号」：观察池里达标 / 接近的标的数量。
 * 列表行上用它来决定要不要画一个小圆点，避免天天红点提醒。
 */
export function signalCount(payload) {
  const s = (payload && payload.summary) || {}
  return (
    (toNumber(s.yield_hit_count) || 0) +
    (toNumber(s.price_hit_count) || 0) +
    (toNumber(s.yield_near_count) || 0) +
    (toNumber(s.price_near_count) || 0)
  )
}

/**
 * 明细表按涨跌幅排序：默认按「今日盈亏金额」降序，与涨跌榜一致。
 * 金额缺失（没有昨收）的排在最后 —— 它们既不涨也不跌，插在中间只会干扰。
 */
export function sortItemsByPnl(items, key = 'day_pnl') {
  const list = Array.isArray(items) ? [...items] : []
  return list.sort((a, b) => {
    const av = toNumber(a?.[key])
    const bv = toNumber(b?.[key])
    if (av === null && bv === null) return 0
    if (av === null) return 1
    if (bv === null) return -1
    return bv - av
  })
}
