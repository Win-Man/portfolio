import { createRouter, createWebHashHistory } from 'vue-router'

import Portfolio from './views/Portfolio.vue'
import CalendarView from './views/CalendarView.vue'
import WatchlistView from './views/WatchlistView.vue'
import AdminView from './views/AdminView.vue'
import ReportsView from './views/ReportsView.vue'
import { ensurePortfoliosLoaded, selectedPortfolioId } from './composables/usePortfolioContext.js'

const routes = [
  { path: '/', name: 'portfolio', component: Portfolio },
  { path: '/calendar', name: 'calendar', component: CalendarView },
  // 观察池是全局的（不绑定组合），因此守卫里不为它做「组合是否已选」的兜底
  { path: '/watchlist', name: 'watchlist', component: WatchlistView },
  // 后台管理同样与组合无关：定时任务是全局的
  { path: '/admin', name: 'admin', component: AdminView },
  // 日报：组合日报按组合存，观察池日报全局一份；切换类型在页面内完成
  { path: '/reports', name: 'reports', component: ReportsView },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

/**
 * 直接打开 `#/calendar` / `#/reports` 时组合可能还没选定（或组合列表为空）：
 * 先确保组合列表加载过一次，仍然没有可用组合就退回投资组合页（spec §8-20）。
 *
 * `#/reports` 里的组合日报是按组合存的，没有选中组合就只能看到空列表，
 * 所以同样要先确认「有组合可用」。
 */
router.beforeEach(async (to) => {
  if (to.name !== 'calendar' && to.name !== 'reports') return true
  await ensurePortfoliosLoaded()
  if (selectedPortfolioId.value === null || selectedPortfolioId.value === undefined) {
    return { name: 'portfolio' }
  }
  return true
})

export default router
