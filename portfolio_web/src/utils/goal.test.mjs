/**
 * 年收入目标展示纯函数的单测（node --test）。
 *
 * 锁定的口径：
 *   - 月均 = 年 ÷ 12，非有限数一律 0（不产生 NaN 上屏）
 *   - 进度条宽度 clamp 到 [0, 100]（超额封顶，避免条溢出色块）
 *   - 收益率 2 位小数 + %，无意义值显示「—」而不是 0.00%
 *   - localStorage 记忆「每个组合上次选中的目标」，任何异常都必须静默降级
 *
 * 环境注意：node --test 下不保证存在 globalThis.localStorage，
 * 因此这里全部用注入的假 storage，并额外验证 storage 缺失/抛异常/数据损坏时的降级。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  GOAL_STORAGE_KEY,
  toMonthly,
  progressPercentWidth,
  formatYieldPercent,
  pickInitialGoalId,
  readRememberedGoalId,
  persistGoalId,
} from './goal.js'

/** Map 实现的假 localStorage（node --test 环境没有真 storage） */
function createFakeStorage() {
  const map = new Map()
  return {
    map,
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => { map.set(key, String(value)) },
    removeItem: (key) => { map.delete(key) },
  }
}

// ---------------------------------------------------------------- toMonthly

test('toMonthly：年 ÷ 12', () => {
  assert.equal(toMonthly(1200), 100)
  assert.equal(toMonthly(120000), 10000)
  assert.equal(toMonthly(0), 0)
  assert.equal(toMonthly(144), 12)
})

test('toMonthly：null / undefined / NaN / 非数字字符串 → 0（不上屏 NaN）', () => {
  assert.equal(toMonthly(null), 0)
  assert.equal(toMonthly(undefined), 0)
  assert.equal(toMonthly(NaN), 0)
  assert.equal(toMonthly('abc'), 0)
  assert.equal(toMonthly(Infinity), 0)
})

test('toMonthly：数字字符串按数字处理（后端/输入框可能给字符串）', () => {
  assert.equal(toMonthly('1200'), 100)
  assert.equal(toMonthly('120'), 10)
})

// ------------------------------------------------------- progressPercentWidth

test('progressPercentWidth：区间内原值返回', () => {
  assert.equal(progressPercentWidth(72.35), 72.35)
  assert.equal(progressPercentWidth(0), 0)
  assert.equal(progressPercentWidth(100), 100)
  assert.equal(progressPercentWidth(0.5), 0.5)
})

test('progressPercentWidth：clamp 到 [0, 100]（负数归 0，超额封顶 100）', () => {
  assert.equal(progressPercentWidth(-5), 0)
  assert.equal(progressPercentWidth(-0.01), 0)
  assert.equal(progressPercentWidth(120), 100)
  assert.equal(progressPercentWidth(100.01), 100)
})

test('progressPercentWidth：null / undefined / NaN → 0', () => {
  assert.equal(progressPercentWidth(null), 0)
  assert.equal(progressPercentWidth(undefined), 0)
  assert.equal(progressPercentWidth(NaN), 0)
  assert.equal(progressPercentWidth('abc'), 0)
})

// -------------------------------------------------------- formatYieldPercent

test('formatYieldPercent：2 位小数 + %', () => {
  assert.equal(formatYieldPercent(4.5), '4.50%')
  assert.equal(formatYieldPercent(0), '0.00%')
  assert.equal(formatYieldPercent(12), '12.00%')
  assert.equal(formatYieldPercent(3.14159), '3.14%')
})

test('formatYieldPercent：null / undefined / 非有限数 → 「—」', () => {
  assert.equal(formatYieldPercent(null), '—')
  assert.equal(formatYieldPercent(undefined), '—')
  assert.equal(formatYieldPercent(NaN), '—')
  assert.equal(formatYieldPercent('abc'), '—')
})

// -------------------------------------------------------- pickInitialGoalId

const GOALS = [
  { id: 7, name: '加菜线' },
  { id: 9, name: '生活自由线' },
]

test('pickInitialGoalId：记忆值命中列表 → 用它', () => {
  assert.equal(pickInitialGoalId(GOALS, 9), 9)
  assert.equal(pickInitialGoalId(GOALS, '7'), 7) // 宽松比较，兼容字符串 id
})

test('pickInitialGoalId：记忆值失效（被删 / 不属于本组合）→ 回退首个', () => {
  assert.equal(pickInitialGoalId(GOALS, 999), 7)
  assert.equal(pickInitialGoalId(GOALS, null), 7)
  assert.equal(pickInitialGoalId(GOALS, undefined), 7)
})

test('pickInitialGoalId：空列表 / 非法入参 → null', () => {
  assert.equal(pickInitialGoalId([], 9), null)
  assert.equal(pickInitialGoalId(undefined, 9), null)
  assert.equal(pickInitialGoalId(null, 9), null)
})

// ------------------------------------------- readRememberedGoalId / persistGoalId

test('persistGoalId / readRememberedGoalId：写入后能读回，并按组合隔离', () => {
  const storage = createFakeStorage()

  persistGoalId(1, 42, storage)
  assert.equal(readRememberedGoalId(1, storage), 42)
  // 另一个组合没有记忆值
  assert.equal(readRememberedGoalId(2, storage), null)

  persistGoalId(2, 77, storage)
  assert.equal(readRememberedGoalId(1, storage), 42)
  assert.equal(readRememberedGoalId(2, storage), 77)

  // 同一组合覆盖写
  persistGoalId(1, 43, storage)
  assert.equal(readRememberedGoalId(1, storage), 43)

  // value 是 {portfolioId: goalId} 的 JSON 对象
  assert.deepEqual(JSON.parse(storage.getItem(GOAL_STORAGE_KEY)), { 1: 43, 2: 77 })
})

test('persistGoalId：goalId 为 null 时删除该组合的键（而不是写 null）', () => {
  const storage = createFakeStorage()
  persistGoalId(1, 42, storage)
  persistGoalId(2, 77, storage)

  persistGoalId(1, null, storage)

  assert.equal(readRememberedGoalId(1, storage), null)
  assert.equal(readRememberedGoalId(2, storage), 77) // 其他组合不受影响
  assert.deepEqual(JSON.parse(storage.getItem(GOAL_STORAGE_KEY)), { 2: 77 })
})

test('storage 降级：storage 为 undefined / null 时不抛异常，读回 null', () => {
  assert.equal(readRememberedGoalId(1, undefined), null)
  assert.equal(readRememberedGoalId(1, null), null)
  assert.doesNotThrow(() => persistGoalId(1, 42, undefined))
  assert.doesNotThrow(() => persistGoalId(1, 42, null))
})

test('storage 降级：getItem/setItem 抛异常（隐私模式）时读写都不抛', () => {
  const throwing = {
    getItem() { throw new Error('SecurityError') },
    setItem() { throw new Error('QuotaExceededError') },
  }
  assert.equal(readRememberedGoalId(1, throwing), null)
  assert.doesNotThrow(() => persistGoalId(1, 42, throwing))
  assert.doesNotThrow(() => persistGoalId(1, null, throwing))
})

test('storage 降级：存了非法 JSON → 读返回 null，写不抛且能从空对象重建', () => {
  const storage = createFakeStorage()
  storage.setItem(GOAL_STORAGE_KEY, '{ 这不是 JSON')

  assert.equal(readRememberedGoalId(1, storage), null)
  assert.doesNotThrow(() => persistGoalId(1, 42, storage))
  // 损坏数据被重置，新值可正常读回
  assert.equal(readRememberedGoalId(1, storage), 42)
})

test('storage 降级：JSON 合法但不是对象（数组 / 数字）→ 读返回 null', () => {
  const storage = createFakeStorage()
  storage.setItem(GOAL_STORAGE_KEY, '[1,2]')
  assert.equal(readRememberedGoalId(1, storage), null)

  storage.setItem(GOAL_STORAGE_KEY, '123')
  assert.equal(readRememberedGoalId(1, storage), null)
})

test('readRememberedGoalId：不传 storage 时默认取 globalThis.localStorage（缺失也不抛）', () => {
  // node --test 环境通常没有 localStorage；即使有也不应抛异常
  assert.doesNotThrow(() => readRememberedGoalId(1))
  assert.doesNotThrow(() => persistGoalId(1, 42))
})
