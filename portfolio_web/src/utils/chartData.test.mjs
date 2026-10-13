/**
 * 饼图数据整理逻辑单测
 *
 * 运行：node --test src/utils/chartData.test.mjs
 * （纯 JS，无需 vitest / jsdom）
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  aggregateAssets,
  buildSlices,
  buildTooltipHtml,
  escapeHtml,
  formatExact,
  stableNameOrder,
  truncate,
  topSlices,
} from './chartData.js'

// ---------- aggregateAssets ----------

test('aggregateAssets 按 pick 取值，保留名称', () => {
  const assets = [
    { name: 'A', holding_value: 100 },
    { name: 'B', holding_value: 250 },
  ]
  assert.deepEqual(aggregateAssets(assets, (a) => a.holding_value), [
    { name: 'A', value: 100 },
    { name: 'B', value: 250 },
  ])
})

test('aggregateAssets 合并同名标的', () => {
  const assets = [
    { name: 'A', holding_value: 100 },
    { name: 'A', holding_value: 50 },
    { name: 'B', holding_value: 30 },
  ]
  const out = aggregateAssets(assets, (a) => a.holding_value)
  assert.equal(out.length, 2)
  assert.equal(out.find((d) => d.name === 'A').value, 150)
})

test('aggregateAssets 处理缺失字段与空数组', () => {
  assert.deepEqual(aggregateAssets([], (a) => a.holding_value), [])
  assert.deepEqual(aggregateAssets(null, (a) => a.holding_value), [])
  const out = aggregateAssets([{ holding_value: undefined }], (a) => a.holding_value)
  assert.equal(out[0].name, '未命名')
  assert.equal(out[0].value, 0)
})

test('aggregateAssets 无 name 时回退到 code', () => {
  const out = aggregateAssets([{ code: '600900', holding_value: 5 }], (a) => a.holding_value)
  assert.equal(out[0].name, '600900')
})

// ---------- buildSlices ----------

test('buildSlices 过滤 0 与负值，并按值降序', () => {
  const out = buildSlices([
    { name: 'A', value: 10 },
    { name: 'B', value: 0 },
    { name: 'C', value: 30 },
    { name: 'D', value: -5 },
  ])
  assert.deepEqual(out.map((d) => d.name), ['C', 'A'])
})

test('buildSlices 计算百分比且合计为 100', () => {
  const out = buildSlices([
    { name: 'A', value: 25 },
    { name: 'B', value: 75 },
  ])
  assert.equal(out[0].name, 'B')
  assert.equal(out[0].percent, 75)
  assert.equal(out[1].percent, 25)
  assert.equal(
    out.reduce((s, d) => s + d.percent, 0),
    100
  )
})

test('buildSlices 空输入返回空数组', () => {
  assert.deepEqual(buildSlices([]), [])
  assert.deepEqual(buildSlices(null), [])
  assert.deepEqual(buildSlices([{ name: 'A', value: 0 }]), [])
})

test('buildSlices 项数不超过上限时不合并', () => {
  const items = Array.from({ length: 8 }, (_, i) => ({ name: `N${i}`, value: 100 - i }))
  const out = buildSlices(items, { maxSlices: 8 })
  assert.equal(out.length, 8)
  assert.ok(!out.some((d) => d.isMisc))
})

test('buildSlices 项数超限时把小占比合并为「其他」', () => {
  const items = [
    { name: 'BIG1', value: 5000 },
    { name: 'BIG2', value: 4000 },
    ...Array.from({ length: 10 }, (_, i) => ({ name: `S${i}`, value: 10 })),
  ]
  const out = buildSlices(items, { maxSlices: 8, minShare: 2 })
  const misc = out.find((d) => d.isMisc)
  assert.ok(misc, '应产生「其他」聚合项')
  assert.equal(misc.name, '其他 (10 项)')
  assert.equal(misc.value, 100)
  // 保留 2 个大项 + 1 个其他
  assert.equal(out.length, 3)
})

test('buildSlices 合并后「其他」不足 2 项时放弃合并', () => {
  // 9 项，只有 1 项低于阈值 → 不应产生只有一个成员的「其他」
  const items = [
    ...Array.from({ length: 8 }, (_, i) => ({ name: `B${i}`, value: 1000 })),
    { name: 'TINY', value: 1 },
  ]
  const out = buildSlices(items, { maxSlices: 8, minShare: 2 })
  assert.equal(out.length, 9)
  assert.ok(!out.some((d) => d.isMisc))
})

test('buildSlices 合并后总金额守恒', () => {
  const items = [
    { name: 'A', value: 1234.56 },
    ...Array.from({ length: 12 }, (_, i) => ({ name: `S${i}`, value: 3.21 })),
  ]
  const total = items.reduce((s, d) => s + d.value, 0)
  const out = buildSlices(items, { maxSlices: 5, minShare: 2 })
  const sum = out.reduce((s, d) => s + d.value, 0)
  assert.ok(Math.abs(sum - total) < 1e-6, `合计应守恒: ${sum} vs ${total}`)
})

test('buildSlices 全是 0 时返回空（避免除以 0）', () => {
  assert.deepEqual(buildSlices([{ name: 'A', value: 0 }, { name: 'B', value: 0 }]), [])
})

test('buildSlices 单项时占比 100%', () => {
  const out = buildSlices([{ name: 'Only', value: 42 }])
  assert.equal(out.length, 1)
  assert.equal(out[0].percent, 100)
})

// ---------- stableNameOrder ----------

test('stableNameOrder 按主口径降序，保证两图配色一致', () => {
  const assets = [
    { name: 'A', holding_value: 10 },
    { name: 'B', holding_value: 30 },
    { name: 'C', holding_value: 20 },
  ]
  assert.deepEqual(stableNameOrder(assets, (a) => a.holding_value), ['B', 'C', 'A'])
})

test('stableNameOrder 排除 0 值项', () => {
  const assets = [
    { name: 'A', holding_value: 10 },
    { name: 'Z', holding_value: 0 },
  ]
  assert.deepEqual(stableNameOrder(assets, (a) => a.holding_value), ['A'])
})

// ---------- 格式化 ----------

test('formatExact 保留两位小数与千分位', () => {
  assert.equal(formatExact(79786), '79,786.00')
  assert.equal(formatExact(4047.25), '4,047.25')
  assert.equal(formatExact(0), '0.00')
  assert.equal(formatExact(null), '0.00')
})

test('truncate 超长加省略号', () => {
  assert.equal(truncate('央企红利ETF华泰柏瑞', 8), '央企红利ETF华…')
  assert.equal(truncate('长江电力', 8), '长江电力')
  assert.equal(truncate('', 8), '')
  assert.equal(truncate(null, 8), '')
})

// ---------- 悬浮提示（图例移除后，这是唯一识别扇区的途径，必须完整）----------

test('buildTooltipHtml 包含名称、金额、占比三项信息', () => {
  const html = buildTooltipHtml({
    name: '长江电力',
    value: 5876,
    percent: 7.364,
    unit: '¥',
  })
  assert.ok(html.includes('长江电力'), '应含名称')
  assert.ok(html.includes('¥5,876.00'), '应含精确金额（千分位 + 两位小数）')
  assert.ok(html.includes('占比 7.36%'), '应含两位小数占比')
})

test('buildTooltipHtml 处理缺失占比不崩溃', () => {
  const html = buildTooltipHtml({ name: 'A', value: 1, percent: undefined })
  assert.ok(html.includes('占比 0.00%'))
})

test('buildTooltipHtml 使用传入单位', () => {
  const html = buildTooltipHtml({ name: 'A', value: 100, percent: 50, unit: 'HK$' })
  assert.ok(html.includes('HK$100.00'))
})

test('buildTooltipHtml 转义名称中的 HTML 特殊字符', () => {
  const html = buildTooltipHtml({ name: '<img src=x>', value: 1, percent: 1 })
  assert.ok(!html.includes('<img'), '不应注入原始标签')
  assert.ok(html.includes('&lt;img'))
})

test('escapeHtml 覆盖四个特殊字符', () => {
  assert.equal(escapeHtml('&<>"'), '&amp;&lt;&gt;&quot;')
  assert.equal(escapeHtml(null), '')
  assert.equal(escapeHtml(undefined), '')
})

test('占比总和为 100 时，提示中的百分比与实际一致', () => {
  const assets = [
    { name: 'A', holding_value: 30 },
    { name: 'B', holding_value: 70 },
  ]
  const slices = buildSlices(aggregateAssets(assets, (a) => a.holding_value))
  const htmlA = buildTooltipHtml({ ...slices.find((s) => s.name === 'A'), unit: '¥' })
  assert.ok(htmlA.includes('占比 30.00%'))
})

// ---------- 真实组合数据（用户当前持仓形态）----------

test('真实组合：9 项标的不触发合并，两图百分比合计 100', () => {
  const assets = [
    { name: '央企红利ETF华泰柏瑞', holding_value: 16075, expected_annual_income: 1187.5 },
    { name: '红利国企ETF国泰', holding_value: 15900, expected_annual_income: 544.5 },
    { name: '云南白药', holding_value: 10414, expected_annual_income: 524.2 },
    { name: '红利ETF易方达', holding_value: 8676, expected_annual_income: 366 },
    { name: '港股通红利ETF华泰柏瑞', holding_value: 7875, expected_annual_income: 600 },
    { name: '港股红利低波ETF招商', holding_value: 6516, expected_annual_income: 288 },
    { name: '长江电力', holding_value: 5876, expected_annual_income: 200 },
    { name: '招商银行', holding_value: 4276, expected_annual_income: 201.6 },
    { name: '牧原股份', holding_value: 4178, expected_annual_income: 135.45 },
  ]

  const valueSlices = buildSlices(aggregateAssets(assets, (a) => a.holding_value))
  const incomeSlices = buildSlices(aggregateAssets(assets, (a) => a.expected_annual_income))

  assert.equal(valueSlices.length, 9)
  assert.equal(incomeSlices.length, 9)
  assert.ok(!valueSlices.some((d) => d.isMisc))
  assert.ok(Math.abs(valueSlices.reduce((s, d) => s + d.percent, 0) - 100) < 1e-9)
  assert.ok(Math.abs(incomeSlices.reduce((s, d) => s + d.percent, 0) - 100) < 1e-9)

  // 榜首校验：市值与收入的第一名都是央企红利ETF华泰柏瑞
  assert.equal(valueSlices[0].name, '央企红利ETF华泰柏瑞')
  assert.equal(incomeSlices[0].name, '央企红利ETF华泰柏瑞')

  // 「市值占比小但收入占比大」应能被观察出来（股息效率）
  const byName = Object.fromEntries(incomeSlices.map((d) => [d.name, d.percent]))
  const vByName = Object.fromEntries(valueSlices.map((d) => [d.name, d.percent]))
  assert.ok(byName['港股通红利ETF华泰柏瑞'] > vByName['港股通红利ETF华泰柏瑞'],
    '港股通红利ETF的收入占比应高于其市值占比')
})

// ---------- topSlices：饼图 TOP N 图例的取数 ----------

test('topSlices：取占比最大的 n 项，且按值降序', () => {
  const slices = [
    { name: 'A', value: 10 },
    { name: 'B', value: 50 },
    { name: 'C', value: 30 },
    { name: 'D', value: 5 },
    { name: 'E', value: 40 },
    { name: 'F', value: 1 },
  ]
  assert.deepEqual(
    topSlices(slices, 5).map((d) => d.name),
    ['B', 'E', 'C', 'A', 'D'],
  )
})

test('topSlices：保留原始下标，供调用方按饼图色块取色', () => {
  const slices = [
    { name: 'A', value: 10 },  // index 0
    { name: 'B', value: 50 },  // index 1
    { name: 'C', value: 30 },  // index 2
  ]
  const top = topSlices(slices, 3)
  assert.deepEqual(top.map((d) => d.index), [1, 2, 0])
  // 关键：B 排到第一位，但 index 仍是 1 —— 用它取色才能对上饼图里的 B 色块
  assert.equal(top[0].name, 'B')
  assert.equal(top[0].index, 1)
})

test('topSlices：项数不足 n 时全返回', () => {
  const slices = [{ name: 'A', value: 3 }, { name: 'B', value: 1 }]
  assert.deepEqual(topSlices(slices, 5).map((d) => d.name), ['A', 'B'])
})

test('topSlices：空数组 / null / n=0 安全', () => {
  assert.deepEqual(topSlices([], 5), [])
  assert.deepEqual(topSlices(null, 5), [])
  assert.deepEqual(topSlices([{ name: 'A', value: 1 }], 0), [])
})

test('topSlices：不修改传入数组', () => {
  const slices = [{ name: 'A', value: 1 }, { name: 'B', value: 9 }]
  const snapshot = JSON.stringify(slices)
  topSlices(slices, 1)
  assert.equal(JSON.stringify(slices), snapshot, '原数组顺序不应被改动')
})

test('topSlices：真实组合中 其他 项也参与排序（占比大就可能进图例）', () => {
  const slices = [
    { name: '其他 (3 项)', value: 100, isMisc: true },
    { name: '小标的', value: 1 },
  ]
  const top = topSlices(slices, 5)
  assert.equal(top[0].name, '其他 (3 项)')
  assert.equal(top[0].isMisc, true)
})
