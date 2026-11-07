/**
 * 观察池展示纯函数的单测（node --test，spec 003 §10.2 的 8 组）。
 *
 * 锁定的口径：
 *   - 信号 → 文案 / 颜色：五个键全覆盖，未知取值退化成 unknown
 *   - 排序：默认 hit → near → watch → unknown/not_set，同档按 yield_gap_points 降序；
 *     表头列排序 6 列升降序、空值沉底、同值保持默认次序
 *   - 曲线：日期升序、null 原样保留（断线），不插值；空数组不抛错
 *   - 格式化：2 位小数、正负号用 +/Unicode 减号「−」、无意义值给「—」
 *   - 陈旧：5 天不算、6 天才算（> 5 个自然日）
 *
 * 注意：这里**不测**任何阈值比较 —— 那是后端的职责（D9），
 * 前端一旦自己算「是否达标」，就会出现两套口径。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  alignExDividends,
  buildRangeQuery,
  buildTrendSeries,
  daysBetween,
  defaultCustomRange,
  formatChartTip,
  formatGapPercent,
  formatYield,
  isDataStale,
  signalLabel,
  signalTone,
  SORTABLE_COLUMNS,
  sortByColumn,
  sortForDisplay,
  spanDays,
  staleDays,
  summarize,
  summarizeRange,
} from './watchlist.js'
import { SIGNAL_COLORS, SIGNAL_LABELS, signalBadgeStyle } from './watchlistColors.js'

// ------------------------------------------------- 1. 信号 → 文案 / 颜色

test('signalLabel / signalTone：5 种信号映射正确', () => {
  const cases = [
    ['hit', '已达标', '#059669'],
    ['near', '接近', '#f59e0b'],
    ['watch', '观察中', '#94a3b8'],
    ['unknown', '数据不足', '#cbd5e1'],
    ['not_set', '未设目标价', '#e2e8f0'],
  ]
  for (const [signal, label, tone] of cases) {
    assert.equal(signalLabel(signal), label, `${signal} 的文案`)
    assert.equal(signalTone(signal), tone, `${signal} 的色值`)
    assert.equal(SIGNAL_LABELS[signal], label)
    assert.equal(SIGNAL_COLORS[signal], tone)
  }
  // 未知取值不抛错、不返回 undefined
  assert.equal(signalLabel('wat'), '数据不足')
  assert.equal(signalTone('wat'), '#cbd5e1')
})

test('signalBadgeStyle：给出文字色 / 边框色 / 浅色底（未知信号走 unknown）', () => {
  const hit = signalBadgeStyle('hit')
  assert.equal(hit.borderColor, '#059669')
  assert.match(hit.background, /^rgba\(5, 150, 105, 0\.1\)$/)
  assert.equal(signalBadgeStyle('nope').borderColor, '#cbd5e1')
})

// ------------------------------------------------- 2. 排序

test('sortForDisplay：hit → near → watch → 数据不足，同档按 yield_gap_points 降序', () => {
  const items = [
    { id: 1, yield_signal: 'watch', price_signal: 'watch', yield_gap_points: 1.0 },
    { id: 2, yield_signal: 'unknown', price_signal: 'not_set', yield_gap_points: null },
    { id: 3, yield_signal: 'hit', price_signal: 'not_set', yield_gap_points: 0.8 },
    { id: 4, yield_signal: 'near', price_signal: 'watch', yield_gap_points: -0.15 },
    { id: 5, yield_signal: 'near', price_signal: 'near', yield_gap_points: -0.05 },
    { id: 6, yield_signal: 'hit', price_signal: 'hit', yield_gap_points: 1.2 },
  ]
  const sorted = sortForDisplay(items).map((it) => it.id)
  // hit 组按缺口降序；near 组同上；watch 其次；unknown/not_set 排最后
  assert.deepEqual(sorted, [6, 3, 5, 4, 1, 2])
  // 不修改入参
  assert.deepEqual(items.map((it) => it.id), [1, 2, 3, 4, 5, 6])
})

test('sortForDisplay：非数组 / 空数组不抛错', () => {
  assert.deepEqual(sortForDisplay([]), [])
  assert.deepEqual(sortForDisplay(null), [])
  assert.deepEqual(sortForDisplay(undefined), [])
})

// ------------------------------------------------- 3. 曲线序列

test('buildTrendSeries：日期升序、null 保留为断点、不插值', () => {
  const series = buildTrendSeries([
    { trade_date: '2026-10-03', price: 10.2, ttm_yield: 5.1 },
    { trade_date: '2026-10-01', price: 10.0, ttm_yield: null },
    { trade_date: '2026-10-02', price: null, ttm_yield: null },
  ])
  assert.deepEqual(series.dates, ['2026-10-01', '2026-10-02', '2026-10-03'])
  assert.deepEqual(series.prices, [10.0, null, 10.2])
  assert.deepEqual(series.yields, [null, null, 5.1])
  // 长度始终与日期一致（不因 null 丢点）
  assert.equal(series.prices.length, series.dates.length)
  assert.equal(series.yields.length, series.dates.length)
})

test('buildTrendSeries：空数组 / 非法入参 → 空序列，不抛错', () => {
  assert.deepEqual(buildTrendSeries([]), { dates: [], prices: [], yields: [] })
  assert.deepEqual(buildTrendSeries(null), { dates: [], prices: [], yields: [] })
  assert.deepEqual(buildTrendSeries([null, undefined, {}]), {
    dates: [],
    prices: [],
    yields: [],
  })
})

// ------------------------------------------------- 4. 格式化

test('formatGapPercent：正数带 +、负数带 −、null → —、可带单位', () => {
  assert.equal(formatGapPercent(1.234), '+1.23')
  assert.equal(formatGapPercent(-0.456), '−0.46')
  assert.equal(formatGapPercent(0), '+0.00')
  assert.equal(formatGapPercent(null), '—')
  assert.equal(formatGapPercent(undefined), '—')
  assert.equal(formatGapPercent('abc'), '—')
  assert.equal(formatGapPercent(-1.5, '%'), '−1.50%')
  assert.equal(formatGapPercent(null, '%'), '—')
})

test('formatYield：5.5 → "5.50%"，null → "—"', () => {
  assert.equal(formatYield(5.5), '5.50%')
  assert.equal(formatYield('4.123'), '4.12%')
  assert.equal(formatYield(0), '0.00%')
  assert.equal(formatYield(null), '—')
  assert.equal(formatYield(undefined), '—')
  assert.equal(formatYield(NaN), '—')
})

test('formatChartTip：后端全精度值在 tooltip 里收口到 2 位小数', () => {
  // 后端故意不四舍五入（D10），这是库里真实会出现的值
  assert.equal(formatChartTip(3.762456546929316), '3.76')
  assert.equal(formatChartTip(5.612345), '5.61')
  assert.equal(formatChartTip(8.63), '8.63')
  assert.equal(formatChartTip('1.296'), '1.30')
  assert.equal(formatChartTip(0), '0.00')
  // 不适用 → 「—」，不是 0.00
  assert.equal(formatChartTip(null), '—')
  assert.equal(formatChartTip(undefined), '—')
  assert.equal(formatChartTip(''), '—')
  assert.equal(formatChartTip(NaN), '—')
})

// ------------------------------------------------- 5. 陈旧判定

test('isDataStale / staleDays：5 天 → false，6 天 → true，无快照 → false', () => {
  const today = new Date(2026, 10, 1) // 2026-11-01（本地时区，Date 分支）
  assert.equal(staleDays('2026-10-27', today), 5)
  assert.equal(isDataStale('2026-10-27', today), false)
  assert.equal(staleDays('2026-10-26', today), 6)
  assert.equal(isDataStale('2026-10-26', today), true)
  assert.equal(isDataStale('2026-11-01', today), false)
  assert.equal(isDataStale(null, today), false)
  assert.equal(staleDays(null, today), null)
  assert.equal(isDataStale('not-a-date', today), false)
})

// ------------------------------------------------- 6. 汇总

test('summarize：两类达标数、价格分母不含未设目标价、陈旧数与最新日期', () => {
  const items = [
    { yield_signal: 'hit', price_signal: 'hit', is_stale: false, snapshot_date: '2026-10-30' },
    { yield_signal: 'hit', price_signal: 'watch', is_stale: true, snapshot_date: '2026-10-20' },
    { yield_signal: 'near', price_signal: 'not_set', is_stale: false, snapshot_date: null },
    { yield_signal: 'unknown', price_signal: 'unknown', is_stale: false, snapshot_date: '2026-10-31' },
  ]
  const summary = summarize(items)
  assert.equal(summary.total, 4)
  assert.equal(summary.yieldHitCount, 2)
  assert.equal(summary.priceHitCount, 1)
  // 4 项里 1 项未设目标价 → 分母 3
  assert.equal(summary.priceTargetedCount, 3)
  assert.equal(summary.staleCount, 1)
  assert.equal(summary.latestSnapshotDate, '2026-10-31')

  const empty = summarize([])
  assert.equal(empty.total, 0)
  assert.equal(empty.latestSnapshotDate, null)
  assert.equal(summarize(null).total, 0)
})

// ------------------------------------------------- 10. 除权日对齐（图上竖线）

test('alignExDividends：只保留窗口内的除权日，按日期升序', () => {
  const dates = ['2026-07-01', '2026-07-02', '2026-07-03', '2026-09-01']
  const dividends = [
    { ex_date: '2026-09-01', dividend_per_share: 0.35 },
    { ex_date: '2026-07-02', dividend_per_share: 0.3 },
    { ex_date: '2026-01-05', dividend_per_share: 0.4 }, // 早于横轴起点 → 丢弃
  ]
  const marks = alignExDividends(dates, dividends)
  assert.deepEqual(marks, [
    { date: '2026-07-02', amount: 0.3 },
    { date: '2026-09-01', amount: 0.35 },
  ])
})

test('alignExDividends：除权日缺快照时顺延到之后最近的交易日', () => {
  // 07-04 是除权日，但那天没有快照（周末 / 停牌 / 回填缺日）
  const dates = ['2026-07-01', '2026-07-02', '2026-07-07', '2026-07-08']
  const marks = alignExDividends(dates, [{ ex_date: '2026-07-04', dividend_per_share: 0.5 }])
  assert.deepEqual(marks, [{ date: '2026-07-07', amount: 0.5 }])
})

test('alignExDividends：窗口右边界之外 / 空入参都不抛错', () => {
  const dates = ['2026-07-01', '2026-07-02']
  // 除权日晚于最后一根 K 线 → 画不出来，丢弃
  assert.deepEqual(alignExDividends(dates, [{ ex_date: '2026-08-01', dividend_per_share: 0.5 }]), [])
  // 空数组 / null / 非数组
  assert.deepEqual(alignExDividends([], [{ ex_date: '2026-07-01', dividend_per_share: 0.5 }]), [])
  assert.deepEqual(alignExDividends(null, null), [])
  assert.deepEqual(alignExDividends(dates, undefined), [])
  // 缺字段的记录直接跳过
  assert.deepEqual(alignExDividends(dates, [{ dividend_per_share: 0.5 }, null]), [])
})

test('alignExDividends：同日多条只保留一条，金额缺失记 0', () => {
  const dates = ['2026-07-01', '2026-07-02']
  const marks = alignExDividends(dates, [
    { ex_date: '2026-07-01', dividend_per_share: 0.2 },
    { ex_date: '2026-07-01', dividend_per_share: 0.9 }, // 同一横轴位置，不重复画
    { ex_date: '2026-07-02' },                          // 金额缺失 → 0
  ])
  assert.deepEqual(marks, [
    { date: '2026-07-01', amount: 0.2 },
    { date: '2026-07-02', amount: 0 },
  ])
})

// ------------------------------------------------- 9. 时间维度（全部 / 自定义区间）

test('daysBetween：正常日期、跨年、不可解析入参', () => {
  assert.equal(daysBetween('2026-01-01', '2026-01-31'), 30)
  assert.equal(daysBetween('2025-12-31', '2026-01-01'), 1)
  assert.equal(daysBetween('2026-01-31', '2026-01-01'), -30)
  assert.equal(daysBetween('2026-01-01', '2026-01-01'), 0)
  assert.equal(daysBetween(null, '2026-01-01'), null)
  assert.equal(daysBetween('not-a-date', '2026-01-01'), null)
})

test('spanDays：不足两个点给 0，多点为自然日跨度', () => {
  assert.equal(spanDays([]), 0)
  assert.equal(spanDays(['2026-01-01']), 0)
  assert.equal(spanDays(null), 0)
  assert.equal(spanDays(['2026-01-01', '2026-01-10', '2026-02-01']), 31)
})

test('summarizeRange：给出首尾日期、点数与可读文案（长区间不乱折叠）', () => {
  const dates = ['2007-05-15', '2015-06-01', '2026-10-30']
  const info = summarizeRange(dates)
  assert.equal(info.first, '2007-05-15')
  assert.equal(info.last, '2026-10-30')
  assert.equal(info.count, 3)
  assert.equal(info.label, '2007-05-15 ~ 2026-10-30 · 3 个交易日')

  // 千分位固定实现（不依赖 locale，避免不同机器输出不同）
  const many = Array.from({ length: 4715 }, (_, i) => `2026-01-0${(i % 9) + 1}`)
  assert.match(summarizeRange(many).label, /· 4,715 个交易日$/)

  assert.deepEqual(summarizeRange([]), { first: null, last: null, count: 0, days: 0, label: '' })
  assert.deepEqual(summarizeRange(null), { first: null, last: null, count: 0, days: 0, label: '' })
})

test('defaultCustomRange：默认最近 3 年，历史不足则给全部', () => {
  // 19 年历史 → 取最近 3 年
  assert.deepEqual(defaultCustomRange('2007-05-15', '2026-10-30'), {
    start: '2023-10-31',
    end: '2026-10-30',
  })
  // 只有 1 年多 → 全部
  assert.deepEqual(defaultCustomRange('2025-06-01', '2026-10-30'), {
    start: '2025-06-01',
    end: '2026-10-30',
  })
  // 不可用 → 空串，交给用户自己选
  assert.deepEqual(defaultCustomRange(null, null), { start: '', end: '' })
  assert.deepEqual(defaultCustomRange('2026-10-30', '2026-01-01'), { start: '', end: '' })
})

test('buildRangeQuery：天数 / 自定义区间 / 全部三种入参都要拼对', () => {
  // 裸数字（旧调用）与 {days} 必须等价 —— 这里曾退化过：{days:30} 被静默当成 90
  assert.equal(buildRangeQuery(30), 'days=30')
  assert.equal(buildRangeQuery({ days: 30 }), 'days=30')
  assert.equal(buildRangeQuery({ days: 180 }), 'days=180')
  assert.equal(buildRangeQuery({ all: true }), 'all=true')

  assert.equal(
    buildRangeQuery({ start: '2015-01-01', end: '2016-12-31' }),
    'start=2015-01-01&end=2016-12-31',
  )
  // 任一端可省略
  assert.equal(buildRangeQuery({ start: '2015-01-01' }), 'start=2015-01-01')
  assert.equal(buildRangeQuery({ start: '2015-01-01', end: '' }), 'start=2015-01-01')

  // 非法 / 缺省 → 回落 90 天，绝不拼出空查询串
  assert.equal(buildRangeQuery(undefined), 'days=90')
  assert.equal(buildRangeQuery({}), 'days=90')
  assert.equal(buildRangeQuery({ days: 0 }), 'days=90')
  assert.equal(buildRangeQuery({ days: 'abc' }), 'days=90')
  assert.equal(buildRangeQuery(null), 'days=90')
})

// ------------------------------------------------- 9. 表头列排序

/**
 * 4 只标的覆盖 6 个排序字段，其中「触发价 / 距触发价 / 目标价」刻意留 null。
 * 默认排序（sortForDisplay）的基准次序是 [2, 4, 1, 3]：
 *   乙 hit 最前；其余 watch 同档按 yield_gap_points 降序 → 丁 0、甲 -0.8、丙 -3.7。
 */
const SORT_ROWS = [
  {
    id: 1, name: '甲', current_yield: 4.2, target_yield: 5.0, yield_gap_points: -0.8,
    yield_trigger_price: 9.5, yield_trigger_gap_percent: -5, target_price: 8.8,
    yield_signal: 'watch', price_signal: 'watch',
  },
  {
    id: 2, name: '乙', current_yield: 6.1, target_yield: 4.5, yield_gap_points: 1.6,
    yield_trigger_price: 7.2, yield_trigger_gap_percent: 12.5, target_price: null,
    yield_signal: 'hit', price_signal: 'not_set',
  },
  {
    id: 3, name: '丙', current_yield: 2.3, target_yield: 6.0, yield_gap_points: -3.7,
    yield_trigger_price: 12.0, yield_trigger_gap_percent: 40, target_price: 6.5,
    yield_signal: 'watch', price_signal: 'watch',
  },
  {
    id: 4, name: '丁', current_yield: 5.0, target_yield: 5.0, yield_gap_points: 0,
    yield_trigger_price: null, yield_trigger_gap_percent: null, target_price: null,
    yield_signal: 'watch', price_signal: 'not_set',
  },
]

const idsOf = (rows) => rows.map((row) => row.id)

test('SORTABLE_COLUMNS：恰好是 6 个可排序字段，顺序与表头一致', () => {
  assert.deepEqual(SORTABLE_COLUMNS.map((col) => col.label), [
    '当前股息率', '目标股息率', '距目标', '触发价', '距触发价', '目标价',
  ])
  assert.equal(new Set(SORTABLE_COLUMNS.map((col) => col.key)).size, 6)
})

test('sortByColumn：6 列升降序都按数值比较（不是字符串比较）', () => {
  const cases = [
    // key,                          升序,        降序
    ['current_yield',               [3, 1, 4, 2], [2, 4, 1, 3]],
    ['target_yield',                [2, 4, 1, 3], [3, 4, 1, 2]],
    ['yield_gap_points',            [3, 1, 4, 2], [2, 4, 1, 3]],
    ['yield_trigger_price',         [2, 1, 3, 4], [3, 1, 2, 4]],
    ['yield_trigger_gap_percent',   [1, 2, 3, 4], [3, 2, 1, 4]],
    ['target_price',                [3, 1, 2, 4], [1, 3, 2, 4]],
  ]
  for (const [key, asc, desc] of cases) {
    assert.deepEqual(idsOf(sortByColumn(SORT_ROWS, key, 'asc')), asc, `${key} 升序`)
    assert.deepEqual(idsOf(sortByColumn(SORT_ROWS, key, 'desc')), desc, `${key} 降序`)
  }
})

test('sortByColumn：null / 非数字在升降序都沉底，绝不冒充 0', () => {
  const rows = [
    { id: 1, target_price: null },
    { id: 2, target_price: 3 },
    { id: 3, target_price: 9 },
    { id: 4, target_price: 'abc' },
  ]
  assert.deepEqual(idsOf(sortByColumn(rows, 'target_price', 'asc')), [2, 3, 1, 4])
  assert.deepEqual(idsOf(sortByColumn(rows, 'target_price', 'desc')), [3, 2, 1, 4])
})

test('sortByColumn：字符串数字也按数值排（后端偶发返回字符串）', () => {
  const rows = [
    { id: 1, current_yield: '9.5' },
    { id: 2, current_yield: 2.1 },
    { id: 3, current_yield: '4' },
  ]
  assert.deepEqual(idsOf(sortByColumn(rows, 'current_yield', 'asc')), [2, 3, 1])
})

test('sortByColumn：同值保持默认「已达标优先」的次序，不退回按 id', () => {
  const tied = [
    {
      id: 1, current_yield: 5, yield_gap_points: -1,
      yield_signal: 'watch', price_signal: 'watch',
    },
    {
      id: 2, current_yield: 5, yield_gap_points: -2,
      yield_signal: 'hit', price_signal: 'hit',
    },
  ]
  assert.deepEqual(idsOf(sortByColumn(tied, 'current_yield', 'asc')), [2, 1])
  assert.deepEqual(idsOf(sortByColumn(tied, 'current_yield', 'desc')), [2, 1])
})

test('sortByColumn：返回新数组，不修改入参', () => {
  const rows = [
    { id: 1, current_yield: 8, yield_signal: 'watch', price_signal: 'watch' },
    { id: 2, current_yield: 1, yield_signal: 'watch', price_signal: 'watch' },
  ]
  const out = sortByColumn(rows, 'current_yield', 'asc')
  assert.notStrictEqual(out, rows)
  assert.deepEqual(idsOf(rows), [1, 2])
  assert.deepEqual(idsOf(out), [2, 1])
})

test('sortByColumn：未知列 / 非数组安全退化到默认排序', () => {
  assert.deepEqual(
    idsOf(sortByColumn(SORT_ROWS, 'name', 'asc')),
    idsOf(sortForDisplay(SORT_ROWS)),
  )
  assert.deepEqual(idsOf(sortByColumn(SORT_ROWS, '', 'asc')), idsOf(sortForDisplay(SORT_ROWS)))
  assert.deepEqual(sortByColumn(null, 'current_yield'), [])
  assert.deepEqual(sortByColumn(undefined, 'current_yield'), [])
  assert.deepEqual(sortByColumn('nope', 'current_yield'), [])
})
