/**
 * 数字格式化工具的单测。
 *
 * 这些用例锁定的是一组**刻意选择**的口径：
 *   - 一律显示完整数字，**不用「万 / 亿」**（同一列混用会破坏右对齐）
 *   - 金额固定 2 位、成本固定 3 位、持股数量不补零
 * 口径若被改动，这里应当红。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  formatAmount,
  formatBytes,
  formatQuantity,
  formatCost,
  formatPrice,
  formatSigned,
  priceDigits,
} from './format.js'

test('formatAmount：千分位 + 固定 2 位小数，不用「万」', () => {
  // 关键：7.99 万这种写法必须彻底消失，一律给完整数字
  assert.equal(formatAmount(79886), '79,886.00')
  assert.equal(formatAmount(79886.4), '79,886.40')
  assert.equal(formatAmount(12345678.9), '12,345,678.90')
  assert.equal(formatAmount(999), '999.00')
  assert.equal(formatAmount(0), '0.00')
})

test('formatAmount：负数与边界值', () => {
  assert.equal(formatAmount(-1234.5), '-1,234.50')
  assert.equal(formatAmount(-0.005), '-0.01')
})

test('formatAmount：非法输入退化为 0.00 而不是 NaN/undefined', () => {
  assert.equal(formatAmount(null), '0.00')
  assert.equal(formatAmount(undefined), '0.00')
  assert.equal(formatAmount(NaN), '0.00')
  assert.equal(formatAmount('abc'), '0.00')
  assert.equal(formatAmount(''), '0.00')
})

test('formatAmount：能解析数字字符串（后端可能给字符串）', () => {
  assert.equal(formatAmount('1234.5'), '1,234.50')
  assert.equal(formatAmount('0'), '0.00')
})

test('formatQuantity：不强制小数位，整数不带 .00', () => {
  // A 股持股数一般是整数，补 ".00" 只是噪音
  assert.equal(formatQuantity(12500), '12,500')
  assert.equal(formatQuantity(200), '200')
  assert.equal(formatQuantity(0), '0')
})

test('formatQuantity：真有小数时如实显示，最多 3 位', () => {
  assert.equal(formatQuantity(1.25), '1.25')
  assert.equal(formatQuantity(0.12345), '0.123')   // 第 4 位起四舍五入
  assert.equal(formatQuantity(null), '0')
})

test('formatCost：固定 3 位小数', () => {
  // 成本常带 3 位精度（如分红摊薄后的 -4.891），2 位会丢信息
  assert.equal(formatCost(-4.891), '-4.891')
  assert.equal(formatCost(48.608), '48.608')
  assert.equal(formatCost(1.046), '1.046')
  assert.equal(formatCost(0), '0.000')
  assert.equal(formatCost(null), '0.000')
})

test('formatCost：整数也补足 3 位，保证列内对齐', () => {
  assert.equal(formatCost(50), '50.000')
})

test('formatSigned：正数补 +，负数用 Unicode 减号 −', () => {
  assert.equal(formatSigned(20668.8), '+20,668.80')
  // 用 U+2212 MINUS SIGN 而不是 ASCII 连字符，视觉上更规整
  assert.equal(formatSigned(-1234.5), '−1,234.50')
  assert.equal(formatSigned(0), '+0.00')
})

test('formatSigned：可传入自定义格式化函数（如成本用 3 位）', () => {
  assert.equal(formatSigned(-4.891, formatCost), '−4.891')
})

// ---------------------------------------------------------------- 价格精度

test('priceDigits：ETF 3 位、股票 2 位，类型缺省按股票', () => {
  assert.equal(priceDigits('ETF'), 3)
  assert.equal(priceDigits('股票'), 2)
  // 老数据 / 手动录入可能没带类型
  assert.equal(priceDigits(''), 2)
  assert.equal(priceDigits(null), 2)
  assert.equal(priceDigits(undefined), 2)
  // 后端 asset_type 是自由字符串，非「股票」的其它类型（基金/LOF）都按 3 位
  assert.equal(priceDigits('LOF'), 3)
})

test('formatPrice：ETF 保留 3 位小数，不能把 1.523 写成 1.52', () => {
  // 这条是原始 bug：513530 现价 1.523 曾被格式化成 1.52
  assert.equal(formatPrice(1.523, 'ETF'), '1.523')
  assert.equal(formatPrice(1.295, 'ETF'), '1.295')
  assert.equal(formatPrice(0.8727272727272728, 'ETF'), '0.873')  // 触发价四舍五入到 3 位
  // 价格本身只有 2 位精度时补零到 3 位，保证同列小数点对齐
  assert.equal(formatPrice(1.5, 'ETF'), '1.500')
  assert.equal(formatPrice(1.05, 'ETF'), '1.050')
})

test('formatPrice：股票仍是 2 位，不给 8.57 补成 8.570', () => {
  assert.equal(formatPrice(8.57, '股票'), '8.57')
  assert.equal(formatPrice(52.8, '股票'), '52.80')
  assert.equal(formatPrice(1234.5, '股票'), '1,234.50')
})

test('formatPrice：类型已知时严格按类型位数（触发价这类计算值不能被推到 3 位）', () => {
  // 触发价 = TTM DPS / 目标股息率，天然带一长串小数；
  // 若按「数值精度」判断，股票的触发价会永远显示 3 位（42.857）
  assert.equal(formatPrice(42.857142857142854, '股票'), '42.86')
  assert.equal(formatPrice(49.63636363636363, '股票'), '49.64')
  // ETF 的触发价仍是 3 位
  assert.equal(formatPrice(1.4545454545454546, 'ETF'), '1.455')
  assert.equal(formatPrice(2.1818181818181817, 'ETF'), '2.182')
})

test('formatPrice：类型缺失但数值确实带 3 位小数时，仍按 3 位显示', () => {
  // 宁可多一位，也不要因为类型字段丢失而显示一个错的价格
  assert.equal(formatPrice(1.523, null), '1.523')
  assert.equal(formatPrice('1.523'), '1.523')
  // 2 位精度的值在类型缺失时退回 2 位
  assert.equal(formatPrice(8.57, null), '8.57')
})

test('formatPrice：无效值给「—」而不是假的 0.00', () => {
  assert.equal(formatPrice(null, 'ETF'), '—')
  assert.equal(formatPrice(undefined, 'ETF'), '—')
  assert.equal(formatPrice('', 'ETF'), '—')
  assert.equal(formatPrice(NaN, 'ETF'), '—')
  assert.equal(formatPrice('abc', 'ETF'), '—')
  // 0 是有效价格，不该当成「无数据」
  assert.equal(formatPrice(0, '股票'), '0.00')
})

test('formatBytes：按 1024 进位，1KB 以下不带小数', () => {
  assert.equal(formatBytes(0), '0 B')
  assert.equal(formatBytes(512), '512 B')
  assert.equal(formatBytes(1023), '1023 B')
  assert.equal(formatBytes(1024), '1.0 KB')
  assert.equal(formatBytes(1536), '1.5 KB')
  assert.equal(formatBytes(5214208), '5.0 MB')
  assert.equal(formatBytes(1024 ** 3), '1.0 GB')
})

test('formatBytes：无效输入给 "0 B" 而不是 NaN', () => {
  assert.equal(formatBytes(null), '0 B')
  assert.equal(formatBytes(undefined), '0 B')
  assert.equal(formatBytes(-100), '0 B')
  assert.equal(formatBytes('abc'), '0 B')
})
