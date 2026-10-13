/**
 * 年收入目标的**展示用**纯函数（D10：所有口径计算在后端，前端只做展示换算）。
 *
 * 这里只有四类事情：
 *   1. 月均换算（年 ÷ 12）
 *   2. 进度条宽度（clamp 到 [0, 100]，超额封顶）
 *   3. 百分数格式化（2 位小数 + %，无意义值给「—」）
 *   4. 「上次选中的目标」的 localStorage 读写
 *
 * storage 约定（重要）：
 *   - 读写函数的第三个参数默认在**调用时**才去取 `globalThis.localStorage`，
 *     因此 node --test 里可以注入假 storage，浏览器隐私模式下也不会崩。
 *   - 所有访问都包在 try/catch 里，失败静默降级：读返回 null，写直接忽略，绝不抛出。
 */

export const GOAL_STORAGE_KEY = 'portfolio.goals.selectedId.v1'

/** 安全地取默认 storage：globalThis.localStorage 可能是 undefined，也可能被策略拦到抛异常 */
function defaultStorage() {
  try {
    return globalThis.localStorage
  } catch {
    return undefined
  }
}

/**
 * 年 → 月：年 ÷ 12。非有限数（null / undefined / NaN / 非数字字符串）统一返回 0。
 * @param {number|string|null|undefined} annual
 * @returns {number}
 */
export function toMonthly(annual) {
  const v = Number(annual)
  if (!Number.isFinite(v)) return 0
  return v / 12
}

/**
 * 进度条宽度百分比：clamp 到 [0, 100]。
 * 超额（如 120%）时封顶 100%（进度条画不出更长），但「完成度」数字仍保留原值。
 * @param {number|string|null|undefined} percent
 * @returns {number}
 */
export function progressPercentWidth(percent) {
  const v = Number(percent)
  if (!Number.isFinite(v)) return 0
  if (v < 0) return 0
  if (v > 100) return 100
  return v
}

/**
 * 收益率格式化为「4.50%」。null / undefined / 非有限数 → 「—」（无意义，不要显示 0.00%）。
 * @param {number|string|null|undefined} v
 * @returns {string}
 */
export function formatYieldPercent(v) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return `${n.toFixed(2)}%`
}

/**
 * 首屏选中哪个目标：
 *   记忆值仍在列表里 → 用它；否则回退到第一个目标；空列表 / 非法入参 → null。
 * @param {Array<{id:number}>|undefined|null} goals
 * @param {number|null|undefined} rememberedId
 * @returns {number|null}
 */
export function pickInitialGoalId(goals, rememberedId) {
  if (!Array.isArray(goals) || goals.length === 0) return null
  if (rememberedId !== null && rememberedId !== undefined) {
    // 宽松比较：localStorage 里存的是 JSON 数字，但旧数据/手改值可能是字符串
    const hit = goals.find((g) => g && String(g.id) === String(rememberedId))
    if (hit) return hit.id
  }
  return goals[0].id
}

/**
 * 读取该组合「上次选中的目标 id」。任何异常（无 storage / 抛异常 / JSON 损坏）→ null。
 * @param {number|string} portfolioId
 * @param {{getItem:Function}|undefined|null} [storage] 默认 globalThis.localStorage
 * @returns {number|null}
 */
export function readRememberedGoalId(portfolioId, storage = defaultStorage()) {
  try {
    if (portfolioId === null || portfolioId === undefined) return null
    if (!storage || typeof storage.getItem !== 'function') return null
    const raw = storage.getItem(GOAL_STORAGE_KEY)
    if (!raw) return null
    const map = JSON.parse(raw)
    if (!map || typeof map !== 'object' || Array.isArray(map)) return null
    const value = map[String(portfolioId)]
    if (value === null || value === undefined) return null
    const id = Number(value)
    return Number.isFinite(id) ? id : null
  } catch {
    return null
  }
}

/**
 * 记住该组合「上次选中的目标 id」。
 * `goalId === null` 表示删除该组合的记忆项（不是写 null），用于列表空了 / 目标被删。
 * 任何异常静默忽略。
 * @param {number|string} portfolioId
 * @param {number|null} goalId
 * @param {{getItem:Function,setItem:Function}|undefined|null} [storage] 默认 globalThis.localStorage
 * @returns {void}
 */
export function persistGoalId(portfolioId, goalId, storage = defaultStorage()) {
  try {
    if (portfolioId === null || portfolioId === undefined) return
    if (!storage || typeof storage.getItem !== 'function' || typeof storage.setItem !== 'function') return

    // 读出整张 mapping（按组合存），损坏时从空对象重建，避免整块记忆丢失
    let map = {}
    try {
      const parsed = JSON.parse(storage.getItem(GOAL_STORAGE_KEY) || '{}')
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) map = parsed
    } catch {
      map = {}
    }

    const key = String(portfolioId)
    if (goalId === null || goalId === undefined) {
      delete map[key]
    } else {
      map[key] = goalId
    }
    storage.setItem(GOAL_STORAGE_KEY, JSON.stringify(map))
  } catch {
    /* 存储不可用（隐私模式等）：静默降级为「不记忆」 */
  }
}
