/**
 * 饼图数据整理
 *
 * 从后端已算好的字段聚合出可绘制的扇区：
 *   holding_value          = quantity × price
 *   expected_annual_income = quantity × dividend_per_share
 *
 * 纯函数，便于单测（见 src/utils/chartData.test.mjs）。
 */

/** 占比低于此值且项数过多时合并为「其他」 */
export const DEFAULT_MIN_SHARE = 2
/** 超过此扇区数才启用合并 */
export const DEFAULT_MAX_SLICES = 8

export const MISC_LABEL = '其他'

/**
 * 把资产数组聚合成 [{name, value}]。
 * 同名标的合并（用户可能重复录入同一只股票）。
 */
export function aggregateAssets(assets, pick) {
  const acc = new Map()
  for (const a of assets || []) {
    const name = (a && (a.name || a.code)) || '未命名'
    const value = Number(pick(a)) || 0
    acc.set(name, (acc.get(name) || 0) + value)
  }
  return [...acc.entries()].map(([name, value]) => ({ name, value }))
}

/**
 * 生成扇区列表。
 *
 * - 过滤掉 value <= 0 的项（饼图无法绘制 0 值扇区，图例也会显得杂乱）
 * - 按 value 降序排列
 * - 仅当项数 > maxSlices 时，把占比 < minShare 的合并为「其他 (N 项)」
 *   （合并后若「其他」只剩 1 项被吞并，则放弃合并，避免出现只有一个成员的聚合）
 * - 每项附带 percent（占比，0~100）
 */
export function buildSlices(items, options = {}) {
  const maxSlices = options.maxSlices ?? DEFAULT_MAX_SLICES
  const minShare = options.minShare ?? DEFAULT_MIN_SHARE

  const positive = (items || [])
    .map((d) => ({ name: String(d.name ?? ''), value: Number(d.value) || 0 }))
    .filter((d) => d.value > 0)
    .sort((a, b) => b.value - a.value)

  if (positive.length === 0) return []

  const sum = positive.reduce((s, d) => s + d.value, 0)
  const withPercent = (list) =>
    list.map((d) => ({ ...d, percent: sum ? (d.value / sum) * 100 : 0 }))

  if (positive.length <= maxSlices) return withPercent(positive)

  const main = []
  const misc = []
  for (const d of positive) {
    if ((d.value / sum) * 100 < minShare) misc.push(d)
    else main.push(d)
  }
  if (misc.length < 2) return withPercent(positive)

  const miscValue = misc.reduce((s, d) => s + d.value, 0)
  return withPercent([
    ...main,
    {
      name: `${MISC_LABEL} (${misc.length} 项)`,
      value: miscValue,
      isMisc: true,
    },
  ])
}

/**
 * 计算共享颜色映射的顺序：按「持仓市值」降序作为基准，
 * 保证两张图的颜色分配顺序一致。
 */
export function stableNameOrder(assets, primaryPick) {
  const agg = aggregateAssets(assets, primaryPick).filter((d) => d.value > 0)
  return agg.sort((a, b) => b.value - a.value).map((d) => d.name)
}

/**
 * 取占比最大的 n 项（用于饼图的「TOP N 图例」）。
 *
 * 返回项带 `index`，是它在**传入数组中的原始下标** —— 调用方要用它取色，
 * 否则排序后再按下标取色，图例色点会与饼图色块对不上。
 *
 * @param {Array<{name:string,value:number,percent?:number}>} slices
 * @param {number} n
 */
export function topSlices(slices, n = 5) {
  return (slices || [])
    .map((d, index) => ({ ...d, index }))
    .sort((a, b) => (Number(b.value) || 0) - (Number(a.value) || 0))
    .slice(0, Math.max(0, n))
}

/** 数字格式化：精确到分，带千分位 */
export function formatExact(num) {
  return Number(num || 0).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

/** 名称截断 */
export function truncate(name, max = 9) {
  const s = String(name ?? '')
  return s.length > max ? s.slice(0, max) + '…' : s
}

/**
 * 悬浮提示的 HTML 内容（纯函数，便于单测）。
 *
 * 图例已移除，扇区识别完全依赖此提示，因此内容要完整：
 * 名称 + 精确金额 + 两位小数占比。
 */
export function buildTooltipHtml({ name, value, percent, unit = '¥' }) {
  const pct = Number.isFinite(percent) ? percent : 0
  return [
    `<div style="font-weight:600;margin-bottom:4px">${escapeHtml(name)}</div>`,
    `<div>${unit}${formatExact(value)}</div>`,
    `<div style="opacity:.75">占比 ${pct.toFixed(2)}%</div>`,
  ].join('')
}

/** 极简 HTML 转义，防止标的名称里的特殊字符破坏提示结构 */
export function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

