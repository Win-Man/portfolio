/**
 * 成本口径的换算（纯函数，前后端共用同一套约定）。
 *
 * 为什么要单独一个模块：
 *   券商 App 显示的「成本」是**四舍五入后的展示值**（华泰/同花顺都是 3 位小数），
 *   真实成本 = 持仓成本总额 ÷ 持股数，可能带 6 位以上小数。
 *   按展示值当成本，低價 ETF 会算错盈亏：
 *
 *     513530：7500 份，现价 1.523
 *       券商成本显示 1.521（实为 1.521137…，总成本 11408.53）
 *       按 1.521 算 → (1.523 − 1.521) × 7500 = +15.00
 *       按 1.521137 算 → 11422.50 − 11408.53 = +13.97  ← 与券商一致
 *
 *   差的 1.03 元全部来自「每股成本只保留 3 位」。
 *
 * 所以：每股成本允许 6 位小数落库，同时提供「持仓成本总额」输入口，
 * 用券商给的总额（市值 − 盈亏）反推，永远对得上账。
 */

/** 每股成本保留的小数位（够还原「总额 ÷ 份数」，又不会出现浮点尾巴） */
export const COST_PRICE_DECIMALS = 6

/** 成本总额保留的小数位（与券商一致：分） */
export const COST_AMOUNT_DECIMALS = 2

/** 按指定小数位四舍五入（避免 toFixed 的字符串往返） */
export function roundTo(value, decimals) {
  const factor = 10 ** decimals
  return Math.round(value * factor) / factor
}

/** 宽松转数字：空值 / 非数字 → null（0 是合法值，不能当空） */
function toNumber(value) {
  if (value === null || value === undefined || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

/**
 * 每股成本 → 持仓成本总额。
 * @param {number|string|null} costPrice 每股成本
 * @param {number|string|null} quantity 持股数量
 * @returns {number|null} 总额（2 位小数）；任一输入无效或份数 ≤ 0 → null
 */
export function costAmountFromPrice(costPrice, quantity) {
  const c = toNumber(costPrice)
  const q = toNumber(quantity)
  if (c === null || q === null || q <= 0) return null
  return roundTo(c * q, COST_AMOUNT_DECIMALS)
}

/**
 * 持仓成本总额 → 每股成本。
 * @param {number|string|null} amount 成本总额（券商给的「市值 − 盈亏」）
 * @param {number|string|null} quantity 持股数量
 * @returns {number|null} 每股成本（6 位小数）；任一输入无效或份数 ≤ 0 → null
 */
export function costPriceFromAmount(amount, quantity) {
  const a = toNumber(amount)
  const q = toNumber(quantity)
  if (a === null || q === null || q <= 0) return null
  return roundTo(a / q, COST_PRICE_DECIMALS)
}
