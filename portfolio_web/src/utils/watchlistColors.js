/**
 * 观察池的配色与文案 —— **唯一定义处**，组件里不出现硬编码色值。
 *
 * 语义是「是否达标」，**与红涨绿跌无关**：沿用 PortfolioGoals 里
 * 「已达成 = 绿」的既有约定（spec 003 §7.4）。
 *
 * 五个信号键必须覆盖后端 `yield_signal` / `price_signal` 的全部取值：
 *   hit / near / watch / unknown / not_set
 */

/** 信号主色（badge 边框、圆点、进度等） */
export const SIGNAL_COLORS = {
  hit: '#059669',
  near: '#f59e0b',
  watch: '#94a3b8',
  unknown: '#cbd5e1',
  not_set: '#e2e8f0',
}

/** badge 文字色：在浅色底上保证对比度（#f59e0b 当正文色偏浅） */
export const SIGNAL_TEXT_COLORS = {
  hit: '#047857',
  near: '#b45309',
  watch: '#475569',
  unknown: '#94a3b8',
  not_set: '#94a3b8',
}

/** 信号文案（与 spec §7.4 表格逐字一致） */
export const SIGNAL_LABELS = {
  hit: '已达标',
  near: '接近',
  watch: '观察中',
  unknown: '数据不足',
  not_set: '未设目标价',
}

/** 曲线配色：价格线走右轴（¥），股息率线走左轴（%） */
export const LINE_COLORS = {
  price: '#2563eb',
  yield: '#059669',
  targetYield: '#f59e0b',
}

/**
 * 图上「标注类」配色：除权日竖线。
 *
 * 线刻意用很淡的 slate-300 —— 除权日只是**解释**股息率曲线台阶的辅助信息，
 * 不是要判断的对象，不能比价格线 / 股息率线更显眼，也不能和目标股息率
 * 那条琥珀色虚线抢注意力；但标签文字要够深才读得清（slate-500）。
 */
export const MARK_COLORS = {
  exDividend: '#cbd5e1',
  exDividendText: '#64748b',
}

/** 陈旧数据：行首 3px 左侧条 */
export const STALE_BAR_COLOR = '#f59e0b'

/** #rrggbb + alpha → rgba()，用于 badge 的浅色底 */
export function withAlpha(hex, alpha) {
  const value = String(hex || '').replace('#', '')
  if (value.length !== 6) return `rgba(148, 163, 184, ${alpha})`
  const r = parseInt(value.slice(0, 2), 16)
  const g = parseInt(value.slice(2, 4), 16)
  const b = parseInt(value.slice(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

/** 信号 → badge 的内联样式（未知取值退化成 unknown，不抛错） */
export function signalBadgeStyle(signal) {
  const key = SIGNAL_COLORS[signal] ? signal : 'unknown'
  return {
    color: SIGNAL_TEXT_COLORS[key],
    borderColor: SIGNAL_COLORS[key],
    background: withAlpha(SIGNAL_COLORS[key], 0.1),
  }
}
