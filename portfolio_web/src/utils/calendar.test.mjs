/**
 * 「收入日历」纯函数单测（spec §10.2 的 8 组）。
 *
 * 运行：node --test src/utils/calendar.test.mjs
 * （纯 JS，无需 vitest / jsdom；`npm test` 会连带跑本文件）
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  SOURCE_KEYS,
  STATUS_KEYS,
  bucketByMonth,
  classifySource,
  classifyStatusFromImpl,
  cumulativeLine,
  formatDayLabel,
  formatMonthLabel,
  isPendingMonth,
  pendingMonthsOfYear,
  stackedBarData,
  sumBySource,
  sumByStatus,
} from './calendar.js'
import { CUMULATIVE_LINE_COLOR, SOURCE_COLORS, SOURCE_LABELS, SOURCE_ORDER } from './calendarColors.js'

// ---------- 1. bucketByMonth ----------

test('bucketByMonth：3 笔落在 3 个月，其余 9 个月零占位，恒为 12 个月', () => {
  const months = bucketByMonth(
    [
      { ex_date: '2026-01-15', amount: 10, status: 'paid', source: 'stock' },
      { ex_date: '2026-01-28', amount: 5.5, status: 'predicted', source: 'etf' },
      // 故意不给 amount：应回退 quantity × dividend_per_share
      { ex_date: '2026-10-16', quantity: 100, dividend_per_share: 0.25, status: 'announced', source: 'bond' },
    ],
    2026
  )

  assert.equal(months.length, 12)
  assert.deepEqual(months.map((m) => m.month), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])

  // 1 月：两笔
  assert.equal(months[0].total_amount, 15.5)
  assert.equal(months[0].count, 2)
  assert.deepEqual(months[0].by_status, { predicted: 5.5, announced: 0, paid: 10, manual: 0 })
  assert.deepEqual(months[0].by_source, {
    stock: 10,
    etf: 5.5,
    bond: 0,
    otc: 0,
    reit: 0,
    custom: 0,
    self: 0,
    manual: 0,
  })

  // 10 月：一笔，金额由 quantity × per_share 兜底
  assert.equal(months[9].total_amount, 25)
  assert.equal(months[9].count, 1)
  assert.equal(months[9].by_source.bond, 25)

  // 其余月份：0 笔 / ¥0（不是缺字段）
  for (const i of [1, 2, 3, 4, 5, 6, 7, 8, 10, 11]) {
    assert.equal(months[i].total_amount, 0, `${i + 1} 月应为 0`)
    assert.equal(months[i].count, 0, `${i + 1} 月应为 0 笔`)
    assert.deepEqual(months[i].by_status, { predicted: 0, announced: 0, paid: 0, manual: 0 })
  }
})

test('bucketByMonth：按年过滤；跨年与非法日期不落箱', () => {
  const months = bucketByMonth(
    [
      { ex_date: '2025-12-30', amount: 99, status: 'paid', source: 'stock' },
      { ex_date: '乱码', amount: 50, status: 'paid', source: 'stock' },
      { ex_date: '2026-12-01', amount: 1, status: 'paid', source: 'stock' },
    ],
    2026
  )
  assert.equal(months.reduce((sum, m) => sum + m.total_amount, 0), 1)
  assert.equal(months[11].total_amount, 1)
  assert.equal(months[11].count, 1)
  // 非 12 月全为 0
  assert.ok(months.slice(0, 11).every((m) => m.total_amount === 0 && m.count === 0))
})

test('bucketByMonth：入参缺失 / 空数组也能返回 12 个零槽位', () => {
  const months = bucketByMonth(null, 2026)
  assert.equal(months.length, 12)
  assert.ok(months.every((m) => m.total_amount === 0 && m.count === 0))
  assert.equal(bucketByMonth([], 2026).length, 12)
})

// ---------- 2. sumByStatus ----------

test('sumByStatus：4 类状态分别累加，未知 / 缺失状态忽略', () => {
  const out = sumByStatus([
    { amount: 10, status: 'paid' },
    { amount: 2.5, status: 'paid' },
    { amount: 1, status: 'announced' },
    { amount: 3, status: 'predicted' },
    { amount: 4, status: 'manual' },
    { amount: 100, status: 'unknown' },
    { amount: 999 },
  ])
  assert.deepEqual(out, { predicted: 3, announced: 1, paid: 12.5, manual: 4 })
  assert.deepEqual(Object.keys(out), STATUS_KEYS)
  assert.deepEqual(sumByStatus([]), { predicted: 0, announced: 0, paid: 0, manual: 0 })
})

// ---------- 3. sumBySource ----------

test('sumBySource：8 类来源分别累加且全部零填充', () => {
  const items = SOURCE_KEYS.map((source, i) => ({ source, amount: (i + 1) * 10 }))
  const out = sumBySource(items)
  assert.deepEqual(Object.keys(out), SOURCE_KEYS)
  assert.deepEqual(out, {
    stock: 10,
    etf: 20,
    bond: 30,
    otc: 40,
    reit: 50,
    custom: 60,
    self: 70,
    manual: 80,
  })
  assert.deepEqual(sumBySource([{ source: '不存在', amount: 5 }]), {
    stock: 0,
    etf: 0,
    bond: 0,
    otc: 0,
    reit: 0,
    custom: 0,
    self: 0,
    manual: 0,
  })
})

// ---------- 4. classifyStatusFromImpl ----------

test('classifyStatusFromImpl：已实施 → paid；已发布 / 实施中 → announced；其余保守为 predicted', () => {
  assert.equal(classifyStatusFromImpl('已实施'), 'paid')
  assert.equal(classifyStatusFromImpl('已发布'), 'announced')
  assert.equal(classifyStatusFromImpl('实施中'), 'announced')
  assert.equal(classifyStatusFromImpl('董事会预案'), 'predicted')
  assert.equal(classifyStatusFromImpl('股东大会通过'), 'predicted')
  assert.equal(classifyStatusFromImpl('未实施'), 'predicted')
  assert.equal(classifyStatusFromImpl('尚未实施'), 'predicted')
  assert.equal(classifyStatusFromImpl(''), 'predicted')
  assert.equal(classifyStatusFromImpl('   '), 'predicted')
  assert.equal(classifyStatusFromImpl(null), 'predicted')
  assert.equal(classifyStatusFromImpl(undefined), 'predicted')
})

// ---------- 5. classifySource ----------

test('classifySource：ETF 代码 / 债 / OTC / REITs / 自建 / 自定义 / 兜底 stock / 手动 manual', () => {
  assert.equal(classifySource({ code: '510300' }), 'etf')
  assert.equal(classifySource({ code: '159915' }), 'etf')
  assert.equal(classifySource({ code: '161725' }), 'etf')
  assert.equal(classifySource({ code: '180101' }), 'etf')
  assert.equal(classifySource({ code: '600036', asset_type: '股票' }), 'stock')
  assert.equal(classifySource({ code: '000002', asset_type: '个股' }), 'stock')
  assert.equal(classifySource({ code: '113050', asset_type: '可转债' }), 'bond')
  assert.equal(classifySource({ code: 'XYZ01', asset_type: 'OTC 场外' }), 'otc')
  assert.equal(classifySource({ code: 'XYZ02', asset_type: 'REITs' }), 'reit')
  assert.equal(classifySource({ code: 'XYZ03', asset_type: '自建组合' }), 'self')
  assert.equal(classifySource({ code: 'XYZ04', asset_type: '自定义' }), 'custom')
  assert.equal(classifySource({ code: '600036', asset_type: '股票' }, true), 'manual')
  assert.equal(classifySource(null), 'stock')
  assert.equal(classifySource(undefined, true), 'manual')
})

// ---------- 6. cumulativeLine ----------

test('cumulativeLine：返回 12 个累计值，最后一项 = 全年合计', () => {
  const line = cumulativeLine([1, 2, 3, 0, 0, 0, 0, 0, 0, 0, 0, 4])
  assert.equal(line.length, 12)
  assert.deepEqual(line, [1, 3, 6, 6, 6, 6, 6, 6, 6, 6, 6, 10])
  assert.equal(line[11], 10)

  // 也接受 CalendarMonth 对象数组
  const months = bucketByMonth(
    [
      { ex_date: '2026-03-01', amount: 30, status: 'paid', source: 'stock' },
      { ex_date: '2026-07-01', amount: 70, status: 'paid', source: 'stock' },
    ],
    2026
  )
  const fromMonths = cumulativeLine(months)
  assert.equal(fromMonths.length, 12)
  assert.equal(fromMonths[2], 30)
  assert.equal(fromMonths[11], 100)
  assert.equal(fromMonths[11], months.reduce((s, m) => s + m.total_amount, 0))
})

// ---------- 7. stackedBarData ----------

test('stackedBarData：8 条系列，顺序 = 固定图例顺序，形状 {name,data,stack,itemStyle}', () => {
  const months = bucketByMonth(
    [
      { ex_date: '2026-01-05', amount: 10, status: 'paid', source: 'stock' },
      { ex_date: '2026-02-05', amount: 20, status: 'announced', source: 'etf' },
      { ex_date: '2026-03-05', amount: 30, status: 'manual', source: 'manual' },
    ],
    2026
  )
  const series = stackedBarData(months)

  assert.equal(series.length, 8)
  assert.deepEqual(series.map((s) => s.name), SOURCE_ORDER.map((k) => SOURCE_LABELS[k]))
  assert.deepEqual(Object.keys(series[0]).sort(), ['data', 'itemStyle', 'name', 'stack'])
  assert.equal(series[0].stack, 'total')
  assert.deepEqual(series[0].itemStyle, { color: SOURCE_COLORS.stock })
  assert.equal(series[7].itemStyle.color, SOURCE_COLORS.manual)

  assert.equal(series[0].data.length, 12)
  assert.equal(series[0].data[0], 10)
  assert.equal(series[1].data[1], 20)
  assert.equal(series[7].data[2], 30)
  assert.deepEqual(series[2].data, [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])

  // 指定 sources 时顺序按入参走（图例顺序可控）
  assert.deepEqual(stackedBarData(months, ['manual', 'stock']).map((s) => s.name), ['补录', '股'])
})

// ---------- 8. formatMonthLabel ----------

test('formatMonthLabel：输出 YYYY年MM月（月不补 0）', () => {
  assert.equal(formatMonthLabel(2026, 10), '2026年10月')
  assert.equal(formatMonthLabel(2026, 1), '2026年1月')
  assert.equal(formatMonthLabel('2025', '12'), '2025年12月')
})

// ---------- 附加：月 Sheet 行内日期展示 ----------

test('formatDayLabel：YYYY-MM-DD → MM月DD日，非法输入给空串', () => {
  assert.equal(formatDayLabel('2026-10-16'), '10月16日')
  assert.equal(formatDayLabel('2026-01-05'), '1月5日')
  assert.equal(formatDayLabel('乱码'), '')
  assert.equal(formatDayLabel(null), '')
})

// ---------- 常量自检（颜色只在 calendarColors.js 里定义） ----------

test('SOURCE_KEYS：8 类来源，且与颜色 / 文案表一一对应', () => {
  assert.equal(SOURCE_KEYS.length, 8)
  assert.deepEqual(SOURCE_KEYS, SOURCE_ORDER)
  for (const key of SOURCE_KEYS) {
    assert.ok(SOURCE_COLORS[key], `${key} 缺少颜色`)
    assert.ok(SOURCE_LABELS[key], `${key} 缺少文案`)
  }
  assert.equal(CUMULATIVE_LINE_COLOR, '#059669')
})

// ---------- 附加：「待收录」月份（未来月份不外推，D2） ----------

test('isPendingMonth：只有严格晚于当前年月的月份才算待收录', () => {
  const today = new Date(2026, 9, 16) // 2026-10-16
  assert.equal(isPendingMonth(2026, 11, today), true)
  assert.equal(isPendingMonth(2026, 12, today), true)
  assert.equal(isPendingMonth(2027, 1, today), true)
  assert.equal(isPendingMonth(2026, 10, today), false) // 当月不算
  assert.equal(isPendingMonth(2026, 9, today), false)  // 过去月份「确实没有」≠「待收录」
  assert.equal(isPendingMonth(2025, 12, today), false) // 往年更不算
})

test('isPendingMonth：非法入参不崩、返回 false', () => {
  const today = new Date(2026, 9, 16)
  assert.equal(isPendingMonth(2026, 0, today), false)
  assert.equal(isPendingMonth(2026, 13, today), false)
  assert.equal(isPendingMonth(null, 11, today), false)
  assert.equal(isPendingMonth('abc', 11, today), false)
})

test('pendingMonthsOfYear：只列出该年剩余月份（跨年只到 12 月）', () => {
  const today = new Date(2026, 9, 16) // 2026-10-16
  assert.deepEqual(pendingMonthsOfYear(2026, today), [11, 12])
  assert.deepEqual(pendingMonthsOfYear(2025, today), [])
  assert.deepEqual(pendingMonthsOfYear(2027, today), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
})
