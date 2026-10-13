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
  formatQuantity,
  formatCost,
  formatSigned,
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
