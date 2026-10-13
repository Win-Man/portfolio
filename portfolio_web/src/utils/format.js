/**
 * 数字格式化工具 —— 全站统一口径。
 *
 * 设计原则：**不用「万 / 亿」**，一律显示完整数字。
 * 理由：同一列里混用 "1.25万" 与 "5,830.00" 会让宽度参差不齐，
 * 右对齐也救不回来（小数点对不齐）。统一 2 位小数 + 千分位后，
 * 右对齐即可让小数点列成一条线。
 */

/**
 * 金额 / 比率：千分位 + 固定 2 位小数。
 * @param {number|string|null|undefined} n
 * @returns {string} 例如 "79,886.00"
 */
export function formatAmount(n) {
  const v = Number(n)
  if (!Number.isFinite(v)) return '0.00'
  return v.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

/**
 * 持股数量：**不强制小数位**。
 * A 股/场内 ETF 的持股数一般就是整数，补 ".00" 只是噪音；
 * 万一有小数（如场外基金份额）也如实显示，最多 3 位。
 * @param {number|string|null|undefined} n
 * @returns {string} 例如 "12,500" 或 "1.25"
 */
export function formatQuantity(n) {
  const v = Number(n)
  if (!Number.isFinite(v)) return '0'
  return v.toLocaleString('zh-CN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 3,
  })
}

/**
 * 每股成本：固定 3 位小数。
 * 成本常带 3 位精度（如 -4.891），按 2 位显示会丢信息。
 * @param {number|string|null|undefined} n
 * @returns {string} 例如 "-4.891"
 */
export function formatCost(n) {
  const v = Number(n)
  if (!Number.isFinite(v)) return '0.000'
  return v.toLocaleString('zh-CN', {
    minimumFractionDigits: 3,
    maximumFractionDigits: 3,
  })
}

/**
 * 带正负号的金额（用于盈亏）：正数补 "+"，负数用 Unicode 减号 "−"。
 * @param {number} n
 * @param {(v:number)=>string} [fmt=formatAmount]
 * @returns {string} 例如 "+2.07" / "−1,234.00"
 */
export function formatSigned(n, fmt = formatAmount) {
  const v = Number(n) || 0
  return (v >= 0 ? '+' : '−') + fmt(Math.abs(v))
}
