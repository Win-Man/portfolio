import test from 'node:test'
import assert from 'node:assert/strict'

import {
  plainAmount,
  plainPercent,
  plainQuantity,
  pnlTone,
  priceSignalMeta,
  priceSourceMeta,
  reportHeadline,
  reportTypeMeta,
  signalCount,
  signedAmount,
  signedPercent,
  signedPrice,
  sortItemsByPnl,
  yieldSignalMeta,
} from './report.js'

test('signedAmount：正负号、千分位、0 与缺失都给「—」', () => {
  assert.equal(signedAmount(1234.5), '+¥1,234.50')
  assert.equal(signedAmount(-1234.5), '−¥1,234.50')
  // 0 在日报里几乎总是「没数据」，显示 0.00 反而像算错了
  assert.equal(signedAmount(0), '—')
  assert.equal(signedAmount(null), '—')
  assert.equal(signedAmount(undefined), '—')
  assert.equal(signedAmount('abc'), '—')
})

test('signedAmount：四舍五入到 0 的负数不给「−¥0.00」', () => {
  // ETF 每股跌 0.003 元 → 金额层面就是 0.00，显示「−¥0.00」比「—」更让人困惑
  assert.equal(signedAmount(-0.003), '—')
  assert.equal(signedAmount(-0.004), '—')
  assert.equal(signedAmount(-0.005), '−¥0.01')
})

test('signedPrice：按标的类型给 2 / 3 位小数', () => {
  // ETF：0.003 元是真实波动，不能被抹成 0.00
  assert.equal(signedPrice(0.003, 'ETF'), '+0.003')
  assert.equal(signedPrice(-0.003, 'ETF'), '−0.003')
  assert.equal(signedPrice(0.49, '股票'), '+0.49')
  assert.equal(signedPrice(-0.49, '股票'), '−0.49')
  // 类型缺失：退回 2 位
  assert.equal(signedPrice(0.5), '+0.50')
  // 该精度下等于 0 → 「—」
  assert.equal(signedPrice(0.0004, 'ETF'), '—')
  assert.equal(signedPrice(null, 'ETF'), '—')
})

test('signedPercent：正负号与位数', () => {
  assert.equal(signedPercent(1.234), '+1.23%')
  assert.equal(signedPercent(-1.234), '−1.23%')
  assert.equal(signedPercent(0), '0.00%')
  assert.equal(signedPercent(1.234, 1), '+1.2%')
  assert.equal(signedPercent(null), '—')
})

test('plainPercent / plainAmount / plainQuantity', () => {
  assert.equal(plainPercent(4.8213), '4.82%')
  assert.equal(plainPercent(4.8213, 1), '4.8%')
  assert.equal(plainPercent(null), '—')
  assert.equal(plainAmount(11422.5), '11,422.50')
  assert.equal(plainAmount(0), '0.00')
  assert.equal(plainAmount(null), '—')
  assert.equal(plainQuantity(12500), '12,500')
  assert.equal(plainQuantity(0.5), '0.5')
  assert.equal(plainQuantity(null), '—')
})

test('pnlTone：红涨绿跌的三态', () => {
  assert.equal(pnlTone(1), 'is-up')
  assert.equal(pnlTone(-1), 'is-down')
  assert.equal(pnlTone(0), 'is-flat')
  assert.equal(pnlTone(null), 'is-flat')
})

test('信号文案：达标 / 接近 / 观察 / 未设 / 无数据', () => {
  assert.equal(yieldSignalMeta('hit').label, '达标')
  assert.equal(yieldSignalMeta('near').label, '接近')
  assert.equal(yieldSignalMeta('watch').label, '观察')
  assert.equal(yieldSignalMeta('unknown').label, '无数据')
  // 未知入参也退化到「无数据」，不抛错、不显示原始英文
  assert.equal(yieldSignalMeta('whatever').label, '无数据')

  assert.equal(priceSignalMeta('hit').label, '达标')
  // 没设目标价 ≠ 数据不足，文案要区分
  assert.equal(priceSignalMeta('not_set').label, '未设')
  assert.equal(priceSignalMeta('watch').label, '等待')
})

test('priceSourceMeta：三种价格来源', () => {
  assert.deepEqual(priceSourceMeta('real').label, '当日行情')
  assert.equal(priceSourceMeta('real').tone, 'ok')
  assert.equal(priceSourceMeta('cached').label, '缓存价')
  assert.equal(priceSourceMeta('mock').label, '模拟价')
  // 未知值按「缓存价」处理：宁可疑，不可当作真实行情
  assert.equal(priceSourceMeta(undefined).tone, 'warn')
})

test('reportTypeMeta：两种日报的标题与空态文案', () => {
  assert.equal(reportTypeMeta('portfolio').label, '组合日报')
  assert.equal(reportTypeMeta('watchlist').label, '观察池日报')
  assert.match(reportTypeMeta('watchlist').emptyHint, /观察池/)
  assert.equal(reportTypeMeta(undefined).key, 'portfolio')
})

test('reportHeadline：组合按涨跌家数，观察池按达标数', () => {
  assert.equal(
    reportHeadline({
      type: 'portfolio',
      summary: { asset_count: 3 },
      items: [{ change_percent: 1 }, { change_percent: -2 }, { change_percent: 0 }],
    }),
    '3 只标的 · 涨 1 跌 1'
  )

  assert.equal(
    reportHeadline({
      type: 'watchlist',
      summary: { item_count: 12, yield_hit_count: 2, price_hit_count: 1 },
    }),
    '12 只标的 · 达标 3'
  )

  // 没有任何标的时不要拼出「0 只标的 · 涨 0 跌 0」
  assert.equal(reportHeadline({ type: 'portfolio', summary: {}, items: [] }), '')
  assert.equal(reportHeadline(null), '')
})

test('signalCount：四类信号相加', () => {
  assert.equal(
    signalCount({
      summary: { yield_hit_count: 2, price_hit_count: 1, yield_near_count: 1, price_near_count: 0 },
    }),
    4
  )
  assert.equal(signalCount({}), 0)
  assert.equal(signalCount(null), 0)
})

test('sortItemsByPnl：金额降序，缺数据的排最后', () => {
  const items = [
    { code: 'C', day_pnl: null },
    { code: 'A', day_pnl: 100 },
    { code: 'B', day_pnl: -50 },
  ]
  assert.deepEqual(sortItemsByPnl(items).map((i) => i.code), ['A', 'B', 'C'])
  // 不改入参数组
  assert.equal(items[0].code, 'C')
  assert.deepEqual(sortItemsByPnl(null), [])
})
