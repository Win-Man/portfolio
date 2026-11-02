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
 * 价格的小数位：由**标的类型**决定，不能一律 2 位。
 *
 * 交易所的最小价格变动不同：
 *   - 股票（含北交所）：0.01  → 2 位，如 8.57
 *   - 场内 ETF / LOF / 基金：0.001 → 3 位，如 1.523
 * 按 2 位显示 ETF 会把 1.523 写成 1.52，是**错的**（差 0.003 元，
 * 12,500 份就是 37.5 元）；而给股票补到 3 位（8.570）又只是噪音。
 *
 * 类型缺省（老数据 / 手工录入未带类型）时本函数返回 2，但 formatPrice 在
 * 类型缺失时会额外按数值精度兜底，不会因为类型丢了就截断精度。
 *
 * @param {string|null|undefined} assetType 后端 asset_type，如 "ETF" / "股票"
 * @returns {2|3}
 */
export function priceDigits(assetType) {
  return assetType && assetType !== '股票' ? 3 : 2
}

/** 该数值在第 3 位小数上是否有非零数字（兜底判断，避免浮点误差误判） */
function hasThirdDecimal(v) {
  return Math.abs(v * 100 - Math.round(v * 100)) > 1e-6
}

/**
 * 价格：按标的类型给 2 或 3 位小数，带千分位。
 *
 * 位数判定分两种情况，**类型优先**：
 *   1. 类型已知（"ETF" / "股票" / 其它）→ 严格按类型的位数。
 *      这一条很重要：触发价 = TTM 每股分红 / 目标股息率 是算出来的，
 *      42.857142… 天然带一堆小数，若按「数值精度」判断就会永远显示 3 位，
 *      股票的 42.857 看起来像另一类标的。
 *   2. 类型缺失（老数据 / 手工录入没带）→ 退回按数值本身精度判断：
 *      带 3 位小数就显示 3 位，宁多一位也不要显示一个错的价格。
 *
 * @param {number|string|null|undefined} n
 * @param {string|null|undefined} [assetType] 后端 asset_type
 * @returns {string} 例如 "1.523" / "8.57"；无效值 → "—"
 */
export function formatPrice(n, assetType) {
  if (n === null || n === undefined || n === '') return '—'
  const v = Number(n)
  if (!Number.isFinite(v)) return '—'
  const digits = assetType ? priceDigits(assetType) : hasThirdDecimal(v) ? 3 : 2
  return v.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
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
