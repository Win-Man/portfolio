/**
 * 组合上下文（全局单例）—— 「投资组合」与「收入日历」两个视图共享选中组合。
 *
 * 为什么是模块级单例 + inject 访问器（与 useToast.js 同款）：
 *   - 模块级 ref 保证**两个视图、router 守卫看到的是同一份状态**；
 *   - `providePortfolioContext()` / `usePortfolioContext()` 让组件仍然走 provide/inject
 *     （spec §7.2），且不引入 pinia 这类新依赖；
 *   - 全应用**只有这里**调用 `getPortfolios()`。
 *
 * localStorage（key `portfolio.ui.selectedPortfolioId.v1`）：
 *   - 读写都包 try/catch，隐私模式 / 存储被禁用时静默降级为「不记忆」；
 *   - 读到的 id 只是**候选偏好**，必须能在后端返回的组合列表里命中才会被采用，
 *     否则回落到当前选中项，再回落到第一个（spec §8-26）。
 */
import { inject, provide, ref } from 'vue'

import { getPortfolios } from '../api/portfolio.js'
import { toast } from './useToast.js'

export const PORTFOLIO_CONTEXT_KEY = Symbol('portfolio-context')
export const SELECTED_PORTFOLIO_STORAGE_KEY = 'portfolio.ui.selectedPortfolioId.v1'

/** 安全地取默认 storage：globalThis.localStorage 可能是 undefined，也可能被策略拦到抛异常 */
function defaultStorage() {
  try {
    return globalThis.localStorage
  } catch {
    return undefined
  }
}

/**
 * 读取「上次选中的组合 id」。任何异常（无 storage / 抛异常 / 值非法）→ null。
 * @param {{getItem?:Function}|undefined|null} [storage] 默认 globalThis.localStorage
 * @returns {number|null}
 */
export function readSelectedPortfolioId(storage = defaultStorage()) {
  try {
    if (!storage || typeof storage.getItem !== 'function') return null
    const raw = storage.getItem(SELECTED_PORTFOLIO_STORAGE_KEY)
    if (raw === null || raw === undefined || raw === '') return null
    const id = Number(raw)
    return Number.isFinite(id) ? id : null
  } catch {
    return null
  }
}

/**
 * 记住选中的组合 id。`id === null` 表示清除记忆（不是写 null）。任何异常静默忽略。
 * @param {number|null} id
 * @param {{setItem?:Function, removeItem?:Function}|undefined|null} [storage]
 * @returns {void}
 */
export function persistSelectedPortfolioId(id, storage = defaultStorage()) {
  try {
    if (!storage) return
    if (id === null || id === undefined || id === '') {
      if (typeof storage.removeItem === 'function') storage.removeItem(SELECTED_PORTFOLIO_STORAGE_KEY)
      return
    }
    if (typeof storage.setItem !== 'function') return
    storage.setItem(SELECTED_PORTFOLIO_STORAGE_KEY, String(id))
  } catch {
    /* 存储不可用（隐私模式等）：静默降级为「不记忆」 */
  }
}

// ---------- 模块级单例状态 ----------
// 直接导出这三个 ref：router.js 的守卫需要在组件之外读 selectedPortfolioId。

export const portfolios = ref([])
export const selectedPortfolioId = ref(null)
export const loadingPortfolios = ref(false)

/** 并发去重：router 守卫与 App.vue 可能同时想加载组合列表 */
let inflight = null

/**
 * 选中组合并写回 localStorage。
 * 只改状态，**不负责拉数据** —— 数据由各视图 watch(selectedPortfolioId) 自行加载。
 * @param {number|null} id
 */
export function selectPortfolio(id) {
  const next = id === null || id === undefined || id === '' ? null : id
  selectedPortfolioId.value = next
  persistSelectedPortfolioId(next)
}

/**
 * 局部更新组合列表（重命名后不重拉列表，避免看板闪动）。
 * 不存在则追加。
 * @param {object} portfolio
 */
export function upsertPortfolioLocal(portfolio) {
  if (!portfolio || portfolio.id === null || portfolio.id === undefined) return
  const index = portfolios.value.findIndex((p) => p.id === portfolio.id)
  if (index === -1) {
    portfolios.value = [...portfolios.value, portfolio]
    return
  }
  portfolios.value = portfolios.value.map((p, i) => (i === index ? { ...p, ...portfolio } : p))
}

/**
 * 拉组合列表并选中一个组合（全应用唯一的 getPortfolios 调用点）。
 *
 * @param {number|null} preferredId 首选选中的组合（新增后指向新组合 / 删除后指向邻居）；
 *        不在列表里或未传时，依次尝试：当前选中项 → localStorage 记忆值 → 第一个。
 * @returns {Promise<void>}
 */
export async function reloadPortfolios(preferredId = null) {
  if (inflight) {
    // 已有请求在飞：等它结束，若本次带 preferredId 再补一次选中
    await inflight
    if (preferredId !== null && preferredId !== undefined) {
      if (portfolios.value.some((p) => p.id === preferredId)) selectPortfolio(preferredId)
    }
    return
  }

  loadingPortfolios.value = true
  inflight = (async () => {
    try {
      const data = await getPortfolios()
      portfolios.value = Array.isArray(data) ? data : []
    } catch (e) {
      console.error('加载组合列表失败:', e)
      portfolios.value = []
      toast.error('组合列表加载失败，请稍后重试')
    }

    const ids = portfolios.value.map((p) => p.id)
    if (ids.length === 0) {
      selectPortfolio(null)
      return
    }

    const remembered = readSelectedPortfolioId()
    const next = ids.includes(preferredId)
      ? preferredId
      : ids.includes(selectedPortfolioId.value)
        ? selectedPortfolioId.value
        : ids.includes(remembered)
          ? remembered
          : ids[0]
    selectPortfolio(next)
  })()

  try {
    await inflight
  } finally {
    inflight = null
    loadingPortfolios.value = false
  }
}

/** 组合列表还没加载过时加载一次（router 守卫用：直接进 /calendar 时也要能判定有没有组合） */
export async function ensurePortfoliosLoaded() {
  if (portfolios.value.length > 0) return
  await reloadPortfolios()
}

const context = {
  portfolios,
  selectedPortfolioId,
  loadingPortfolios,
  reloadPortfolios,
  selectPortfolio,
  upsertPortfolioLocal,
  ensurePortfoliosLoaded,
}

/** 在 App.vue 的 setup() 里调用，把共享状态 provide 给两个视图 */
export function providePortfolioContext() {
  provide(PORTFOLIO_CONTEXT_KEY, context)
  return context
}

/** 组件里取共享状态（provide/inject；未 provide 时回落到模块单例，单测/独立挂载也安全） */
export function usePortfolioContext() {
  return inject(PORTFOLIO_CONTEXT_KEY, context)
}
