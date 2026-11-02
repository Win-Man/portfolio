import { createRouter, createWebHashHistory } from 'vue-router'

import Portfolio from './views/Portfolio.vue'
import CalendarView from './views/CalendarView.vue'
import WatchlistView from './views/WatchlistView.vue'
import { ensurePortfoliosLoaded, selectedPortfolioId } from './composables/usePortfolioContext.js'

const routes = [
  { path: '/', name: 'portfolio', component: Portfolio },
  { path: '/calendar', name: 'calendar', component: CalendarView },
  // 观察池是全局的（不绑定组合），因此守卫里不为它做「组合是否已选」的兜底
  { path: '/watchlist', name: 'watchlist', component: WatchlistView },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

/**
 * 直接打开 `#/calendar` 时组合可能还没选定（或组合列表为空）：
 * 先确保组合列表加载过一次，仍然没有可用组合就退回投资组合页（spec §8-20）。
 */
router.beforeEach(async (to) => {
  if (to.name !== 'calendar') return true
  await ensurePortfoliosLoaded()
  if (selectedPortfolioId.value === null || selectedPortfolioId.value === undefined) {
    return { name: 'portfolio' }
  }
  return true
})

export default router
