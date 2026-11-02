/**
 * 成本换算单测（node --test）。
 * 场景全部取自真实账户：513530（港股通红利ETF华泰柏瑞，7500 份）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  COST_AMOUNT_DECIMALS,
  COST_PRICE_DECIMALS,
  costAmountFromPrice,
  costPriceFromAmount,
  roundTo,
} from './cost.js'

test('roundTo：按小数位四舍五入', () => {
  assert.equal(roundTo(1.52113749, 6), 1.521137)
  assert.equal(roundTo(11408.5275, 2), 11408.53)
  assert.equal(roundTo(-0.004, 2), -0)
})

test('常量：每股 6 位、总额 2 位', () => {
  assert.equal(COST_PRICE_DECIMALS, 6)
  assert.equal(COST_AMOUNT_DECIMALS, 2)
})

test('成本总额 → 每股成本：还原券商的 1.521137', () => {
  // 华泰：市值 11422.50，总盈亏 +13.97 → 总成本 11408.53
  assert.equal(costPriceFromAmount(11408.53, 7500), 1.521137)
})

test('每股成本 → 成本总额：反算回 11408.53', () => {
  assert.equal(costAmountFromPrice(1.521137, 7500), 11408.53)
})

test('往返一致：总额 → 每股 → 总额 不失真', () => {
  for (const amount of [11408.53, 10000, 2500.5, 1, 999999.99]) {
    const per = costPriceFromAmount(amount, 7500)
    assert.equal(costAmountFromPrice(per, 7500), amount, `amount=${amount}`)
  }
})

test('负成本（摊薄后）同样能换算', () => {
  assert.equal(costPriceFromAmount(-325, 100), -3.25)
  assert.equal(costAmountFromPrice(-3.25, 100), -325)
})

test('无效输入 → null（0 份不能除）', () => {
  assert.equal(costPriceFromAmount(11408.53, 0), null)
  assert.equal(costPriceFromAmount(11408.53, -1), null)
  assert.equal(costPriceFromAmount('', 7500), null)
  assert.equal(costPriceFromAmount('abc', 7500), null)
  assert.equal(costAmountFromPrice(null, 7500), null)
  assert.equal(costAmountFromPrice(1.5, 0), null)
  assert.equal(costAmountFromPrice(1.5, undefined), null)
})

test('成本为 0 是合法值（已全部回本），不是「未填」', () => {
  assert.equal(costAmountFromPrice(0, 7500), 0)
  assert.equal(costPriceFromAmount(0, 7500), 0)
})
