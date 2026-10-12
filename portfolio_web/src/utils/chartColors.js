/**
 * 图表配色
 *
 * 两张饼图（持仓市值 / 预计年收入）共用同一份「资产 → 颜色」映射，
 * 这样同一个标的在两张图里颜色一致，便于对照：
 * 例如「市值占比 10% 但收入占比 15%」的标的一眼能看出来。
 */
export const CHART_PALETTE = [
  '#2563eb', '#0ea5e9', '#14b8a6', '#22c55e',
  '#84cc16', '#f59e0b', '#f97316', '#ef4444',
  '#ec4899', '#a855f7', '#6366f1', '#0891b2',
]

/** 「其他」聚合项统一用中性灰 */
export const MISC_COLOR = '#cbd5e1'

/**
 * 按稳定顺序为每个标的分配颜色。
 *
 * @param {string[]} names 标的名称，调用方需保证顺序稳定（如按市值降序）
 * @returns {Record<string, string>} 名称 → 颜色
 */
export function buildColorMap(names) {
  const map = {}
  names.forEach((name, i) => {
    map[name] = CHART_PALETTE[i % CHART_PALETTE.length]
  })
  return map
}

/**
 * 取颜色：优先用共享映射，未命中则按序号回退到调色板。
 */
export function pickColor(name, index, colorMap) {
  if (name && colorMap && colorMap[name]) return colorMap[name]
  return CHART_PALETTE[index % CHART_PALETTE.length]
}
